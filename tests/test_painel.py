import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from osu_tracker import analise, config, db, painel

from test_mapas import MAPA, score

AGORA = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


def mapa(beatmap_id, notas=200, estrelas=6.2):
    return {**MAPA, "id": beatmap_id, "checksum": f"c{beatmap_id}", "difficulty_rating": estrelas,
            "count_circles": notas, "count_sliders": 0, "count_spinners": 0}


def stats(great, miss, ok=0, meh=0):
    return {"great": great, "ok": ok, "meh": meh, "miss": miss}


def atributos(con, beatmap_id, estrelas, aim, speed, mods=""):
    con.execute(
        "INSERT OR REPLACE INTO mapas_mods (beatmap_id, ruleset_id, mods_chave, mods_json, taxa, taxa_api, "
        "star_rating, aim_difficulty, speed_difficulty) VALUES (?, 0, ?, '[]', 1, 1, ?, ?, ?)",
        (beatmap_id, mods, estrelas, aim, speed),
    )


class Banco(unittest.TestCase):
    def setUp(self):
        self.con = db.conectar(Path(":memory:"))
        self.addCleanup(self.con.close)

    def gravar(self, *scores):
        with self.con:
            db.gravar_jogadas(self.con, list(scores))


class TestContas(Banco):
    def test_misses_e_acc_somam_antes_de_dividir(self):
        # 300 notas jogadas no total: 10 misses -> 3,33 por 100 notas, mesmo com tentativas de tamanhos diferentes.
        self.gravar(
            score(1, beatmap=mapa(1, notas=400), stats=stats(great=40, miss=10)),
            score(2, beatmap=mapa(1, notas=400), stats=stats(great=240, ok=10, miss=0), ended_at="2026-11-22T18:00:00Z"),
        )
        r = analise.painel(self.con)["resumo"]
        self.assertEqual((r["jogadas"], r["notas"], r["misses"], r["misses_100"]), (2, 300, 10, 3.33))
        self.assertEqual(r["acc"], round((300 * 280 + 100 * 10) / (300 * 300), 4))
        self.assertEqual(r["acc_acertadas"], round((300 * 280 + 100 * 10) / (300 * 290), 4))
        self.assertEqual(r["fracao_media"], round((50 / 400 + 250 / 400) / 2, 4))

    def test_acc_bate_com_a_do_jogo(self):
        # Jogada real: 100 x 300, 3 x 100, 6 misses -> 92,66% no osu!stable.
        self.gravar(score(1, stats=stats(great=100, ok=3, miss=6)))
        self.assertEqual(analise.painel(self.con)["resumo"]["acc"], 0.9266)

    def test_banco_vazio(self):
        d = analise.painel(self.con)
        self.assertEqual(d["resumo"]["jogadas"], 0)
        self.assertIsNone(d["resumo"]["misses_100"])
        for secao in ("por_dia", "por_mapa", "por_faixa", "acc", "sugestoes"):
            self.assertEqual(d[secao], [], secao)
        self.assertEqual(d["aim_speed"]["aim"]["jogadas"], 0)


class TestFiltros(Banco):
    def setUp(self):
        super().setUp()
        self.gravar(
            score(1, beatmap=mapa(1), ended_at="2026-11-01T12:00:00Z"),
            score(2, beatmap=mapa(1), mods=["HR"], ended_at="2026-11-22T12:00:00Z"),
            score(3, beatmap=mapa(2), mods=["HD", "NF"], ended_at="2026-11-23T12:00:00Z"),
            score(4, beatmap=mapa(2), ruleset_id=1, ended_at="2026-11-23T12:00:00Z"),  # taiko fica de fora
        )

    def ids(self, **filtros):
        return [j.score_id for j in analise.carregar(self.con, agora=AGORA, **filtros)]

    def test_periodo(self):
        self.assertEqual(self.ids(), [1, 2, 3])
        self.assertEqual(self.ids(dias=7), [2, 3])
        self.assertEqual(self.ids(dias=1), [3])

    def test_mods(self):
        self.assertEqual(self.ids(mods=""), [1])  # sem mods
        self.assertEqual(self.ids(mods="HR"), [2])
        self.assertEqual(self.ids(mods="HD"), [3])  # NF nao muda a dificuldade
        self.assertEqual(self.ids(mods="HR", dias=1), [])

    def test_opcoes_de_mods(self):
        self.assertEqual([(o["valor"], o["nome"], o["jogadas"]) for o in analise.opcoes_mods(self.con)],
                         [("", "Sem mods", 1), ("HD", "HD", 1), ("HR", "HR", 1)])


class TestSecoes(Banco):
    def test_por_mapa_so_com_mais_de_uma_tentativa_e_tendencia(self):
        self.gravar(
            score(1, beatmap=mapa(1), stats=stats(great=80, miss=20), ended_at="2026-11-22T10:00:00Z"),
            score(2, beatmap=mapa(1), stats=stats(great=90, miss=10), ended_at="2026-11-22T11:00:00Z"),
            score(3, beatmap=mapa(1), stats=stats(great=95, miss=5), ended_at="2026-11-22T12:00:00Z"),
            score(4, beatmap=mapa(1), mods=["HR"], ended_at="2026-11-22T12:00:00Z"),  # outra combinacao, 1 tentativa
            score(5, beatmap=mapa(2), ended_at="2026-11-22T13:00:00Z"),
        )
        mapas = analise.painel(self.con)["por_mapa"]
        self.assertEqual(len(mapas), 1)
        m = mapas[0]
        self.assertEqual((m["id"], m["jogadas"], m["mods"]), ("1:", 3, "Sem mods"))
        self.assertEqual([t["misses_100"] for t in m["tentativas"]], [20.0, 10.0, 5.0])
        # Primeira metade (tentativa 1) contra segunda (tentativa 3); a do meio fica de fora.
        self.assertEqual(m["tendencia"], {"antes": 20.0, "depois": 5.0, "sentido": "caindo"})
        self.assertEqual(m["maior_fracao"], 0.5)

    def test_tendencia_estavel_e_subindo(self):
        def tentativas(*misses):
            return [analise.Jogada(i, 1, f"2026-11-22T1{i}:00:00Z", False, "", 100 - m, 0, 0, m, 0.5, 6, 3, 2, "x", 200)
                    for i, m in enumerate(misses)]
        self.assertEqual(analise._tendencia(tentativas(10, 10.5))["sentido"], "estável")
        self.assertEqual(analise._tendencia(tentativas(5, 9))["sentido"], "subindo")

    def test_faixas_de_meia_estrela_com_os_mods(self):
        self.gravar(
            score(1, beatmap=mapa(1, estrelas=6.2)),
            score(2, beatmap=mapa(1, estrelas=6.2), mods=["HR"]),
            score(3, beatmap=mapa(2, estrelas=6.49)),
        )
        with self.con:
            atributos(self.con, 1, 6.95, 3.5, 2.5, mods="HR")  # SR com HR vem do cache de atributos
        faixas = analise.painel(self.con)["por_faixa"]
        self.assertEqual([(f["nome"], f["jogadas"], f["mapas"]) for f in faixas], [("6,0 a 6,5★", 2, 2), ("6,5 a 7,0★", 1, 1)])

    def test_aim_contra_speed_compara_dentro_da_faixa(self):
        # Na faixa 6,0: mapa 1 puxado para aim, mapa 2 para speed. Na faixa 7,0 tudo tem aim/speed maior,
        # mas la tambem um e de aim e o outro de speed.
        self.gravar(
            score(1, beatmap=mapa(1), stats=stats(great=90, miss=10)),
            score(2, beatmap=mapa(2), stats=stats(great=98, miss=2)),
            score(3, beatmap=mapa(3), stats=stats(great=80, miss=20)),
            score(4, beatmap=mapa(4), stats=stats(great=96, miss=4)),
            score(5, beatmap=mapa(5), stats=stats(great=50, miss=50)),  # faixa com uma jogada so: fora
        )
        with self.con:
            for beatmap_id, estrelas, aim, speed in ((1, 6.1, 3.4, 2.0), (2, 6.2, 3.0, 2.6), (3, 7.1, 4.4, 2.6),
                                                     (4, 7.2, 3.9, 3.3), (5, 8.0, 4.5, 3.0)):
                atributos(self.con, beatmap_id, estrelas, aim, speed)
        a = analise.painel(self.con)["aim_speed"]
        self.assertEqual((a["aim"]["jogadas"], a["aim"]["misses_100"]), (2, 15.0))
        self.assertEqual((a["speed"]["jogadas"], a["speed"]["misses_100"]), (2, 3.0))

    def test_sugestoes(self):
        self.gravar(
            # 2 misses por 100 notas num mapa de 500 notas -> 10 misses estimados
            score(1, beatmap=mapa(1, notas=500), stats=stats(great=98, miss=2)),
            # 5 por 100 num mapa de 100 notas -> 5 estimados: vem primeiro
            score(2, beatmap=mapa(2, notas=100), stats=stats(great=95, miss=5)),
            # amostra curta demais (40 notas), mesmo sem miss
            score(3, beatmap=mapa(3), stats=stats(great=40, miss=0)),
            # ja tem FC
            score(4, beatmap=mapa(4, notas=150), stats=stats(great=150, miss=0), passed=True),
        )
        s = analise.painel(self.con)["sugestoes"]
        self.assertEqual([(m["beatmap_id"], m["misses_estimados"]) for m in s], [(2, 5.0), (1, 10.0)])

    def test_por_dia_usa_o_fuso_local(self):
        self.gravar(score(1, ended_at="2026-11-22T12:00:00Z"), score(2, ended_at="2026-11-23T12:00:00Z"))
        dias = analise.painel(self.con)["por_dia"]
        esperado = [datetime(2026, 11, d, 12, tzinfo=timezone.utc).astimezone().date().isoformat() for d in (22, 23)]
        self.assertEqual([d["dia"] for d in dias], esperado)


def pegar(url: str) -> tuple[int, str, bytes]:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.headers.get("content-type", ""), r.read()


class TestRotas(unittest.TestCase):
    """Servidor de verdade numa porta livre, como a janela e o comando painel sobem."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.banco = Path(self.tmp.name) / "banco.sqlite3"
        self.fonte = {"caminho": self.banco, "jogador": "JogadorTeste"}
        self.servidor = painel.Servidor(app=painel.criar_app(lambda: (self.fonte["caminho"], self.fonte["jogador"])))
        self.url = self.servidor.iniciar()
        self.addCleanup(self.servidor.parar)

    def api(self, consulta=""):
        status, tipo, corpo = pegar(self.url + "api/painel" + consulta)
        self.assertEqual(status, 200)
        self.assertIn("application/json", tipo)
        return json.loads(corpo)

    def test_so_escuta_neste_pc(self):
        self.assertTrue(self.url.startswith("http://127.0.0.1:"))

    def test_pagina_e_arquivos_sem_internet(self):
        status, tipo, corpo = pegar(self.url)
        self.assertEqual(status, 200)
        self.assertIn("text/html", tipo)
        html = corpo.decode("utf-8")
        self.assertIn("Mapas sugeridos para farmar", html)
        self.assertNotIn("http", html.replace('href="data:,"', ""))  # nada de CDN
        for arquivo in ("/static/painel.js", "/static/painel.css", "/static/vendor/plotly.min.js"):
            with self.subTest(arquivo=arquivo):
                self.assertIn(arquivo, html)
                status, _, corpo = pegar(self.url + arquivo.lstrip("/"))
                self.assertEqual(status, 200)
                self.assertGreater(len(corpo), 1000)

    def test_sem_banco_avisa(self):
        d = self.api()
        self.assertIn("Ainda não há jogadas", d["aviso"])
        self.assertNotIn("resumo", d)
        self.fonte["caminho"] = None
        self.assertIn("ainda não foi configurado", self.api()["aviso"])

    def test_dados_e_filtros(self):
        con = db.conectar(self.banco)
        with con:
            db.gravar_jogadas(con, [
                score(1, beatmap=mapa(1), ended_at="2020-01-01T12:00:00Z"),
                score(2, beatmap=mapa(1), mods=["HR"], ended_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ])
        con.close()
        d = self.api()
        self.assertIsNone(d["aviso"])
        self.assertEqual(d["jogador"], "JogadorTeste")
        self.assertEqual(d["resumo"]["jogadas"], 2)
        self.assertEqual({m["valor"] for m in d["mods_disponiveis"]}, {"", "HR"})
        self.assertEqual(self.api("?dias=30")["resumo"]["jogadas"], 1)
        self.assertEqual(self.api("?mods=")["resumo"]["jogadas"], 1)  # mods vazio = sem mods
        self.assertEqual(self.api("?mods=HR&dias=30")["resumo"]["jogadas"], 1)
        self.assertEqual(self.api("?mods=DT")["resumo"]["jogadas"], 0)

    def test_periodo_invalido(self):
        with self.assertRaises(urllib.error.HTTPError) as erro:
            pegar(self.url + "api/painel?dias=0")
        erro.exception.close()
        self.assertEqual(erro.exception.code, 422)

    def test_banco_sem_tabelas_nem_colunas_e_migrado_sem_quebrar(self):
        con = sqlite3.connect(self.banco)
        with con:
            con.execute("CREATE TABLE jogadas (score_id INTEGER)")
            con.execute("INSERT INTO jogadas VALUES (7)")
        con.close()
        d = self.api()
        self.assertIsNone(d["aviso"])
        self.assertEqual(d["resumo"]["jogadas"], 0)  # sem ruleset nem notas: nao entra nas contas
        con = sqlite3.connect(self.banco)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT score_id FROM jogadas").fetchall(), [(7,)])  # nada se perde

    def test_nao_escreve_no_banco(self):
        con = db.conectar(self.banco)
        con.close()
        antes = self.banco.stat().st_mtime_ns
        self.api()
        self.assertEqual(self.banco.stat().st_mtime_ns, antes)


class TestBancoConfigurado(unittest.TestCase):
    def test_usa_o_banco_e_o_jogador_da_configuracao(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": tmp}):
            self.assertEqual(painel.banco_configurado(), (None, ""))
            Path(tmp, "config.toml").write_text('[jogador]\nuser_id = 7\nusername = "Fulano"\n', encoding="utf-8")
            caminho, nome = painel.banco_configurado()
            self.assertEqual((caminho, nome), (Path(tmp) / config.BANCO_PADRAO, "Fulano"))


class TestComandoPainel(unittest.TestCase):
    def test_servir_ate_parar(self):
        parar = threading.Event()
        urls = []

        def ao_iniciar(url):
            urls.append(url)
            self.assertEqual(pegar(url)[0], 200)
            parar.set()

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": tmp}):
            painel.servir(abrir_navegador=False, ao_iniciar=ao_iniciar, parar=parar)
        self.assertEqual(len(urls), 1)


if __name__ == "__main__":
    unittest.main()

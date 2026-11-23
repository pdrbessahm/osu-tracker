import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from osu_tracker import __main__ as cli
from osu_tracker import arquivo_osu, db, painel, perfil, progresso, skillsets
from osu_tracker.api import ApiError, NaoEncontrado

from test_mapas import MAPA, score

AGORA = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


# --- .osu sinteticos ---

def osu(objetos: list[tuple[float, float, float]], bpm: float = 180, cs: float = 4, sliders: int = 0,
        pontos_extra: str = "") -> str:
    """Um .osu minimo: (x, y, tempo) de cada circulo; os `sliders` primeiros viram sliders curtos."""
    batida = 60000 / bpm
    linhas = ["osu file format v14", "", "[General]", "Mode: 0", "", "[Difficulty]", f"CircleSize:{cs}",
              "SliderMultiplier:1.4", "", "[TimingPoints]", f"0,{batida},4,2,0,60,1,0", pontos_extra, "", "[HitObjects]"]
    for i, (x, y, t) in enumerate(objetos):
        if i < sliders:
            linhas.append(f"{x:.0f},{y:.0f},{t:.0f},2,0,L|{x + 60:.0f}:{y:.0f},1,60")
        else:
            linhas.append(f"{x:.0f},{y:.0f},{t:.0f},1,0,0:0:0:0:")
    return "\n".join(linhas) + "\n"


def mapa_stream(bpm=180, notas=200):
    b = 60000 / bpm
    objs, t = [], 1000.0
    while len(objs) < notas:
        for k in range(16):  # stream de 16 notas em 1/4, pouco espacado
            objs.append((200 + 8 * (k % 8), 200, t))
            t += b / 4
        t += b  # pausa de uma batida
    return osu(objs[:notas], bpm)


def mapa_jump(bpm=180, notas=200):
    b = 60000 / bpm
    return osu([(80 if i % 2 else 430, 80 + (i % 3) * 100, 1000 + i * b / 2) for i in range(notas)], bpm)


def mapa_tech(bpm=180, notas=200):
    b = 60000 / bpm
    passos = [0.5, 0.75, 0.25, 1.0, 1 / 3, 2 / 3, 0.5, 1.5]
    objs, t = [], 1000.0
    for i in range(notas):
        objs.append((250 + 30 * (i % 4), 200 + 20 * (i % 3), t))
        t += b * passos[i % len(passos)]
    pontos = "\n".join(f"{int(1000 + k * 4 * b)},{-100 / (0.8 + 0.1 * (k % 4))},4,2,0,60,0,0" for k in range(40))
    return osu(objs, bpm, sliders=notas // 3, pontos_extra=pontos)


def mapa_burst(bpm=180, notas=200):
    b = 60000 / bpm
    objs, t, tamanhos = [], 1000.0, [3, 5, 4, 7, 3, 6]
    i = 0
    while len(objs) < notas:
        for _ in range(tamanhos[i % len(tamanhos)]):
            objs.append((220, 220 + 10 * (len(objs) % 3), t))
            t += b / 4
        t += b * 0.75
        i += 1
    return osu(objs[:notas], bpm)


def features(texto):
    return arquivo_osu.features(arquivo_osu.ler(texto))


class TestArquivoOsu(unittest.TestCase):
    def test_stream(self):
        f = features(mapa_stream())
        self.assertGreater(f["frac_stream"], 0.9)
        self.assertEqual(f["maior_corrida"], 16)
        self.assertEqual(f["bpm"], 180)
        self.assertLess(f["frac_saltos"], 0.05)

    def test_jump(self):
        f = features(mapa_jump())
        self.assertEqual(f["frac_stream"], 0)
        self.assertGreater(f["frac_saltos"], 0.9)
        self.assertGreater(f["salto_medio"], 5)
        self.assertGreater(f["frac_1_2"], 0.95)

    def test_tech_e_burst(self):
        tech = features(mapa_tech())
        self.assertGreater(tech["mudanca_ritmo"], 0.6)
        self.assertGreater(tech["divisores"], 3)
        self.assertGreater(tech["sv_por_minuto"], 6)
        self.assertAlmostEqual(tech["frac_sliders"], 66 / 200, places=3)
        burst = features(mapa_burst())
        self.assertGreater(burst["frac_burst"], 0.9)
        self.assertGreater(burst["bursts_impares"], 0.4)

    def test_regras(self):
        casos = {"stream": mapa_stream(), "jump": mapa_jump(), "tech": mapa_tech(), "alternate": mapa_burst()}
        for esperado, texto in casos.items():
            f = features(texto)
            tipos = {t for t in skillsets.TIPOS if skillsets.prob_regra(t, f) >= 0.5}
            with self.subTest(esperado=esperado):
                self.assertIn(esperado, tipos)
        self.assertEqual({t for t in skillsets.TIPOS if skillsets.prob_regra(t, features(mapa_jump())) >= 0.5}, {"jump"})

    def test_bpm_dominante_com_mudanca(self):
        self.assertEqual(features(mapa_jump(notas=100))["fora_do_bpm"], 0)
        # 180 BPM ate 15 s, depois 120 BPM (ponto nao herdado) ate o fim, em 17,5 s: o principal continua 180.
        objs = [(80 if i % 2 else 430, 200, 1000 + i * 165) for i in range(101)]
        f = features(osu(objs, 180, pontos_extra="15000,500,4,2,0,60,1,0"))
        self.assertEqual(f["bpm"], 180)
        self.assertAlmostEqual(f["fora_do_bpm"], 2500 / 17500, places=2)

    def test_invalidos(self):
        with self.assertRaises(arquivo_osu.ArquivoInvalido):
            arquivo_osu.ler("[General]\nMode: 0\n")
        with self.assertRaises(arquivo_osu.ArquivoInvalido):
            features(mapa_jump().replace("Mode: 0", "Mode: 3"))
        with self.assertRaises(arquivo_osu.ArquivoInvalido):
            features(mapa_jump(notas=5))


# --- banco ---

def mapa(beatmap_id, estrelas=6.0, checksum=None):
    return {**MAPA, "id": beatmap_id, "checksum": checksum or f"c{beatmap_id}", "difficulty_rating": estrelas,
            "count_circles": 100, "count_sliders": 0, "count_spinners": 0}


def stats(great, miss=0):
    return {"great": great, "ok": 0, "meh": 0, "miss": miss}


class Banco(unittest.TestCase):
    def setUp(self):
        self.con = db.conectar(Path(":memory:"))
        self.addCleanup(self.con.close)

    def gravar(self, *scores):
        with self.con:
            db.gravar_jogadas(self.con, list(scores))

    def features(self, beatmap_id, feats):
        with self.con:
            skillsets._gravar_features(self.con, beatmap_id, f"c{beatmap_id}", feats, None)


USUARIO = {
    "id": 17, "statistics": {"pp": 4321.5, "global_rank": 51000, "country_rank": 1500, "hit_accuracy": 96.53,
                             "play_count": 12000, "ranked_score": 9876543210, "level": {"current": 100, "progress": 37}},
    "rank_history": {"mode": "osu", "data": [0] + [60000 - 100 * i for i in range(1, 89)] + [51000]},
}


class ApiPerfil:
    def __init__(self):
        self.chamadas = 0

    def buscar_usuario(self, id_ou_nick, mode=None):
        self.chamadas += 1
        return USUARIO


class TestPerfil(Banco):
    def test_foto_uma_vez_por_dia_e_historico_de_rank(self):
        api = ApiPerfil()
        hoje = date(2026, 11, 23)
        self.assertTrue(perfil.registrar_se_preciso(self.con, api, 17, "osu", hoje))
        self.assertFalse(perfil.registrar_se_preciso(self.con, api, 17, "osu", hoje))
        self.assertEqual(api.chamadas, 1)
        foto = self.con.execute("SELECT pp, rank_global, rank_pais, acuracia, play_count, ranked_score, nivel "
                                "FROM perfil_diario").fetchone()
        self.assertEqual(foto, (4321.5, 51000, 1500, 0.9653, 12000, 9876543210, 100.37))
        hist = self.con.execute("SELECT dia, rank_global FROM rank_historico ORDER BY dia").fetchall()
        self.assertEqual(len(hist), 90)
        self.assertEqual(hist[-1], ("2026-11-23", 51000))
        self.assertEqual(hist[0], ("2026-08-26", None))  # 0 = sem rank
        self.assertEqual(hist[1], ("2026-08-27", 59900))

    def test_variacao_em_7_e_30_dias(self):
        for dia, pp in (("2026-10-20", 4000.0), ("2026-11-16", 4300.0), ("2026-11-23", 4321.5)):
            with self.con:
                perfil.gravar(self.con, {**USUARIO, "statistics": {**USUARIO["statistics"], "pp": pp}},
                              "osu", date.fromisoformat(dia))
        p = progresso.perfil(self.con)
        self.assertEqual(p["variacao"]["7"]["pp"], 21.5)
        self.assertEqual(p["variacao"]["30"]["pp"], 321.5)  # foto mais proxima antes de 24/10: 20/10
        # rank: a foto do dia vale mais que o historico importado; nos dias sem foto vale o historico.
        self.assertEqual(p["variacao"]["7"]["rank_global"], 0)
        ranks = {r["dia"]: r["rank_global"] for r in p["rank"]}
        self.assertEqual((ranks["2026-11-16"], ranks["2026-11-17"]), (51000, 60000 - 100 * 83))
        self.assertEqual(p["rank"][-1], {"dia": "2026-11-23", "rank_global": 51000})


class TestSemanas(Banco):
    def test_tetos_por_semana_pass_fc_e_janelas(self):
        self.gravar(
            score(1, beatmap=mapa(1, 5.0), passed=True, stats=stats(100), is_perfect_combo=True,
                  ended_at="2026-11-10T12:00:00Z"),
            score(2, beatmap=mapa(2, 6.0), passed=True, stats=stats(98, 2), ended_at="2026-11-11T12:00:00Z"),
            score(3, beatmap=mapa(3, 7.5), stats=stats(40, 10), ended_at="2026-11-12T12:00:00Z"),
            score(4, beatmap=mapa(4, 6.5), passed=True, stats=stats(100), is_perfect_combo=True,
                  ended_at="2026-11-20T12:00:00Z"),
        )
        d = progresso.progresso(self.con, AGORA)
        semanas = {s["semana"]: s for s in d["semanas"]}
        primeira = semanas[min(semanas)]
        self.assertEqual(primeira["estrelas_pass"]["valor"], 6.0)
        self.assertEqual(primeira["estrelas_fc"]["valor"], 5.0)
        self.assertEqual(primeira["estrelas_tentada"]["valor"], 7.5)
        self.assertEqual(primeira["fcs"], 1)
        j7 = d["janelas"]["7"]
        self.assertEqual(j7["recente"]["estrelas_fc"]["valor"], 6.5)
        self.assertEqual(j7["anterior"]["estrelas_fc"]["valor"], 5.0)


class TestClassificador(Banco):
    def separaveis(self, n=24):
        """n mapas de stream e n de jump, com features que a regra nao separa (para o modelo mostrar efeito)."""
        for i in range(n):
            self.features(100 + i, {**features(mapa_jump()), "frac_stream": 0.0, "densidade": 2.0 + i / 100})
            self.features(200 + i, {**features(mapa_jump()), "frac_stream": 0.0, "densidade": 8.0 + i / 100})

    def test_marcacao_vale_acima_da_regra(self):
        self.features(1, features(mapa_jump()))
        c = skillsets.classificar(self.con)[1]
        self.assertEqual(c["tipos"], ["jump"])
        self.assertEqual(c["por_tipo"]["jump"]["fonte"], "regra")
        skillsets.marcar(self.con, 1, {"stream", "tech"})
        c = skillsets.classificar(self.con)[1]
        self.assertEqual(c["tipos"], ["stream", "tech"])
        self.assertEqual({i["fonte"] for i in c["por_tipo"].values()}, {"marcação"})
        skillsets.marcar(self.con, 1, set())  # nenhum
        self.assertEqual(skillsets.classificar(self.con)[1]["tipos"], [])
        skillsets.desmarcar(self.con, 1)
        self.assertEqual(skillsets.classificar(self.con)[1]["tipos"], ["jump"])
        with self.assertRaises(ValueError):
            skillsets.marcar(self.con, 1, {"farm"})

    def test_modelo_so_com_marcacoes_suficientes_e_substitui_a_regra(self):
        self.separaveis()
        for i in range(skillsets.MINIMO_POR_CLASSE - 1):
            skillsets.marcar(self.con, 200 + i, {"stream"})
            skillsets.marcar(self.con, 100 + i, {"jump"})
        self.assertEqual(skillsets.atualizar_modelos(self.con), [])
        skillsets.marcar(self.con, 200 + 19, {"stream"})
        skillsets.marcar(self.con, 100 + 19, {"jump"})
        # Cada marcacao diz sim ou nao para os quatro tipos: stream e jump tem 20 de cada; tech e alternate, 0 sim.
        self.assertEqual(skillsets.atualizar_modelos(self.con), ["stream", "jump"])
        self.assertEqual(skillsets.atualizar_modelos(self.con), [])  # nada mudou: nao treina de novo
        situacao = {s["tipo"]: s for s in skillsets.situacao(self.con)}
        self.assertEqual(situacao["stream"]["fonte"], "modelo")
        self.assertGreaterEqual(situacao["stream"]["precisao_cv"], 0.95)
        self.assertEqual(situacao["stream"]["precisao_regra"], 0.5)  # a regra nao via nenhum stream
        self.assertIsNone(situacao["stream"]["aviso"])
        self.assertEqual(situacao["stream"]["pesos"][0]["feature"], arquivo_osu.NOMES["densidade"])
        # Mapas nao marcados agora seguem o modelo: densidade alta e stream, mesmo sem stream no .osu.
        classes = skillsets.classificar(self.con)
        self.assertEqual(classes[223]["por_tipo"]["stream"]["fonte"], "modelo")
        self.assertTrue(classes[223]["por_tipo"]["stream"]["sim"])
        self.assertFalse(classes[123]["por_tipo"]["stream"]["sim"])
        # Marcacao nova muda a assinatura e retreina.
        skillsets.marcar(self.con, 222, {"stream"})
        self.assertIn("stream", skillsets.atualizar_modelos(self.con))

    def test_precisao_baixa_avisa(self):
        aleatorio = [0.3, 0.9, 0.1, 0.7, 0.5, 0.2, 0.8, 0.4, 0.6, 0.0]
        for i in range(44):
            self.features(i + 1, {**features(mapa_jump()), "densidade": aleatorio[i % 10] + i / 1000})
            skillsets.marcar(self.con, i + 1, {"tech"} if (i * 7) % 2 else set())
        skillsets.atualizar_modelos(self.con)
        tech = next(s for s in skillsets.situacao(self.con) if s["tipo"] == "tech")
        self.assertEqual(tech["fonte"], "modelo")
        self.assertLess(tech["precisao_cv"], skillsets.PRECISAO_BAIXA)
        self.assertIn("Precisão estimada baixa", tech["aviso"])

    def test_para_marcar_pega_a_maior_duvida_e_pula_os_marcados(self):
        base = features(mapa_jump())
        self.features(1, {**base, "frac_saltos": 0.26})   # bem no limite da regra de jump
        self.features(2, {**base, "frac_saltos": 0.9})
        self.features(3, {**base, "frac_saltos": 0.25})
        skillsets.marcar(self.con, 3, {"jump"})
        lista = skillsets.para_marcar(skillsets.classificar(self.con))
        self.assertEqual([(b, t) for b, t, _ in lista][:1], [(1, "jump")])
        self.assertNotIn(3, [b for b, _, _ in lista])

    def test_logistica_simples(self):
        x = [[i] for i in range(20)]
        y = [int(i >= 10) for i in range(20)]
        modelo = skillsets.treinar_logistica(x, y)
        self.assertLess(skillsets.prever(modelo, [2]), 0.2)
        self.assertGreater(skillsets.prever(modelo, [17]), 0.8)
        self.assertGreaterEqual(skillsets.validacao_cruzada(x, y), 0.9)  # erra no maximo os vizinhos da fronteira


class ApiOsu:
    def __init__(self, arquivos, falhas=None):
        self.arquivos = arquivos
        self.falhas = falhas or {}
        self.pedidos = []

    def arquivo_osu(self, beatmap_id):
        self.pedidos.append(beatmap_id)
        if beatmap_id in self.falhas:
            raise self.falhas[beatmap_id]
        return self.arquivos[beatmap_id].encode()


class TestLerMapas(Banco):
    def test_baixa_guarda_e_nao_baixa_de_novo(self):
        stream, jump = mapa_stream().encode(), mapa_jump().encode()
        self.gravar(score(1, beatmap=mapa(1, checksum=arquivo_osu.md5(stream))),
                    score(2, beatmap=mapa(2, checksum=arquivo_osu.md5(jump))),
                    score(3, beatmap=mapa(3)), score(4, beatmap=mapa(4)))
        api = ApiOsu({1: mapa_stream(), 2: mapa_jump(), 3: "lixo"},
                     falhas={4: NaoEncontrado("HTTP 404")})
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp) / "mapas_osu"
            with self.assertLogs("osu_tracker", "WARNING") as logs:
                self.assertEqual(skillsets.ler_mapas(self.con, api, pasta), (4, 0))
            self.assertIn("mapa 3 nao deu para ler", logs.output[0])
            self.assertTrue((pasta / "1.osu").exists())
            self.assertEqual(skillsets.ler_mapas(self.con, api, pasta), (0, 0))
            self.assertEqual(sorted(api.pedidos), [1, 2, 3, 4])
            classes = skillsets.classificar(self.con)
            self.assertIn("stream", classes[1]["tipos"])
            self.assertIn("jump", classes[2]["tipos"])
            erros = dict(self.con.execute("SELECT beatmap_id, erro FROM mapas_osu WHERE erro IS NOT NULL"))
            self.assertEqual(set(erros), {3, 4})
            # Mapa atualizado no site (checksum novo): le de novo.
            with self.con:
                db.gravar_jogadas(self.con, [score(9, beatmap=mapa(1, checksum="novo"))])
            self.assertEqual([b for b, _ in skillsets.pendentes_features(self.con)], [1])
            self.assertEqual(skillsets.ler_mapas(self.con, api, pasta), (1, 0))

    def test_falha_de_rede_deixa_para_depois(self):
        self.gravar(score(1, beatmap=mapa(1)), score(2, beatmap=mapa(2), ended_at="2026-11-22T18:00:00Z"))
        api = ApiOsu({1: mapa_stream()}, falhas={2: ApiError("sem rede")})
        with tempfile.TemporaryDirectory() as tmp, self.assertLogs("osu_tracker", "WARNING"):
            self.assertEqual(skillsets.ler_mapas(self.con, api, Path(tmp)), (0, 2))


# --- painel e linha de comando ---

def pegar(url, corpo=None):
    req = urllib.request.Request(url, data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


class TestRotas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.banco = Path(self.tmp.name) / "banco.sqlite3"
        servidor = painel.Servidor(app=painel.criar_app(lambda: (self.banco, "JogadorTeste")))
        self.url = servidor.iniciar()
        self.addCleanup(servidor.parar)

    def test_progresso_e_marcacao(self):
        self.assertIn("Ainda não há dados", pegar(self.url + "api/progresso")["aviso"])
        con = db.conectar(self.banco)
        with con:
            db.gravar_jogadas(con, [score(1, beatmap=mapa(1))])
            skillsets._gravar_features(con, 1, "c1", features(mapa_jump()), None)
            perfil.gravar(con, USUARIO, "osu", date(2026, 11, 23))
        con.close()
        d = pegar(self.url + "api/progresso")
        self.assertIsNone(d["aviso"])
        self.assertEqual(d["perfil"]["atual"]["pp"], 4321.5)
        self.assertEqual(len(d["perfil"]["rank"]), 89)
        self.assertEqual(d["skillsets"]["mapas"][0]["tipos"], ["jump"])
        self.assertEqual(d["skillsets"]["para_marcar"][0]["beatmap_id"], 1)
        jump = next(t for t in d["skillsets"]["tipos"] if t["tipo"] == "jump")
        self.assertEqual((jump["mapas"], jump["total"]["jogadas"]), (1, 1))

        self.assertEqual(pegar(self.url + "api/marcacoes", {"beatmap_id": 1, "tipos": ["stream"]})["ok"], True)
        d = pegar(self.url + "api/progresso")
        self.assertEqual(d["skillsets"]["mapas"][0]["tipos"], ["stream"])
        self.assertEqual(d["skillsets"]["para_marcar"], [])
        self.assertEqual(d["skillsets"]["classificador"][0]["positivos"], 1)
        pegar(self.url + "api/marcacoes", {"beatmap_id": 1, "limpar": True})
        self.assertEqual(pegar(self.url + "api/progresso")["skillsets"]["mapas"][0]["tipos"], ["jump"])

        with self.assertRaises(urllib.error.HTTPError) as erro:
            pegar(self.url + "api/marcacoes", {"beatmap_id": 1, "tipos": ["farm"]})
        erro.exception.close()
        self.assertEqual(erro.exception.code, 422)

    def test_pagina_tem_a_aba_progresso(self):
        with urllib.request.urlopen(self.url, timeout=10) as r:
            html = r.read().decode()
        self.assertIn('data-aba="progresso"', html)
        self.assertIn("/static/progresso.js", html)
        with urllib.request.urlopen(self.url + "static/progresso.js", timeout=10) as r:
            self.assertIn("/api/marcacoes", r.read().decode())


class TestComandoMarcar(unittest.TestCase):
    def test_marcar_listar_e_limpar(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": tmp}), \
                mock.patch("osu_tracker.registro.configurar"):
            Path(tmp, "config.toml").write_text(
                '[jogador]\nuser_id = 7\n\n[osu_api]\nclient_id = "1"\nclient_secret = "x"\n', encoding="utf-8")
            con = db.conectar(Path(tmp) / "osu_tracker.sqlite3")
            with con:
                db.gravar_jogadas(con, [score(5, beatmap=mapa(5))])
                skillsets._gravar_features(con, 5, "c5", features(mapa_stream()), None)
            con.close()

            def rodar(*args):
                saida = io.StringIO()
                with redirect_stdout(saida), redirect_stderr(saida):
                    codigo = cli.main(["marcar", *args])
                return codigo, saida.getvalue()

            codigo, texto = rodar("--sugerir")
            self.assertEqual(codigo, 0)
            self.assertIn("5  Artista - Musica [Insane]", texto)
            self.assertEqual(rodar("5", "stream", "jump")[0], 0)
            self.assertEqual(rodar("5", "nenhum", "jump")[0], 2)
            self.assertEqual(rodar("5", "farm")[0], 2)
            self.assertIn("Nenhum mapa para marcar", rodar("--sugerir")[1])
            con = db.conectar(Path(tmp) / "osu_tracker.sqlite3")
            self.assertEqual(skillsets.classificar(con)[5]["tipos"], ["stream", "jump"])
            con.close()
            self.assertEqual(rodar("5", "--limpar")[0], 0)
            # fecha antes de sair do with: no Windows a pasta temporaria nao apaga arquivo aberto
            con = db.conectar(Path(tmp) / "osu_tracker.sqlite3")
            try:
                self.assertEqual(skillsets.carregar_marcacoes(con), {})
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()

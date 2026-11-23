"""Bancos criados por versoes anteriores do coletor continuam abrindo no painel e no coletor.

Os schemas abaixo sao copias exatas do historico do git (osu_tracker/db.py), porque o CI
clona o repositorio sem historico. Ao mudar o schema, acrescente aqui o da versao que saiu.
"""

import json
import os
import sqlite3
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path

from osu_tracker import db, mapas, painel

from test_mapas import MAPA, score

# Commits 0b5dbe3, b379371 e dff445a: so jogadas e coletas.
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jogadas (
    score_id            INTEGER PRIMARY KEY,
    legacy_score_id     INTEGER,
    user_id             INTEGER NOT NULL,
    beatmap_id          INTEGER NOT NULL,
    beatmapset_id       INTEGER,
    ruleset_id          INTEGER NOT NULL,
    started_at          TEXT,
    ended_at            TEXT NOT NULL,
    passed              INTEGER NOT NULL,
    rank                TEXT,
    accuracy            REAL,
    max_combo           INTEGER,
    total_score         INTEGER,
    legacy_total_score  INTEGER,
    pp                  REAL,
    mods                TEXT NOT NULL,
    mods_json           TEXT NOT NULL,
    n_great             INTEGER NOT NULL,
    n_ok                INTEGER NOT NULL,
    n_meh               INTEGER NOT NULL,
    n_miss              INTEGER NOT NULL,
    statistics_json     TEXT NOT NULL,
    max_statistics_json TEXT,
    has_replay          INTEGER,
    raw_json            TEXT NOT NULL,
    coletado_em         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_jogadas_beatmap ON jogadas (beatmap_id);
CREATE INDEX IF NOT EXISTS idx_jogadas_ended ON jogadas (ended_at);

CREATE TABLE IF NOT EXISTS coletas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    recebidas   INTEGER,
    novas       INTEGER,
    erro        TEXT
);
"""

# Do commit 2e2caa2 em diante o schema e o atual.
VERSOES = {"0b5dbe3 a dff445a": SCHEMA_V1, "2e2caa2 em diante": db.SCHEMA}

JOGADAS = 160  # como o banco do PC em que o problema apareceu


def criar_banco(caminho: Path, schema: str) -> None:
    """Banco no schema de uma versao antiga, com jogadas gravadas como aquela versao gravava."""
    con = sqlite3.connect(caminho)
    con.executescript(schema)
    colunas = [r[1] for r in con.execute("PRAGMA table_info(jogadas)")]
    scores = [
        score(i, beatmap={**MAPA, "id": 1000 + i % 7, "checksum": f"c{i % 7}"}, mods=["HD"] if i % 3 else [],
              ended_at=f"2026-11-{1 + i % 20:02d}T12:00:00Z")
        for i in range(1, JOGADAS + 1)
    ]
    linhas = [{c: v for c, v in db._linha(s).items() if c in colunas} for s in scores]
    with con:
        if "mapas" in schema:  # desde 2e2caa2 o coletor grava o mapa junto com a jogada
            mapas.gravar_mapas(con, [db._linha_mapa(s) for s in scores])
        nomes = list(linhas[0])
        con.executemany(f"INSERT INTO jogadas ({', '.join(nomes)}) VALUES ({', '.join(':' + c for c in nomes)})",
                        linhas)
        con.execute("INSERT INTO coletas (inicio, fim, recebidas, novas) VALUES ('2026-11-20T12:00:00Z', "
                    "'2026-11-20T12:00:05Z', 160, 160)")
    con.close()


def pegar(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


class TestVersoesAnteriores(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.banco = Path(self.tmp.name) / "osu-tracker.sqlite3"
        servidor = painel.Servidor(app=painel.criar_app(lambda: (self.banco, "JogadorTeste")))
        self.url = servidor.iniciar() + "api/painel"
        self.addCleanup(servidor.parar)

    def test_painel_carrega_banco_de_cada_versao(self):
        for versao, schema in VERSOES.items():
            with self.subTest(versao=versao):
                self.banco.unlink(missing_ok=True)
                criar_banco(self.banco, schema)
                d = pegar(self.url)
                self.assertIsNone(d["aviso"])
                self.assertEqual(d["resumo"]["jogadas"], JOGADAS)
                self.assertEqual({m["valor"] for m in d["mods_disponiveis"]}, {"", "HD"})
                self.assertTrue(d["por_faixa"])  # estrelas vem da tabela mapas, preenchida a partir do raw_json
                self.assertTrue(all(p["fracao"] is not None for p in d["acc"]))
                self.assertEqual(pegar(self.url + "?mods=HD")["resumo"]["jogadas"], JOGADAS - JOGADAS // 3)

    def test_migracao_nao_perde_dados_e_e_idempotente(self):
        criar_banco(self.banco, SCHEMA_V1)
        antes = sqlite3.connect(self.banco)
        jogadas = antes.execute("SELECT score_id, raw_json, n_miss FROM jogadas ORDER BY score_id").fetchall()
        antes.close()
        for _ in range(2):
            db.conectar(self.banco).close()
        con = sqlite3.connect(self.banco)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT score_id, raw_json, n_miss FROM jogadas ORDER BY score_id").fetchall(),
                         jogadas)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM coletas").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jogadas WHERE mods_chave IS NULL").fetchone()[0], 0)
        self.assertFalse(db.precisa_migrar(con))

    def test_banco_que_nao_da_para_migrar_mostra_aviso(self):
        criar_banco(self.banco, SCHEMA_V1)
        os.chmod(self.banco, stat.S_IREAD)  # somente leitura: a migracao falha
        self.addCleanup(os.chmod, self.banco, stat.S_IREAD | stat.S_IWRITE)
        with self.assertLogs("osu_tracker", "WARNING") as logs:
            d = pegar(self.url)
        self.assertIn("Não consegui migrar o banco", logs.output[0])
        self.assertIn("Não consegui ler o banco", d["aviso"])
        self.assertNotIn("resumo", d)


class TestColunasDoSchema(unittest.TestCase):
    def test_toda_coluna_que_falta_e_criada(self):
        # Banco de uma versao futura hipotetica a menos: tira colunas de cada tabela e ve se voltam.
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(
            "CREATE TABLE jogadas (score_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, beatmap_id INTEGER NOT NULL,"
            " ruleset_id INTEGER NOT NULL, ended_at TEXT NOT NULL, passed INTEGER NOT NULL, mods TEXT NOT NULL,"
            " mods_json TEXT NOT NULL, n_great INTEGER NOT NULL, n_ok INTEGER NOT NULL, n_meh INTEGER NOT NULL,"
            " n_miss INTEGER NOT NULL, statistics_json TEXT NOT NULL, raw_json TEXT NOT NULL);"
            "CREATE TABLE mapas (beatmap_id INTEGER PRIMARY KEY, titulo TEXT);"
            "CREATE TABLE mapas_mods (beatmap_id INTEGER NOT NULL, ruleset_id INTEGER NOT NULL, mods_chave TEXT NOT NULL,"
            " mods_json TEXT NOT NULL, taxa REAL NOT NULL, taxa_api REAL NOT NULL, PRIMARY KEY (beatmap_id, ruleset_id, mods_chave));"
        )
        self.assertTrue(db.precisa_migrar(con))
        db.migrar(con)
        referencia = sqlite3.connect(":memory:")
        self.addCleanup(referencia.close)
        referencia.executescript(db.SCHEMA)
        for tabela in ("jogadas", "mapas", "mapas_mods", "coletas"):
            with self.subTest(tabela=tabela):
                esperadas = {r[1] for r in referencia.execute(f"PRAGMA table_info({tabela})")}
                self.assertEqual({r[1] for r in con.execute(f"PRAGMA table_info({tabela})")}, esperadas)
        self.assertFalse(db.precisa_migrar(con))


if __name__ == "__main__":
    unittest.main()

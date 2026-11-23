import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from osu_tracker import db, mapas
from osu_tracker.api import ApiError, NaoEncontrado

MAPA = {
    "id": 5587991, "beatmapset_id": 2495265, "mode_int": 0, "version": "Insane", "status": "ranked",
    "checksum": "c3a6", "difficulty_rating": 5.72, "bpm": 175.5, "total_length": 143, "hit_length": 132,
    "ar": 9.3, "accuracy": 8.5, "cs": 4, "drain": 6.5,
    "count_circles": 304, "count_sliders": 289, "count_spinners": 0, "last_updated": "2026-09-02T02:40:53Z",
}
CONJUNTO = {"artist": "Artista", "title": "Musica", "creator": "Mapper"}


def score(score_id, mods=(), beatmap=None, passed=False, stats=None, ended_at="2026-11-22T17:08:40Z", ruleset_id=0,
          **extra):
    beatmap = beatmap or MAPA
    return {
        **extra,
        "id": score_id, "legacy_score_id": 0, "user_id": 1, "beatmap_id": beatmap["id"], "ruleset_id": ruleset_id,
        "ended_at": ended_at, "passed": passed, "rank": "B", "accuracy": 0.88, "max_combo": 66,
        "mods": [m if isinstance(m, dict) else {"acronym": m} for m in mods],
        "statistics": stats or {"great": 42, "ok": 4, "miss": 3},
        "maximum_statistics": {"great": 49}, "beatmap": beatmap, "beatmapset": CONJUNTO,
    }


class ApiFalsa:
    def __init__(self, falhas=None):
        self.chamadas = []
        self.falhas = falhas or {}

    def atributos_dificuldade(self, beatmap_id, mods, ruleset_id):
        self.chamadas.append((beatmap_id, tuple(mods), ruleset_id))
        if beatmap_id in self.falhas:
            raise self.falhas[beatmap_id]
        return {"star_rating": 5.0 + len(mods), "max_combo": 886, "aim_difficulty": 3.2,
                "speed_difficulty": 1.9, "slider_factor": 0.98, "campo_novo": 1}


class TestChaveMods(unittest.TestCase):
    def test_ignora_mods_sem_efeito_e_ordena(self):
        self.assertEqual(mapas.chave_mods([{"acronym": "HR"}, {"acronym": "CL"}, {"acronym": "HD"}, {"acronym": "NF"}]), "HDHR")
        self.assertEqual(mapas.chave_mods([{"acronym": "CL"}]), "")
        self.assertEqual(mapas.chave_mods([]), "")

    def test_velocidade_custom_entra_na_chave(self):
        self.assertEqual(mapas.chave_mods([{"acronym": "DT"}]), "DT")
        self.assertEqual(mapas.chave_mods([{"acronym": "DT", "settings": {"speed_change": 1.3}}]), "DT(speed_change=1.3)")
        # Ajuste que nao muda dificuldade (som do NC) nao separa o cache.
        self.assertEqual(mapas.chave_mods([{"acronym": "NC", "settings": {"adjust_pitch": True}}]), "NC")

    def test_difficulty_adjust(self):
        mods = [{"acronym": "DA", "settings": {"approach_rate": 10, "extended_limits": True}}]
        self.assertEqual(mapas.chave_mods(mods), "DA(approach_rate=10)")


class TestAjustar(unittest.TestCase):
    base = {"bpm": 175.5, "total_length": 143, "hit_length": 132, "ar": 9.3, "od": 8.5, "cs": 4.0, "hp": 6.5, "ruleset_id": 0}

    def test_sem_mods(self):
        r = mapas.ajustar(self.base, [])
        self.assertEqual((r["bpm"], r["total_length"], r["ar"], r["od"], r["cs"], r["taxa"]), (175.5, 143, 9.3, 8.5, 4.0, 1.0))

    def test_hr_e_ez(self):
        r = mapas.ajustar(self.base, [{"acronym": "HR"}])
        self.assertEqual((r["ar"], r["od"], r["cs"], r["hp"]), (10.0, 10.0, 5.2, 9.1))
        r = mapas.ajustar(self.base, [{"acronym": "EZ"}])
        self.assertEqual((r["ar"], r["od"], r["cs"], r["hp"]), (4.65, 4.25, 2.0, 3.25))

    def test_dt_e_ht(self):
        r = mapas.ajustar(self.base, [{"acronym": "DT"}])
        self.assertAlmostEqual(r["bpm"], 263.25)
        self.assertAlmostEqual(r["total_length"], 95.333, places=3)
        self.assertEqual((r["ar"], r["od"], r["taxa"], r["taxa_api"]), (10.53, 10.11, 1.5, 1.5))
        r = mapas.ajustar(self.base, [{"acronym": "HT"}])
        self.assertAlmostEqual(r["bpm"], 131.625)
        self.assertEqual((r["ar"], r["taxa"]), (8.07, 0.75))

    def test_velocidade_custom_usa_a_real_e_registra_a_da_api(self):
        r = mapas.ajustar(self.base, [{"acronym": "DT", "settings": {"speed_change": 1.2}}])
        self.assertAlmostEqual(r["bpm"], 210.6)
        self.assertEqual((r["taxa"], r["taxa_api"]), (1.2, 1.5))

    def test_difficulty_adjust_substitui_o_valor_do_mapa(self):
        r = mapas.ajustar(self.base, [{"acronym": "DA", "settings": {"approach_rate": 8, "circle_size": 5}}])
        self.assertEqual((r["ar"], r["cs"], r["od"]), (8.0, 5.0, 8.5))

    def test_outro_modo_nao_calcula_ar_od(self):
        r = mapas.ajustar({**self.base, "ruleset_id": 1}, [{"acronym": "DT"}])
        self.assertIsNone(r["ar"])
        self.assertAlmostEqual(r["bpm"], 263.25)


class TestFracaoJogada(unittest.TestCase):
    def test_fail_e_pass(self):
        self.assertEqual(mapas.fracao_jogada({"great": 42, "ok": 4, "miss": 3}, MAPA, 0), round(49 / 593, 4))
        self.assertEqual(mapas.fracao_jogada({"great": 590, "ok": 3, "slider_tail_hit": 289}, MAPA, 0), 1.0)

    def test_outro_modo_ou_mapa_sem_contagem(self):
        self.assertIsNone(mapas.fracao_jogada({"great": 10}, MAPA, 1))
        self.assertIsNone(mapas.fracao_jogada({"great": 10}, {}, 0))


class TestBanco(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self.tmp.name) / "teste.sqlite3"
        self.con = db.conectar(self.caminho)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_gravar_jogadas_guarda_mapa_chave_e_fracao(self):
        with self.con:
            db.gravar_jogadas(self.con, [score(1, ["HR", "CL"])])
        self.assertEqual(self.con.execute("SELECT mods_chave, fracao_jogada FROM jogadas").fetchone(), ("HR", round(49 / 593, 4)))
        self.assertEqual(self.con.execute("SELECT titulo, versao, bpm, od FROM mapas").fetchone(), ("Musica", "Insane", 175.5, 8.5))

    def test_enriquecer_usa_cache_e_nao_repete(self):
        api = ApiFalsa()
        with self.con:
            db.gravar_jogadas(self.con, [score(1, ["HR", "CL"]), score(2, ["HR"]), score(3, ["CL"]), score(4, ["DT", "NF"])])
        self.assertEqual(mapas.enriquecer(self.con, api), (3, 0))
        self.assertEqual(sorted(api.chamadas), [(5587991, (), 0), (5587991, ("DT",), 0), (5587991, ("HR",), 0)])

        with self.con:
            db.gravar_jogadas(self.con, [score(5, ["HR"])])
        self.assertEqual(mapas.enriquecer(self.con, api), (0, 0))
        self.assertEqual(len(api.chamadas), 3)

        linha = self.con.execute(
            "SELECT star_rating, ar, bpm, taxa, max_combo, atributos_json FROM mapas_mods WHERE mods_chave = 'DT'"
        ).fetchone()
        self.assertEqual(linha[:5], (6.0, 10.53, 263.25, 1.5, 886))
        self.assertEqual(json.loads(linha[5])["campo_novo"], 1)

    def test_limite_por_coleta_e_ordem_mais_recente_primeiro(self):
        outro = {**MAPA, "id": 2, "checksum": "x"}
        with self.con:
            db.gravar_jogadas(self.con, [score(1), score(2, beatmap=outro, ended_at="2026-11-23T00:00:00Z")])
        api = ApiFalsa()
        self.assertEqual(mapas.enriquecer(self.con, api, limite=1), (1, 1))
        self.assertEqual(api.chamadas[0][0], 2)
        self.assertEqual(mapas.enriquecer(self.con, api, limite=1), (1, 0))

    def test_mapa_recusado_fica_gravado_e_falha_de_rede_para(self):
        apagado = {**MAPA, "id": 7, "checksum": "y"}
        with self.con:
            db.gravar_jogadas(self.con, [score(1, ended_at="2026-11-21T00:00:00Z"), score(2, beatmap=apagado)])
        api = ApiFalsa(falhas={7: NaoEncontrado("HTTP 404")})
        with self.assertLogs("osu_tracker", "WARNING"):
            self.assertEqual(mapas.enriquecer(self.con, api), (2, 0))
        self.assertEqual(self.con.execute("SELECT erro FROM mapas_mods WHERE beatmap_id = 7").fetchone(), ("HTTP 404",))
        self.assertEqual(mapas.enriquecer(self.con, api), (0, 0))  # nao tenta de novo

        with self.con:
            db.gravar_jogadas(self.con, [score(3, ["HD"])])
        sem_rede = ApiFalsa(falhas={MAPA["id"]: ApiError("Sem resposta")})
        with self.assertLogs("osu_tracker", "WARNING"):
            self.assertEqual(mapas.enriquecer(self.con, sem_rede), (0, 1))

    def test_mapa_atualizado_descarta_atributos(self):
        with self.con:
            db.gravar_jogadas(self.con, [score(1)])
        mapas.enriquecer(self.con, ApiFalsa())
        with self.con:
            db.gravar_jogadas(self.con, [score(2, beatmap={**MAPA, "checksum": "novo"})])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM mapas_mods").fetchone()[0], 0)
        self.assertEqual(mapas.enriquecer(self.con, ApiFalsa()), (1, 0))


class TestMigracao(unittest.TestCase):
    def test_banco_antigo_ganha_colunas_e_mapas(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "antigo.sqlite3"
            antigo = sqlite3.connect(caminho)
            # Tabela como a etapa 1 criava (resumida), sem mods_chave e fracao_jogada.
            antigo.execute(
                "CREATE TABLE jogadas (score_id INTEGER PRIMARY KEY, beatmap_id INTEGER NOT NULL,"
                " ruleset_id INTEGER NOT NULL, ended_at TEXT NOT NULL, mods_json TEXT NOT NULL, raw_json TEXT NOT NULL)"
            )
            s = score(9, ["HR", "CL"])
            antigo.execute(
                "INSERT INTO jogadas VALUES (9, ?, 0, ?, ?, ?)",
                (MAPA["id"], s["ended_at"], json.dumps(s["mods"]), json.dumps(s)),
            )
            antigo.commit()
            self.assertNotIn("mods_chave", {r[1] for r in antigo.execute("PRAGMA table_info(jogadas)")})
            antigo.close()

            con = db.conectar(caminho)
            try:
                self.assertEqual(con.execute("SELECT mods_chave, fracao_jogada FROM jogadas").fetchone(), ("HR", round(49 / 593, 4)))
                self.assertEqual(con.execute("SELECT COUNT(*) FROM mapas").fetchone()[0], 1)
                self.assertEqual(len(mapas.pendentes(con)), 1)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()

"""Banco SQLite: jogadas e registro de cada coleta."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jogadas (
    score_id            INTEGER PRIMARY KEY,   -- id do score na API (unico, garante sem duplicata)
    legacy_score_id     INTEGER,               -- id no stable, se houver
    user_id             INTEGER NOT NULL,
    beatmap_id          INTEGER NOT NULL,
    beatmapset_id       INTEGER,
    ruleset_id          INTEGER NOT NULL,
    started_at          TEXT,
    ended_at            TEXT NOT NULL,         -- ISO 8601 em UTC
    passed              INTEGER NOT NULL,      -- 0 = fail
    rank                TEXT,
    accuracy            REAL,                  -- 0 a 1
    max_combo           INTEGER,
    total_score         INTEGER,
    legacy_total_score  INTEGER,
    pp                  REAL,
    mods                TEXT NOT NULL,         -- acronimos concatenados, ex.: HDDT
    mods_json           TEXT NOT NULL,         -- mods com settings (ex.: velocidade do DT)
    n_great             INTEGER NOT NULL,      -- 300
    n_ok                INTEGER NOT NULL,      -- 100
    n_meh               INTEGER NOT NULL,      -- 50
    n_miss              INTEGER NOT NULL,
    statistics_json     TEXT NOT NULL,
    max_statistics_json TEXT,                  -- em fail cobre so o trecho jogado, nao o mapa todo
    has_replay          INTEGER,
    raw_json            TEXT NOT NULL,         -- resposta completa, inclui beatmap e beatmapset
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


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.executescript(SCHEMA)
    return con


def _linha(score: dict) -> dict:
    stats = score.get("statistics") or {}
    mods = score.get("mods") or []
    beatmap = score.get("beatmap") or {}
    return {
        "score_id": score["id"],
        # Fail do stable vem com legacy_score_id 0 (sem id no stable); guarda NULL.
        "legacy_score_id": score.get("legacy_score_id") or None,
        "user_id": score["user_id"],
        "beatmap_id": score.get("beatmap_id") or beatmap["id"],
        "beatmapset_id": beatmap.get("beatmapset_id"),
        "ruleset_id": score["ruleset_id"],
        "started_at": score.get("started_at"),
        "ended_at": score["ended_at"],
        "passed": int(bool(score.get("passed"))),
        "rank": score.get("rank"),
        "accuracy": score.get("accuracy"),
        "max_combo": score.get("max_combo"),
        "total_score": score.get("total_score"),
        "legacy_total_score": score.get("legacy_total_score"),
        "pp": score.get("pp"),
        "mods": "".join(m["acronym"] for m in mods),
        "mods_json": json.dumps(mods, separators=(",", ":")),
        "n_great": stats.get("great", 0),
        "n_ok": stats.get("ok", 0),
        "n_meh": stats.get("meh", 0),
        "n_miss": stats.get("miss", 0),
        "statistics_json": json.dumps(stats, separators=(",", ":")),
        "max_statistics_json": json.dumps(score.get("maximum_statistics"), separators=(",", ":")),
        "has_replay": int(bool(score.get("has_replay"))),
        "raw_json": json.dumps(score, separators=(",", ":")),
    }


def gravar_jogadas(con: sqlite3.Connection, scores: list[dict]) -> int:
    """Insere as jogadas ainda nao vistas. Devolve quantas entraram."""
    if not scores:
        return 0
    linhas = [_linha(s) for s in scores]
    colunas = list(linhas[0])
    sql = (
        f"INSERT OR IGNORE INTO jogadas ({', '.join(colunas)}) "
        f"VALUES ({', '.join(':' + c for c in colunas)})"
    )
    antes = con.total_changes
    con.executemany(sql, linhas)
    return con.total_changes - antes

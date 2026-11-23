"""Banco SQLite: jogadas, mapas, atributos por combinacao de mods e registro de cada coleta."""

import json
import sqlite3
from pathlib import Path

from . import mapas

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
    coletado_em         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    mods_chave          TEXT,                  -- mods que mudam a dificuldade (liga com mapas_mods)
    fracao_jogada       REAL,                  -- notas julgadas / notas do mapa (1 no pass; so osu! standard)
    combo_perfeito      INTEGER                -- is_perfect_combo (lazer) ou legacy_perfect (stable) da API
);
CREATE INDEX IF NOT EXISTS idx_jogadas_beatmap ON jogadas (beatmap_id);
CREATE INDEX IF NOT EXISTS idx_jogadas_ended ON jogadas (ended_at);

-- Dados do mapa sem mods, como vem junto com a jogada.
CREATE TABLE IF NOT EXISTS mapas (
    beatmap_id      INTEGER PRIMARY KEY,
    beatmapset_id   INTEGER,
    ruleset_id      INTEGER,                   -- modo do mapa (0 = osu! standard)
    artista         TEXT,
    titulo          TEXT,
    mapper          TEXT,
    versao          TEXT,                      -- nome da dificuldade
    status          TEXT,
    checksum        TEXT,                      -- md5 do .osu; muda quando o mapa e atualizado
    estrelas        REAL,                      -- SR sem mods
    bpm             REAL,
    total_length    INTEGER,                   -- segundos
    hit_length      INTEGER,                   -- segundos, sem pausas
    ar              REAL,
    od              REAL,
    cs              REAL,
    hp              REAL,
    count_circles   INTEGER,
    count_sliders   INTEGER,
    count_spinners  INTEGER,
    last_updated    TEXT
);

-- Cache dos atributos por mapa e combinacao de mods: uma chamada a API por linha, nunca repetida.
CREATE TABLE IF NOT EXISTS mapas_mods (
    beatmap_id      INTEGER NOT NULL,
    ruleset_id      INTEGER NOT NULL,
    mods_chave      TEXT NOT NULL,             -- '' = sem mods; ex.: 'HDHR', 'DT(speed_change=1.3)'
    mods_json       TEXT NOT NULL,
    taxa            REAL NOT NULL,             -- velocidade da jogada (1.5 no DT, 0.75 no HT)
    taxa_api        REAL NOT NULL,             -- velocidade que a API usou nos atributos (diferente em DT custom)
    bpm             REAL,                      -- ja com a velocidade
    total_length    REAL,                      -- segundos, ja com a velocidade
    hit_length      REAL,
    ar              REAL,                      -- com HR/EZ/DA e velocidade (so osu! standard)
    od              REAL,
    cs              REAL,
    hp              REAL,
    star_rating     REAL,
    max_combo       INTEGER,
    aim_difficulty  REAL,
    speed_difficulty REAL,
    speed_note_count REAL,
    slider_factor   REAL,
    flashlight_difficulty REAL,
    aim_difficult_slider_count REAL,
    aim_difficult_strain_count REAL,
    speed_difficult_strain_count REAL,
    atributos_json  TEXT,                      -- resposta completa da API
    erro            TEXT,                      -- preenchido se a API recusou (ex.: mapa apagado)
    buscado_em      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (beatmap_id, ruleset_id, mods_chave)
);

CREATE TABLE IF NOT EXISTS coletas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    recebidas   INTEGER,
    novas       INTEGER,
    erro        TEXT
);

-- Foto do perfil na primeira coleta de cada dia (dia no relogio deste PC).
CREATE TABLE IF NOT EXISTS perfil_diario (
    user_id         INTEGER NOT NULL,
    modo            TEXT NOT NULL,
    dia             TEXT NOT NULL,             -- AAAA-MM-DD
    coletado_em     TEXT NOT NULL,
    pp              REAL,
    rank_global     INTEGER,
    rank_pais       INTEGER,
    acuracia        REAL,                      -- 0 a 1
    play_count      INTEGER,
    ranked_score    INTEGER,
    nivel           REAL,                      -- nivel atual + progresso (ex.: 101.37)
    raw_json        TEXT,                      -- statistics completo da API
    PRIMARY KEY (user_id, modo, dia)
);

-- Rank global dia a dia: rank_history da API (90 dias) atualizado a cada foto.
CREATE TABLE IF NOT EXISTS rank_historico (
    user_id         INTEGER NOT NULL,
    modo            TEXT NOT NULL,
    dia             TEXT NOT NULL,
    rank_global     INTEGER,                   -- NULL = sem rank naquele dia
    importado_em    TEXT NOT NULL,
    PRIMARY KEY (user_id, modo, dia)
);

-- Features do .osu de cada mapa (arquivo_osu.features), na versao do extrator que as calculou.
CREATE TABLE IF NOT EXISTS mapas_osu (
    beatmap_id      INTEGER PRIMARY KEY,
    checksum        TEXT,                      -- checksum do mapa na API quando o .osu foi lido
    versao          INTEGER NOT NULL,
    features_json   TEXT,
    erro            TEXT,
    lido_em         TEXT NOT NULL
);

-- Tipo do mapa marcado pelo jogador: vale acima do classificador. Uma linha por tipo (1 = sim, 0 = nao).
CREATE TABLE IF NOT EXISTS marcacoes (
    beatmap_id      INTEGER NOT NULL,
    tipo            TEXT NOT NULL,             -- stream, jump, tech, alternate
    valor           INTEGER NOT NULL,
    marcado_em      TEXT NOT NULL,
    PRIMARY KEY (beatmap_id, tipo)
);

-- Modelo treinado com as marcacoes, por tipo (so existe com marcacoes suficientes).
CREATE TABLE IF NOT EXISTS modelos (
    tipo            TEXT PRIMARY KEY,
    modelo_json     TEXT NOT NULL,             -- medias, desvios, pesos e intercepto
    features_json   TEXT NOT NULL,             -- ordem das features dos pesos
    positivos       INTEGER NOT NULL,
    negativos       INTEGER NOT NULL,
    precisao_cv     REAL,                      -- acuracia balanceada na validacao cruzada
    precisao_regra  REAL,                      -- a da regra nas mesmas marcacoes, para comparar
    assinatura      TEXT NOT NULL,             -- marcacoes usadas: muda -> treina de novo
    treinado_em     TEXT NOT NULL
);
"""


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    try:
        migrar(con)
    except BaseException:
        con.close()
        raise
    return con


def _colunas_do_schema() -> dict[str, list[tuple[str, str]]]:
    """Tabela -> (coluna, tipo) como o SCHEMA atual define, lido de um banco em memoria."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA)
        tabelas = [r[0] for r in ref.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: [(r[1], r[2]) for r in ref.execute(f"PRAGMA table_info({t})")] for t in tabelas}
    finally:
        ref.close()


def _faltando(con: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """Colunas do SCHEMA atual que nao existem no banco (tabela inteira ausente nao entra: o SCHEMA cria)."""
    faltando = {}
    for tabela, colunas in _colunas_do_schema().items():
        existentes = {r[1] for r in con.execute(f"PRAGMA table_info({tabela})")}
        if existentes and (falta := [c for c in colunas if c[0] not in existentes]):
            faltando[tabela] = falta
    return faltando


def precisa_migrar(con: sqlite3.Connection) -> bool:
    """True se o banco veio de uma versao anterior e falta tabela, coluna ou preenchimento. So le."""
    tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if set(_colunas_do_schema()) - tabelas or _faltando(con):
        return True
    return con.execute(
        "SELECT 1 FROM jogadas WHERE (mods_chave IS NULL OR combo_perfeito IS NULL) AND raw_json IS NOT NULL LIMIT 1"
    ).fetchone() is not None


def migrar(con: sqlite3.Connection) -> None:
    """Leva um banco de qualquer versao anterior ao schema atual, sem perder nada. Pode rodar quantas vezes quiser."""
    with con:
        # Coluna nova entra sem NOT NULL nem DEFAULT calculado, que o ALTER TABLE do SQLite nao aceita.
        for tabela, colunas in _faltando(con).items():
            for nome, tipo in colunas:
                con.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
    con.executescript(SCHEMA)  # depois das colunas: tabelas que faltam e indices
    with con:
        con.execute("CREATE INDEX IF NOT EXISTS idx_jogadas_mapa_mods ON jogadas (beatmap_id, ruleset_id, mods_chave)")
        # Jogadas gravadas por versoes anteriores: tudo sai do raw_json, sem chamar a API.
        antigas = con.execute(
            "SELECT raw_json, mods_chave IS NULL FROM jogadas "
            "WHERE (mods_chave IS NULL OR combo_perfeito IS NULL) AND raw_json IS NOT NULL"
        ).fetchall()
        if antigas:
            scores = [json.loads(r[0]) for r in antigas]
            sem_mapa = [s for s, (_, falta_chave) in zip(scores, antigas) if falta_chave and s.get("beatmap")]
            mapas.gravar_mapas(con, [_linha_mapa(s) for s in sem_mapa])
            con.executemany(
                "UPDATE jogadas SET mods_chave = :mods_chave, fracao_jogada = :fracao_jogada, "
                "combo_perfeito = :combo_perfeito WHERE score_id = :score_id",
                [_linha(s) for s in scores],
            )


def _linha_mapa(score: dict) -> dict:
    return mapas.linha_mapa(score["beatmap"], score.get("beatmapset"))


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
        "mods_chave": mapas.chave_mods(mods),
        "fracao_jogada": mapas.fracao_jogada(stats, beatmap, score["ruleset_id"]) if beatmap else None,
        "combo_perfeito": int(bool(score.get("is_perfect_combo") or score.get("legacy_perfect"))),
    }


def gravar_jogadas(con: sqlite3.Connection, scores: list[dict]) -> int:
    """Insere as jogadas ainda nao vistas e atualiza os dados dos mapas delas. Devolve quantas entraram."""
    if not scores:
        return 0
    mapas.gravar_mapas(con, list({s["beatmap"]["id"]: _linha_mapa(s) for s in scores if s.get("beatmap")}.values()))
    linhas = [_linha(s) for s in scores]
    colunas = list(linhas[0])
    sql = (
        f"INSERT OR IGNORE INTO jogadas ({', '.join(colunas)}) "
        f"VALUES ({', '.join(':' + c for c in colunas)})"
    )
    antes = con.total_changes
    con.executemany(sql, linhas)
    return con.total_changes - antes

"""Foto diaria do perfil: pp, ranks, acc, play count, ranked score e nivel, uma vez por dia.

Vem de GET /users/{id}/{modo}, a mesma chamada que traz rank_history: o rank global dos
ultimos 90 dias, do mais antigo para o mais recente (o ultimo e o de hoje; 0 = sem rank).
Esse historico entra numa tabela propria para o grafico de rank nao comecar vazio.
O dia e o do relogio deste PC, o mesmo que o painel usa.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone


def _agora_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tem_foto(con: sqlite3.Connection, user_id: int, modo: str, dia: date) -> bool:
    return con.execute(
        "SELECT 1 FROM perfil_diario WHERE user_id = ? AND modo = ? AND dia = ?", (user_id, modo, dia.isoformat())
    ).fetchone() is not None


def _acuracia(estatisticas: dict) -> float | None:
    """0 a 1. A API manda hit_accuracy em porcentagem (0 a 100); accuracy, quando vem, pode estar em 0 a 1."""
    for campo in ("accuracy", "hit_accuracy"):
        valor = estatisticas.get(campo)
        if valor is not None:
            valor = float(valor)
            return round(valor / 100 if valor > 1 else valor, 6)
    return None


def _nivel(estatisticas: dict) -> float | None:
    nivel = estatisticas.get("level") or {}
    if nivel.get("current") is None:
        return None
    return round(nivel["current"] + (nivel.get("progress") or 0) / 100, 2)


def linha_foto(usuario: dict, modo: str, dia: date) -> dict:
    est = usuario.get("statistics") or {}
    return {
        "user_id": usuario["id"],
        "modo": modo,
        "dia": dia.isoformat(),
        "coletado_em": _agora_utc(),
        "pp": est.get("pp"),
        "rank_global": est.get("global_rank"),
        "rank_pais": est.get("country_rank"),
        "acuracia": _acuracia(est),
        "play_count": est.get("play_count"),
        "ranked_score": est.get("ranked_score"),
        "nivel": _nivel(est),
        "raw_json": json.dumps(est, separators=(",", ":")),
    }


def historico_rank(usuario: dict, dia: date) -> list[tuple[str, int | None]]:
    """(dia, rank) dos ultimos 90 dias de rank_history; o ultimo valor e o de `dia`."""
    dados = (usuario.get("rank_history") or {}).get("data") or []
    n = len(dados)
    return [((dia - timedelta(days=n - 1 - i)).isoformat(), int(r) if r else None) for i, r in enumerate(dados)]


def gravar(con: sqlite3.Connection, usuario: dict, modo: str, dia: date) -> None:
    """Grava a foto do dia (se ja houver, fica a primeira) e atualiza o historico de rank."""
    linha = linha_foto(usuario, modo, dia)
    colunas = list(linha)
    con.execute(
        f"INSERT OR IGNORE INTO perfil_diario ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)})",
        linha,
    )
    agora = _agora_utc()
    con.executemany(
        "INSERT INTO rank_historico (user_id, modo, dia, rank_global, importado_em) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id, modo, dia) DO UPDATE SET rank_global = excluded.rank_global, importado_em = excluded.importado_em",
        [(usuario["id"], modo, d, r, agora) for d, r in historico_rank(usuario, dia)],
    )


def registrar_se_preciso(con: sqlite3.Connection, api, user_id: int, modo: str, hoje: date | None = None) -> bool:
    """Na primeira coleta do dia busca o perfil e grava a foto. Devolve True se gravou."""
    hoje = hoje or datetime.now().astimezone().date()
    if tem_foto(con, user_id, modo, hoje):
        return False
    usuario = api.buscar_usuario(str(user_id), modo)
    with con:
        gravar(con, usuario, modo, hoje)
    return True

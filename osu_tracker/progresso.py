"""Aba Progresso do painel: perfil dia a dia, teto de estrelas, aim, speed e acc por semana e skillsets.

Variacao em 7 e 30 dias:
- perfil (pp, rank, acc...): valor da ultima foto menos o da foto de N dias antes (ou a mais
  proxima anterior a isso); sem foto tao antiga, fica sem variacao;
- jogadas (estrelas, aim, speed, acc, skillsets): ultimos N dias contra os N dias anteriores.
Semana = de segunda a domingo, no fuso deste PC.
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import analise, skillsets
from .analise import Jogada, _arredondar, _local, _soma

CAMPOS_PERFIL = ("pp", "rank_global", "rank_pais", "acuracia", "play_count", "ranked_score", "nivel")
JANELAS = (7, 30)


# --- perfil ---

def _jogador(con: sqlite3.Connection) -> tuple[int, str] | None:
    linha = con.execute("SELECT user_id, modo FROM perfil_diario ORDER BY dia DESC, coletado_em DESC LIMIT 1").fetchone()
    return tuple(linha) if linha else None


def perfil(con: sqlite3.Connection) -> dict:
    jogador = _jogador(con)
    if jogador is None:
        return {"fotos": [], "rank": [], "atual": None, "variacao": {}}
    con.row_factory = sqlite3.Row
    fotos = [dict(r) for r in con.execute(
        f"SELECT dia, {', '.join(CAMPOS_PERFIL)} FROM perfil_diario WHERE user_id = ? AND modo = ? ORDER BY dia",
        jogador)]
    rank = {r["dia"]: r["rank_global"] for r in con.execute(
        "SELECT dia, rank_global FROM rank_historico WHERE user_id = ? AND modo = ? ORDER BY dia", jogador)}
    for f in fotos:  # a foto do dia vale mais que o historico importado
        if f["rank_global"] is not None:
            rank[f["dia"]] = f["rank_global"]
    serie_rank = [{"dia": d, "rank_global": r} for d, r in sorted(rank.items()) if r is not None]
    atual = fotos[-1] if fotos else None

    variacao = {}
    if atual:
        hoje = date.fromisoformat(atual["dia"])
        for n in JANELAS:
            alvo = (hoje - timedelta(days=n)).isoformat()
            antes = next((f for f in reversed(fotos) if f["dia"] <= alvo), None)
            antes_rank = next((p for p in reversed(serie_rank) if p["dia"] <= alvo), None)
            v = {}
            for campo in CAMPOS_PERFIL:
                if campo == "rank_global":
                    ref = antes_rank["rank_global"] if antes_rank else None
                else:
                    ref = antes[campo] if antes else None
                v[campo] = _diferenca(atual[campo], ref)
            variacao[str(n)] = v
    return {"fotos": fotos, "rank": serie_rank, "atual": atual, "variacao": variacao}


def _diferenca(agora, antes):
    if agora is None or antes is None:
        return None
    return round(agora - antes, 6)


# --- semanas ---

def _segunda(iso_utc: str) -> str:
    d = _local(iso_utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _maior(jogadas: list[Jogada], valor, filtro) -> dict | None:
    """Jogada com o maior `valor` entre as que passam no filtro: valor e o mapa, para o detalhe."""
    candidatas = [j for j in jogadas if filtro(j) and valor(j) is not None]
    if not candidatas:
        return None
    j = max(candidatas, key=valor)
    return {"valor": _arredondar(valor(j)), "mapa": j.nome_mapa, "mods": analise.nome_mods(j.mods), "quando": j.ended_at}


def _passou(j: Jogada) -> bool:
    return j.passou


def _fc(j: Jogada) -> bool:
    return j.fc


def _qualquer(j: Jogada) -> bool:
    return True


def _resumo_tetos(jogadas: list[Jogada]) -> dict:
    s = _soma(jogadas)
    ods = [j.od for j in jogadas if j.od is not None]
    return {
        "jogadas": s["jogadas"], "passes": s["passes"], "fcs": sum(j.fc for j in jogadas),
        "estrelas_pass": _maior(jogadas, lambda j: j.estrelas, _passou),
        "estrelas_fc": _maior(jogadas, lambda j: j.estrelas, _fc),
        "estrelas_tentada": _maior(jogadas, lambda j: j.estrelas, _qualquer),
        "aim_pass": _maior(jogadas, lambda j: j.aim, _passou),
        "aim_fc": _maior(jogadas, lambda j: j.aim, _fc),
        "speed_pass": _maior(jogadas, lambda j: j.speed, _passou),
        "speed_fc": _maior(jogadas, lambda j: j.speed, _fc),
        "acc": s["acc"], "acc_acertadas": s["acc_acertadas"], "misses_100": s["misses_100"],
        "od_medio": _arredondar(sum(ods) / len(ods)) if ods else None,
        "maior_pp": _maior(jogadas, lambda j: j.pp, _passou),
    }


def por_semana(jogadas: list[Jogada]) -> list[dict]:
    semanas: dict[str, list[Jogada]] = {}
    for j in jogadas:
        semanas.setdefault(_segunda(j.ended_at), []).append(j)
    return [{"semana": s, **_resumo_tetos(g)} for s, g in sorted(semanas.items())]


def _janelas(jogadas: list[Jogada], agora: datetime) -> dict:
    """Para 7 e 30 dias: o resumo dos ultimos N dias e o dos N dias anteriores."""
    saida = {}
    for n in JANELAS:
        fim = agora
        meio = agora - timedelta(days=n)
        inicio = agora - timedelta(days=2 * n)
        quando = lambda j: datetime.fromisoformat(j.ended_at.replace("Z", "+00:00"))  # noqa: E731
        recente = [j for j in jogadas if meio < quando(j) <= fim]
        anterior = [j for j in jogadas if inicio < quando(j) <= meio]
        saida[str(n)] = {"recente": _resumo_tetos(recente), "anterior": _resumo_tetos(anterior)}
    return saida


# --- skillsets ---

def _nome_mapa(con: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    con.row_factory = sqlite3.Row
    saida = {}
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        for r in con.execute(
                f"SELECT beatmap_id, artista, titulo, versao, estrelas FROM mapas WHERE beatmap_id IN ({','.join('?' * len(lote))})",
                lote):
            saida[r["beatmap_id"]] = {"mapa": analise._nome_mapa(r), "estrelas": _arredondar(r["estrelas"])}
    return saida


def skillsets_progresso(con: sqlite3.Connection, jogadas: list[Jogada], agora: datetime) -> dict:
    classes = skillsets.classificar(con)
    por_tipo = []
    for tipo in skillsets.TIPOS:
        do_tipo = [j for j in jogadas if tipo in classes.get(j.beatmap_id, {}).get("tipos", [])]
        por_tipo.append({
            "tipo": tipo, "nome": skillsets.NOMES_TIPOS[tipo],
            "mapas": len({b for b, c in classes.items() if tipo in c["tipos"]}),
            "semanas": por_semana(do_tipo),
            "janelas": _janelas(do_tipo, agora),
            "total": _resumo_tetos(do_tipo),
        })

    jogados = {j.beatmap_id for j in jogadas}
    sem_features = con.execute(
        "SELECT COUNT(*) FROM mapas m LEFT JOIN mapas_osu o ON o.beatmap_id = m.beatmap_id "
        "WHERE m.ruleset_id = 0 AND o.features_json IS NULL AND m.beatmap_id IN (SELECT beatmap_id FROM jogadas)"
    ).fetchone()[0]
    nomes = _nome_mapa(con, sorted(set(classes) | jogados))
    ultima = {}
    for j in jogadas:
        ultima[j.beatmap_id] = j.ended_at

    def item(bid: int) -> dict:
        c = classes.get(bid, {"tipos": [], "por_tipo": {}, "marcado": False})
        return {"beatmap_id": bid, **nomes.get(bid, {"mapa": f"Mapa {bid}", "estrelas": None}),
                "tipos": c["tipos"], "por_tipo": c["por_tipo"], "marcado": c["marcado"], "ultima": ultima.get(bid)}

    mapas = sorted((item(b) for b in classes if b in jogados or classes[b]["marcado"]),
                   key=lambda m: m["ultima"] or "", reverse=True)
    marcar = [{**item(bid), "duvida_tipo": tipo, "duvida_confianca": conf}
              for bid, tipo, conf in skillsets.para_marcar(classes)]
    return {
        "tipos": por_tipo,
        "classificador": skillsets.situacao(con),
        "para_marcar": marcar,
        "mapas": mapas,
        "sem_features": sem_features,
        "regras": {"minimo_por_classe": skillsets.MINIMO_POR_CLASSE, "precisao_baixa": skillsets.PRECISAO_BAIXA},
    }


def progresso(con: sqlite3.Connection, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    jogadas = analise.carregar(con)
    return {
        "perfil": perfil(con),
        "semanas": por_semana(jogadas),
        "janelas": _janelas(jogadas, agora),
        "skillsets": skillsets_progresso(con, jogadas, agora),
    }

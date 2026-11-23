"""Enriquecimento dos mapas: atributos de dificuldade com os mods de cada jogada.

Dados do proprio mapa (BPM, duracao, AR, OD, CS, HP, contagem de notas) vem junto com a jogada
na API e vao para a tabela mapas sem custo extra.
Os atributos de dificuldade (SR, aim, speed...) vem de POST /beatmaps/{id}/attributes, uma chamada
por mapa e combinacao de mods, guardada em mapas_mods para nunca repetir.

O que a API faz e o que fica por nossa conta (conferido contra a API real em 23/11/2026):
- a resposta nao traz AR nem OD; eles sao calculados aqui com HR, EZ, Difficulty Adjust e a velocidade;
- a API ignora a velocidade customizada do DT/NC/HT/DC (ex.: DT 1.3x e calculado como 1.5x);
  BPM, duracao, AR e OD usam a velocidade real da jogada, e taxa_api registra a que a API usou;
- NF, SD, PF e CL nao mudam a dificuldade, entao nao entram na chave do cache.
"""

import json
import logging
import sqlite3

from .api import ApiError, RespostaRecusada

log = logging.getLogger("osu_tracker")

# Mods que nao mudam nenhum atributo de dificuldade: ficam fora da chave e da chamada.
SEM_EFEITO = frozenset({"NF", "SD", "PF", "CL"})
# Velocidade padrao de cada mod de velocidade (a que a API sempre usa).
TAXA_PADRAO = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}
# Ajustes do Difficulty Adjust que mudam AR, OD, CS e HP.
AJUSTES_DA = ("approach_rate", "overall_difficulty", "circle_size", "drain_rate")
# Quantas chamadas de atributos uma coleta faz no maximo (1 por segundo, entao ~50 s).
LIMITE_POR_COLETA = 50

OSU = 0  # ruleset osu! standard


def _numero(valor) -> str:
    return f"{float(valor):g}"


def mods_relevantes(mods: list[dict]) -> list[dict]:
    """Mods que mudam a dificuldade, em ordem alfabetica, so com os ajustes que importam."""
    saida = []
    for mod in sorted(mods, key=lambda m: m["acronym"]):
        sigla = mod["acronym"]
        if sigla in SEM_EFEITO:
            continue
        ajustes = mod.get("settings") or {}
        if sigla in TAXA_PADRAO:
            usados = {"speed_change": ajustes["speed_change"]} if "speed_change" in ajustes else {}
        elif sigla == "DA":
            usados = {k: ajustes[k] for k in AJUSTES_DA if k in ajustes}
        else:
            usados = {}
        saida.append({"acronym": sigla, "settings": usados} if usados else {"acronym": sigla})
    return saida


def chave_mods(mods: list[dict]) -> str:
    """Chave do cache: ex. '' (sem mods), 'HDHR', 'DT(speed_change=1.3)HD'."""
    partes = []
    for mod in mods_relevantes(mods):
        ajustes = mod.get("settings") or {}
        texto = ",".join(f"{k}={_numero(v)}" for k, v in sorted(ajustes.items()))
        partes.append(mod["acronym"] + (f"({texto})" if texto else ""))
    return "".join(partes)


def _taxas(mods: list[dict]) -> tuple[float, float]:
    """(velocidade real da jogada, velocidade que a API usa para os atributos)."""
    for mod in mods:
        sigla = mod["acronym"]
        if sigla in TAXA_PADRAO:
            real = float((mod.get("settings") or {}).get("speed_change", TAXA_PADRAO[sigla]))
            return real, TAXA_PADRAO[sigla]
    return 1.0, 1.0


def _ar_para_ms(ar: float) -> float:
    return 1800 - 120 * ar if ar < 5 else 1200 - 150 * (ar - 5)


def _ms_para_ar(ms: float) -> float:
    return (1800 - ms) / 120 if ms > 1200 else 5 + (1200 - ms) / 150


def ajustar(mapa: dict, mods: list[dict]) -> dict:
    """Valores do mapa com os mods da jogada: BPM, duracao e, no osu! standard, AR, OD, CS e HP."""
    taxa, taxa_api = _taxas(mods)
    siglas = {m["acronym"] for m in mods}
    saida = {
        "taxa": taxa,
        "taxa_api": taxa_api,
        "bpm": mapa["bpm"] * taxa if mapa.get("bpm") is not None else None,
        "total_length": mapa["total_length"] / taxa if mapa.get("total_length") is not None else None,
        "hit_length": mapa["hit_length"] / taxa if mapa.get("hit_length") is not None else None,
        "ar": None, "od": None, "cs": None, "hp": None,
    }
    if mapa.get("ruleset_id") != OSU:
        return saida

    da = next((m.get("settings") or {} for m in mods if m["acronym"] == "DA"), {})
    valores = {
        "ar": da.get("approach_rate", mapa["ar"]),
        "od": da.get("overall_difficulty", mapa["od"]),
        "cs": da.get("circle_size", mapa["cs"]),
        "hp": da.get("drain_rate", mapa["hp"]),
    }
    if any(v is None for v in valores.values()):
        return saida
    if "HR" in siglas:
        valores = {k: min(v * (1.3 if k == "cs" else 1.4), 10.0) for k, v in valores.items()}
    elif "EZ" in siglas:
        valores = {k: v * 0.5 for k, v in valores.items()}
    # A velocidade encurta o tempo de aproximacao e a janela do 300: AR e OD efetivos sobem ou descem.
    valores["ar"] = _ms_para_ar(_ar_para_ms(valores["ar"]) / taxa)
    valores["od"] = (80 - (80 - 6 * valores["od"]) / taxa) / 6
    saida.update({k: round(v, 2) for k, v in valores.items()})
    return saida


def fracao_jogada(estatisticas: dict, mapa: dict, ruleset_id: int) -> float | None:
    """Fracao das notas do mapa que foram julgadas (1.0 num pass; num fail, ate onde chegou).

    Cada circulo, slider e spinner gera exatamente um julgamento great, ok, meh ou miss,
    tanto no stable quanto no lazer; ticks e pontas de slider ficam em outros contadores.
    So no osu! standard: nos outros modos a contagem do mapa nao bate com esses contadores.
    """
    if ruleset_id != OSU:
        return None
    total = sum(mapa.get(k) or 0 for k in ("count_circles", "count_sliders", "count_spinners"))
    if not total:
        return None
    julgadas = sum(estatisticas.get(k, 0) for k in ("great", "ok", "meh", "miss"))
    return round(min(julgadas / total, 1.0), 4)


def linha_mapa(beatmap: dict, beatmapset: dict | None) -> dict:
    """Linha da tabela mapas a partir do beatmap que vem dentro da jogada."""
    conjunto = beatmapset or {}
    return {
        "beatmap_id": beatmap["id"],
        "beatmapset_id": beatmap.get("beatmapset_id"),
        "ruleset_id": beatmap.get("mode_int"),
        "artista": conjunto.get("artist"),
        "titulo": conjunto.get("title"),
        "mapper": conjunto.get("creator"),
        "versao": beatmap.get("version"),
        "status": beatmap.get("status"),
        "checksum": beatmap.get("checksum"),
        "estrelas": beatmap.get("difficulty_rating"),
        "bpm": beatmap.get("bpm"),
        "total_length": beatmap.get("total_length"),
        "hit_length": beatmap.get("hit_length"),
        "ar": beatmap.get("ar"),
        "od": beatmap.get("accuracy"),
        "cs": beatmap.get("cs"),
        "hp": beatmap.get("drain"),
        "count_circles": beatmap.get("count_circles"),
        "count_sliders": beatmap.get("count_sliders"),
        "count_spinners": beatmap.get("count_spinners"),
        "last_updated": beatmap.get("last_updated"),
    }


def gravar_mapas(con: sqlite3.Connection, linhas: list[dict]) -> None:
    """Insere ou atualiza os mapas. Se o mapa mudou (checksum novo), descarta os atributos guardados."""
    for linha in linhas:
        antigo = con.execute("SELECT checksum FROM mapas WHERE beatmap_id = ?", (linha["beatmap_id"],)).fetchone()
        if antigo and linha["checksum"] and antigo[0] != linha["checksum"]:
            log.info("Mapa %s foi atualizado: atributos serao buscados de novo", linha["beatmap_id"])
            con.execute("DELETE FROM mapas_mods WHERE beatmap_id = ?", (linha["beatmap_id"],))
        colunas = list(linha)
        con.execute(
            f"INSERT INTO mapas ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)}) "
            f"ON CONFLICT (beatmap_id) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in colunas if c != "beatmap_id"),
            linha,
        )


# Atributos da resposta que viram coluna; o resto fica em atributos_json.
COLUNAS_ATRIBUTOS = (
    "star_rating", "max_combo", "aim_difficulty", "speed_difficulty", "speed_note_count",
    "slider_factor", "flashlight_difficulty", "aim_difficult_slider_count",
    "aim_difficult_strain_count", "speed_difficult_strain_count",
)


def pendentes(con: sqlite3.Connection) -> list[tuple[int, int, str, str]]:
    """Mapa e combinacao de mods das jogadas que ainda nao tem atributos, da mais recente para a mais antiga.

    Devolve (beatmap_id, ruleset_id, mods_chave, mods_json de uma jogada com essa chave).
    """
    return con.execute(
        """
        SELECT j.beatmap_id, j.ruleset_id, j.mods_chave, j.mods_json
        FROM jogadas j
        JOIN mapas m ON m.beatmap_id = j.beatmap_id
        LEFT JOIN mapas_mods mm
               ON mm.beatmap_id = j.beatmap_id AND mm.ruleset_id = j.ruleset_id AND mm.mods_chave = j.mods_chave
        WHERE mm.beatmap_id IS NULL
        GROUP BY j.beatmap_id, j.ruleset_id, j.mods_chave
        ORDER BY MAX(j.ended_at) DESC
        """
    ).fetchall()


def _gravar_atributos(con, beatmap_id, ruleset_id, chave, mods, atributos, erro) -> None:
    mapa = dict(zip(
        ("bpm", "total_length", "hit_length", "ar", "od", "cs", "hp", "ruleset_id"),
        con.execute(
            "SELECT bpm, total_length, hit_length, ar, od, cs, hp, ruleset_id FROM mapas WHERE beatmap_id = ?",
            (beatmap_id,),
        ).fetchone(),
    ))
    # AR, OD e CS do mapa so valem no modo dele; num convert para outro modo ficam NULL.
    mapa["ruleset_id"] = ruleset_id if mapa["ruleset_id"] == ruleset_id else None
    linha = {
        "beatmap_id": beatmap_id,
        "ruleset_id": ruleset_id,
        "mods_chave": chave,
        "mods_json": json.dumps(mods_relevantes(mods), separators=(",", ":")),
        **ajustar(mapa, mods),
        **{c: (atributos or {}).get(c) for c in COLUNAS_ATRIBUTOS},
        "atributos_json": json.dumps(atributos, separators=(",", ":")) if atributos is not None else None,
        "erro": erro,
    }
    colunas = list(linha)
    con.execute(
        f"INSERT OR REPLACE INTO mapas_mods ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)})",
        linha,
    )


def enriquecer(con: sqlite3.Connection, api, limite: int = LIMITE_POR_COLETA) -> tuple[int, int]:
    """Busca os atributos que faltam, ate `limite` chamadas. Devolve (buscados agora, ainda pendentes).

    Mapa que a API recusa (apagado, modo invalido) fica gravado com o erro e nao e tentado de novo.
    Falha de rede ou do servidor interrompe e o resto fica para a proxima coleta.
    """
    fila = pendentes(con)
    feitos = 0
    for beatmap_id, ruleset_id, chave, mods_json in fila[:limite]:
        mods = json.loads(mods_json)
        siglas = [m["acronym"] for m in mods_relevantes(mods)]
        try:
            atributos, erro = api.atributos_dificuldade(beatmap_id, siglas, ruleset_id), None
        except RespostaRecusada as e:
            atributos, erro = None, str(e)
            log.warning("Atributos do mapa %s (%s) recusados: %s", beatmap_id, chave or "sem mods", e)
        except ApiError as e:
            log.warning("Enriquecimento interrompido, continua na proxima coleta: %s", e)
            break
        with con:
            _gravar_atributos(con, beatmap_id, ruleset_id, chave, mods, atributos, erro)
        feitos += 1
    return feitos, len(fila) - feitos

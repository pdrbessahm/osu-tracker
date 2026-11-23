"""Consultas do painel: transforma as jogadas do banco em numeros para decidir o que treinar.

Quase toda jogada de quem esta subindo de nivel e fail, entao nada aqui depende de passar o mapa:
- misses por 100 notas = misses / notas julgadas (300, 100, 50 e miss) do trecho que foi jogado;
- fracao jogada = ate onde a jogada chegou no mapa (1 num pass);
- acc = (300 + 100/3 + 50/6) / notas julgadas, a formula do stable, sobre o trecho jogado;
- acc so das acertadas = a mesma conta sem os misses, para separar timing de mira.
Em grupos (faixa de estrelas, mapa, dia), os totais sao somados antes de dividir, entao uma
tentativa de 30 notas pesa menos que uma de 500.

So osu! standard: e o unico modo em que a fracao jogada e as contagens de notas batem.
"""

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

OSU = 0
LARGURA_FAIXA = 0.5          # estrelas
AMOSTRA_MINIMA_SUGESTAO = 100  # notas julgadas no mapa, somando as tentativas, para entrar nas sugestoes
LIMITE_SUGESTOES = 10
MUDANCA_TENDENCIA = 0.10     # 10% para mais ou para menos conta como subindo ou caindo

CONSULTA = """
SELECT j.score_id, j.beatmap_id, j.ended_at, j.passed, j.mods_chave, j.accuracy,
       j.n_great, j.n_ok, j.n_meh, j.n_miss, j.fracao_jogada, j.pp, j.combo_perfeito,
       COALESCE(mm.star_rating, m.estrelas) AS estrelas,
       mm.aim_difficulty AS aim, mm.speed_difficulty AS speed, COALESCE(mm.od, m.od) AS od,
       m.artista, m.titulo, m.versao,
       COALESCE(m.count_circles, 0) + COALESCE(m.count_sliders, 0) + COALESCE(m.count_spinners, 0) AS notas_mapa
FROM jogadas j
LEFT JOIN mapas m ON m.beatmap_id = j.beatmap_id
LEFT JOIN mapas_mods mm
       ON mm.beatmap_id = j.beatmap_id AND mm.ruleset_id = j.ruleset_id AND mm.mods_chave = j.mods_chave
WHERE j.ruleset_id = ?
"""


@dataclass(frozen=True)
class Jogada:
    score_id: int
    beatmap_id: int
    ended_at: str
    passou: bool
    mods: str
    n300: int
    n100: int
    n50: int
    misses: int
    fracao: float | None
    estrelas: float | None
    aim: float | None
    speed: float | None
    nome_mapa: str
    notas_mapa: int
    pp: float | None = None
    od: float | None = None
    combo_perfeito: bool = False

    @property
    def julgadas(self) -> int:
        return self.n300 + self.n100 + self.n50 + self.misses

    @property
    def fc(self) -> bool:
        """FC como o jogo mostra: passou, sem miss e sem quebrar o combo (slider break)."""
        return self.passou and self.misses == 0 and self.combo_perfeito


def nome_mods(chave: str) -> str:
    return chave or "Sem mods"


def _nome_mapa(linha: sqlite3.Row) -> str:
    if not linha["titulo"]:
        return f"Mapa {linha['beatmap_id']}"
    artista = f"{linha['artista']} - " if linha["artista"] else ""
    versao = f" [{linha['versao']}]" if linha["versao"] else ""
    return f"{artista}{linha['titulo']}{versao}"


def carregar(con: sqlite3.Connection, dias: int | None = None, mods: str | None = None,
             agora: datetime | None = None) -> list[Jogada]:
    """Jogadas de osu! standard em ordem de tempo. dias=None: todas; mods=None: todas, '' = sem mods."""
    sql, parametros = CONSULTA, [OSU]
    if dias is not None:
        corte = (agora or datetime.now(timezone.utc)) - timedelta(days=dias)
        sql += " AND j.ended_at >= ?"
        parametros.append(corte.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if mods is not None:
        sql += " AND j.mods_chave = ?"
        parametros.append(mods)
    sql += " ORDER BY j.ended_at, j.score_id"
    con.row_factory = sqlite3.Row
    return [
        Jogada(
            score_id=r["score_id"], beatmap_id=r["beatmap_id"], ended_at=r["ended_at"], passou=bool(r["passed"]),
            mods=r["mods_chave"] or "", n300=r["n_great"], n100=r["n_ok"], n50=r["n_meh"], misses=r["n_miss"],
            fracao=r["fracao_jogada"], estrelas=r["estrelas"], aim=r["aim"], speed=r["speed"],
            nome_mapa=_nome_mapa(r), notas_mapa=r["notas_mapa"],
            pp=r["pp"], od=r["od"], combo_perfeito=bool(r["combo_perfeito"]),
        )
        for r in con.execute(sql, parametros)
    ]


def opcoes_mods(con: sqlite3.Connection) -> list[dict]:
    """Combinacoes de mods que aparecem nas jogadas, da mais jogada para a menos."""
    linhas = con.execute(
        "SELECT COALESCE(mods_chave, ''), COUNT(*) FROM jogadas WHERE ruleset_id = ? GROUP BY 1 ORDER BY 2 DESC, 1",
        (OSU,),
    ).fetchall()
    return [{"valor": chave, "nome": nome_mods(chave), "jogadas": n} for chave, n in linhas]


# --- contas sobre um grupo de jogadas ---

def _soma(jogadas: list[Jogada]) -> dict:
    n300 = sum(j.n300 for j in jogadas)
    n100 = sum(j.n100 for j in jogadas)
    n50 = sum(j.n50 for j in jogadas)
    misses = sum(j.misses for j in jogadas)
    julgadas = n300 + n100 + n50 + misses
    acertadas = julgadas - misses
    pontos = 300 * n300 + 100 * n100 + 50 * n50
    fracoes = [j.fracao for j in jogadas if j.fracao is not None]
    return {
        "jogadas": len(jogadas),
        "passes": sum(j.passou for j in jogadas),
        "notas": julgadas,
        "misses": misses,
        "misses_100": round(100 * misses / julgadas, 2) if julgadas else None,
        "acc": round(pontos / (300 * julgadas), 4) if julgadas else None,
        "acc_acertadas": round(pontos / (300 * acertadas), 4) if acertadas else None,
        "fracao_media": round(statistics.fmean(fracoes), 4) if fracoes else None,
    }


def _local(iso_utc: str) -> datetime:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone()


def _tendencia(tentativas: list[Jogada]) -> dict:
    """Compara misses por 100 notas da primeira metade das tentativas com a segunda."""
    meio = len(tentativas) // 2
    antes = _soma(tentativas[:meio])["misses_100"]
    depois = _soma(tentativas[len(tentativas) - meio:])["misses_100"]
    if antes is None or depois is None:
        sentido = "sem dados"
    elif depois < antes * (1 - MUDANCA_TENDENCIA):
        sentido = "caindo"
    elif depois > antes * (1 + MUDANCA_TENDENCIA):
        sentido = "subindo"
    else:
        sentido = "estável"
    return {"antes": antes, "depois": depois, "sentido": sentido}


def _ponto(j: Jogada) -> dict:
    s = _soma([j])
    return {
        "quando": j.ended_at, "misses": j.misses, "notas": s["notas"], "misses_100": s["misses_100"],
        "fracao": j.fracao, "acc": s["acc"], "acc_acertadas": s["acc_acertadas"], "passou": j.passou,
        "mapa": j.nome_mapa, "mods": nome_mods(j.mods), "estrelas": _arredondar(j.estrelas),
    }


def _arredondar(valor: float | None, casas: int = 2) -> float | None:
    return round(valor, casas) if valor is not None else None


# --- secoes do painel ---

def por_dia(jogadas: list[Jogada]) -> list[dict]:
    """Todos os mapas juntos, um ponto por dia (no fuso deste PC)."""
    dias: dict[str, list[Jogada]] = {}
    for j in jogadas:
        dias.setdefault(_local(j.ended_at).date().isoformat(), []).append(j)
    return [{"dia": dia, **_soma(grupo)} for dia, grupo in sorted(dias.items())]


def por_mapa(jogadas: list[Jogada]) -> list[dict]:
    """Mapas (com a mesma combinacao de mods) tentados mais de uma vez, do mais tentado para o menos."""
    grupos: dict[tuple[int, str], list[Jogada]] = {}
    for j in jogadas:
        grupos.setdefault((j.beatmap_id, j.mods), []).append(j)
    saida = []
    for (beatmap_id, mods), tentativas in grupos.items():
        if len(tentativas) < 2:
            continue
        primeira = tentativas[0]
        saida.append({
            "id": f"{beatmap_id}:{mods}",
            "beatmap_id": beatmap_id,
            "mapa": primeira.nome_mapa,
            "mods": nome_mods(mods),
            "estrelas": _arredondar(primeira.estrelas),
            "tentativas": [_ponto(j) for j in tentativas],
            "maior_fracao": max((j.fracao for j in tentativas if j.fracao is not None), default=None),
            "tendencia": _tendencia(tentativas),
            **_soma(tentativas),
        })
    saida.sort(key=lambda m: m["tentativas"][-1]["quando"], reverse=True)  # empate: o jogado por ultimo primeiro
    saida.sort(key=lambda m: m["jogadas"], reverse=True)
    return saida


def faixa(estrelas: float) -> float:
    return int(estrelas / LARGURA_FAIXA) * LARGURA_FAIXA


def por_faixa(jogadas: list[Jogada]) -> list[dict]:
    """Uma linha por faixa de 0,5 estrela (com os mods da jogada), da mais facil para a mais dificil."""
    grupos: dict[float, list[Jogada]] = {}
    for j in jogadas:
        if j.estrelas is not None:
            grupos.setdefault(faixa(j.estrelas), []).append(j)
    return [
        {"de": inicio, "ate": inicio + LARGURA_FAIXA, "nome": f"{_virgula(inicio)} a {_virgula(inicio + LARGURA_FAIXA)}★",
         "mapas": len({(j.beatmap_id, j.mods) for j in grupo}), **_soma(grupo)}
        for inicio, grupo in sorted(grupos.items())
    ]


def _virgula(valor: float) -> str:
    return f"{valor:.1f}".replace(".", ",")


def aim_contra_speed(jogadas: list[Jogada]) -> dict:
    """Em que tipo de mapa os misses aparecem mais: puxado para aim ou para speed.

    Mapas mais dificeis tem aim e speed maiores ao mesmo tempo, entao a comparacao e feita dentro
    de cada faixa de estrelas: a jogada conta como "puxada para aim" se a razao aim/speed do mapa
    esta acima da mediana das jogadas da mesma faixa, e "puxada para speed" se esta abaixo.
    Faixa com uma jogada so (ou mapas todos iguais) nao entra.
    """
    grupos: dict[float, list[Jogada]] = {}
    for j in jogadas:
        if j.estrelas is not None and j.aim and j.speed:
            grupos.setdefault(faixa(j.estrelas), []).append(j)
    aim, speed = [], []
    for grupo in grupos.values():
        if len(grupo) < 2:
            continue
        mediana = statistics.median(j.aim / j.speed for j in grupo)
        for j in grupo:
            razao = j.aim / j.speed
            if razao > mediana:
                aim.append(j)
            elif razao < mediana:
                speed.append(j)
    return {
        "aim": {**_soma(aim), "mapas": len({(j.beatmap_id, j.mods) for j in aim})},
        "speed": {**_soma(speed), "mapas": len({(j.beatmap_id, j.mods) for j in speed})},
    }


def acc_por_jogada(jogadas: list[Jogada]) -> list[dict]:
    return [_ponto(j) for j in jogadas if j.julgadas]


def sugestoes(jogadas: list[Jogada], limite: int = LIMITE_SUGESTOES) -> list[dict]:
    """Mapas para farmar: onde, no ritmo atual de misses, o mapa inteiro sairia com menos misses.

    Para cada mapa (com os mesmos mods) ainda sem FC: misses estimados no mapa inteiro =
    misses por nota somando as tentativas x notas do mapa. Entra quem ja tem pelo menos
    AMOSTRA_MINIMA_SUGESTAO notas jogadas, para uma tentativa curta de sorte nao dominar a lista.
    Ordena pela estimativa, do menor para o maior; empate vai para o mapa mais dificil.
    """
    grupos: dict[tuple[int, str], list[Jogada]] = {}
    for j in jogadas:
        grupos.setdefault((j.beatmap_id, j.mods), []).append(j)
    saida = []
    for (beatmap_id, mods), tentativas in grupos.items():
        ultima = tentativas[-1]
        s = _soma(tentativas)
        if s["notas"] < AMOSTRA_MINIMA_SUGESTAO or not ultima.notas_mapa:
            continue
        if any(j.fc for j in tentativas):
            continue  # ja tem FC
        saida.append({
            "beatmap_id": beatmap_id,
            "mapa": ultima.nome_mapa,
            "mods": nome_mods(mods),
            "estrelas": _arredondar(ultima.estrelas),
            "notas_mapa": ultima.notas_mapa,
            "misses_estimados": round(s["misses"] / s["notas"] * ultima.notas_mapa, 1),
            "maior_fracao": max((j.fracao for j in tentativas if j.fracao is not None), default=None),
            "ultima": ultima.ended_at,
            **s,
        })
    saida.sort(key=lambda m: (m["misses_estimados"], -(m["estrelas"] or 0)))
    return saida[:limite]


def painel(con: sqlite3.Connection, dias: int | None = None, mods: str | None = None,
           agora: datetime | None = None) -> dict:
    """Tudo o que a pagina do painel mostra, com os filtros aplicados."""
    jogadas = carregar(con, dias, mods, agora)
    return {
        "resumo": _soma(jogadas),
        "por_dia": por_dia(jogadas),
        "por_mapa": por_mapa(jogadas),
        "por_faixa": por_faixa(jogadas),
        "aim_speed": aim_contra_speed(jogadas),
        "acc": acc_por_jogada(jogadas),
        "sugestoes": sugestoes(jogadas),
        "regras": {
            "amostra_minima_sugestao": AMOSTRA_MINIMA_SUGESTAO,
            "limite_sugestoes": LIMITE_SUGESTOES,
            "mudanca_tendencia": MUDANCA_TENDENCIA,
        },
    }

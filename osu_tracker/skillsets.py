"""Skillsets dos mapas (stream, jump, tech, alternate): regras, marcacoes do jogador e modelo treinado.

Para cada mapa e tipo, vale o primeiro que existir:
1. a marcacao do jogador (tabela marcacoes), que sempre ganha;
2. o modelo do tipo, quando ha marcacoes suficientes (MINIMO_POR_CLASSE sim e nao);
3. a regra do tipo, sobre as features do .osu (arquivo_osu.features).

O modelo e uma regressao logistica com regularizacao L2 sobre as features padronizadas:
cada peso diz quanto a feature puxa para "e do tipo" (positivo) ou "nao e" (negativo).
A precisao estimada e a acuracia balanceada (media do acerto nos mapas que sao e nos que nao
sao do tipo) numa validacao cruzada estratificada de ate 5 partes.
A regra tambem tem uma confianca: quanto mais longe do limite, mais perto de 100%.
"""

import hashlib
import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import arquivo_osu
from .api import ApiError, RespostaRecusada

log = logging.getLogger("osu_tracker")

TIPOS = ("stream", "jump", "tech", "alternate")
NOMES_TIPOS = {"stream": "Stream", "jump": "Jump", "tech": "Tech", "alternate": "Alternate / finger control"}
MINIMO_POR_CLASSE = 20       # marcacoes sim e nao do tipo para treinar o modelo
PRECISAO_BAIXA = 0.75        # abaixo disso o painel avisa
DOBRAS = 5
L2 = 1.0
LIMITE_POR_COLETA = 30       # .osu baixados por coleta (1 por segundo)
OSU = 0

# Regra de cada tipo: (feature, limite). O tipo vale quando alguma feature passa do limite;
# a forca da regra e a maior razao feature/limite.
# Limites calibrados em 23/11/2026 nos 90 mapas jogados do primeiro banco real: mudanca de ritmo
# e SV aparecem em quase todo mapa (mediana 0,41 e 8,7 por minuto), entao tech so pega a ponta.
REGRAS: dict[str, list[tuple[str, float]]] = {
    "stream": [("frac_stream", 0.25)],
    "jump": [("frac_saltos", 0.35)],
    "tech": [("mudanca_ritmo", 0.50), ("sv_por_minuto", 30.0), ("frac_1_3", 0.10)],
    "alternate": [("frac_burst", 0.25)],
}
INCLINACAO_REGRA = 4.0  # forca 2x o limite -> ~94% de confianca; no limite -> 50%


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- arquivos .osu e features ---

def pasta_osu(db_path: Path) -> Path:
    """Copia local dos .osu baixados, ao lado do banco (servem tambem para analises futuras)."""
    return db_path.parent / "mapas_osu"


def pendentes_features(con: sqlite3.Connection) -> list[tuple[int, str | None]]:
    """Mapas de osu! standard jogados sem features na versao atual, do jogado por ultimo para o primeiro."""
    return con.execute(
        """
        SELECT m.beatmap_id, m.checksum FROM mapas m
        JOIN jogadas j ON j.beatmap_id = m.beatmap_id AND j.ruleset_id = ?
        LEFT JOIN mapas_osu o ON o.beatmap_id = m.beatmap_id
        WHERE m.ruleset_id = ?
          AND (o.beatmap_id IS NULL OR (o.erro IS NULL AND (o.versao < ? OR o.checksum IS NOT m.checksum)))
        GROUP BY m.beatmap_id ORDER BY MAX(j.ended_at) DESC
        """,
        (OSU, OSU, arquivo_osu.VERSAO),
    ).fetchall()


def _gravar_features(con, beatmap_id, checksum, feats, erro) -> None:
    con.execute(
        "INSERT OR REPLACE INTO mapas_osu (beatmap_id, checksum, versao, features_json, erro, lido_em) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (beatmap_id, checksum, arquivo_osu.VERSAO, json.dumps(feats) if feats else None, erro, _agora()),
    )


def ler_mapas(con: sqlite3.Connection, api, pasta: Path, limite: int = LIMITE_POR_COLETA) -> tuple[int, int]:
    """Le (ou baixa) o .osu de cada mapa pendente e grava as features. Devolve (lidos, ainda pendentes).

    O .osu fica em `pasta` com o nome do beatmap_id; se o md5 nao bate com o checksum do mapa
    (mapa atualizado), baixa de novo. Falha de rede interrompe e o resto fica para a proxima coleta.
    """
    fila = pendentes_features(con)
    feitos = baixados = 0
    for beatmap_id, checksum in fila:
        arquivo = pasta / f"{beatmap_id}.osu"
        conteudo = arquivo.read_bytes() if arquivo.exists() else None
        if conteudo is None or (checksum and arquivo_osu.md5(conteudo) != checksum):
            if baixados >= limite:
                continue
            try:
                conteudo = api.arquivo_osu(beatmap_id)
            except RespostaRecusada as e:
                with con:
                    _gravar_features(con, beatmap_id, checksum, None, str(e))
                feitos += 1
                continue
            except ApiError as e:
                log.warning("Leitura dos .osu interrompida, continua na proxima coleta: %s", e)
                break
            baixados += 1
            pasta.mkdir(parents=True, exist_ok=True)
            arquivo.write_bytes(conteudo)
        try:
            feats, erro = arquivo_osu.features(arquivo_osu.ler(conteudo.decode("utf-8-sig", errors="replace"))), None
        except (arquivo_osu.ArquivoInvalido, ValueError, IndexError) as e:
            feats, erro = None, f"{type(e).__name__}: {e}"
            log.warning(".osu do mapa %s nao deu para ler: %s", beatmap_id, erro)
        with con:
            # Guarda o checksum da API, nao o md5 do arquivo: so le de novo quando o mapa mudar na API.
            _gravar_features(con, beatmap_id, checksum, feats, erro)
        feitos += 1
    return feitos, len(fila) - feitos


def carregar_features(con: sqlite3.Connection) -> dict[int, dict[str, float]]:
    return {
        bid: json.loads(f)
        for bid, f in con.execute("SELECT beatmap_id, features_json FROM mapas_osu WHERE features_json IS NOT NULL")
    }


# --- marcacoes ---

def marcar(con: sqlite3.Connection, beatmap_id: int, tipos: set[str]) -> None:
    """Marca o mapa: os tipos dados sao 'sim', os outros 'nao' (conjunto vazio = nenhum dos tipos)."""
    invalidos = set(tipos) - set(TIPOS)
    if invalidos:
        raise ValueError(f"tipo desconhecido: {', '.join(sorted(invalidos))} (use {', '.join(TIPOS)} ou nenhum)")
    agora = _agora()
    with con:
        con.executemany(
            "INSERT OR REPLACE INTO marcacoes (beatmap_id, tipo, valor, marcado_em) VALUES (?, ?, ?, ?)",
            [(beatmap_id, t, int(t in tipos), agora) for t in TIPOS],
        )


def desmarcar(con: sqlite3.Connection, beatmap_id: int) -> None:
    with con:
        con.execute("DELETE FROM marcacoes WHERE beatmap_id = ?", (beatmap_id,))


def carregar_marcacoes(con: sqlite3.Connection) -> dict[int, dict[str, int]]:
    saida: dict[int, dict[str, int]] = {}
    for bid, tipo, valor in con.execute("SELECT beatmap_id, tipo, valor FROM marcacoes"):
        saida.setdefault(bid, {})[tipo] = valor
    return saida


# --- regras ---

def _sigmoide(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def prob_regra(tipo: str, feats: dict[str, float]) -> float:
    """Probabilidade aproximada da regra: 50% no limite, sobe e desce com log(feature / limite)."""
    forca = max(feats.get(nome, 0.0) / limite for nome, limite in REGRAS[tipo])
    return _sigmoide(INCLINACAO_REGRA * math.log(max(forca, 1e-3)))


# --- regressao logistica ---

def _padronizar(linhas: list[list[float]]) -> tuple[list[float], list[float]]:
    n = len(linhas)
    medias = [sum(c) / n for c in zip(*linhas)]
    desvios = [math.sqrt(sum((v - m) ** 2 for v in c) / n) or 1.0 for c, m in zip(zip(*linhas), medias)]
    return medias, desvios


def _resolver(a: list[list[float]], b: list[float]) -> list[float]:
    """Sistema linear por eliminacao de Gauss com pivo parcial (a matriz e pequena)."""
    n = len(b)
    m = [linha[:] + [b[i]] for i, linha in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def treinar_logistica(x: list[list[float]], y: list[int], l2: float = L2, iteracoes: int = 25) -> dict:
    """Regressao logistica por Newton-Raphson; devolve medias, desvios, pesos e intercepto."""
    medias, desvios = _padronizar(x)
    z = [[1.0] + [(v - m) / d for v, m, d in zip(linha, medias, desvios)] for linha in x]
    k = len(z[0])
    w = [0.0] * k
    for _ in range(iteracoes):
        p = [_sigmoide(sum(wi * zi for wi, zi in zip(w, linha))) for linha in z]
        grad = [sum((pi - yi) * linha[j] for pi, yi, linha in zip(p, y, z)) + (l2 * w[j] if j else 0.0) for j in range(k)]
        hess = [[sum(pi * (1 - pi) * linha[i] * linha[j] for pi, linha in zip(p, z)) + (l2 if i == j and i else 0.0)
                 for j in range(k)] for i in range(k)]
        for i in range(k):
            hess[i][i] += 1e-9
        passo = _resolver(hess, grad)
        w = [wi - si for wi, si in zip(w, passo)]
        if max(abs(s) for s in passo) < 1e-6:
            break
    return {"medias": medias, "desvios": desvios, "intercepto": w[0], "pesos": w[1:]}


def prever(modelo: dict, linha: list[float]) -> float:
    s = modelo["intercepto"] + sum(
        p * (v - m) / d for p, v, m, d in zip(modelo["pesos"], linha, modelo["medias"], modelo["desvios"]))
    return _sigmoide(s)


def _dobras(y: list[int], k: int) -> list[int]:
    """Parte de cada exemplo, alternando dentro de cada classe (estratificada e deterministica)."""
    parte, contador = [0] * len(y), {0: 0, 1: 0}
    for i, yi in enumerate(y):
        parte[i] = contador[yi] % k
        contador[yi] += 1
    return parte


def acuracia_balanceada(y: list[int], previsto: list[int]) -> float:
    acertos = []
    for classe in (0, 1):
        idx = [i for i, yi in enumerate(y) if yi == classe]
        if idx:
            acertos.append(sum(previsto[i] == classe for i in idx) / len(idx))
    return sum(acertos) / len(acertos) if acertos else 0.0


def validacao_cruzada(x: list[list[float]], y: list[int], k: int = DOBRAS) -> float:
    k = max(2, min(k, sum(y), len(y) - sum(y)))
    parte = _dobras(y, k)
    previsto = [0] * len(y)
    for d in range(k):
        treino = [i for i in range(len(y)) if parte[i] != d]
        modelo = treinar_logistica([x[i] for i in treino], [y[i] for i in treino])
        for i in range(len(y)):
            if parte[i] == d:
                previsto[i] = int(prever(modelo, x[i]) >= 0.5)
    return acuracia_balanceada(y, previsto)


def _exemplos(tipo: str, marcacoes: dict, feats: dict) -> tuple[list[int], list[list[float]], list[int]]:
    ids = sorted(b for b, m in marcacoes.items() if tipo in m and b in feats)
    nomes = list(arquivo_osu.NOMES)
    return ids, [[feats[b].get(n, 0.0) for n in nomes] for b in ids], [marcacoes[b][tipo] for b in ids]


def _assinatura(ids: list[int], y: list[int]) -> str:
    texto = f"{arquivo_osu.VERSAO}|" + ",".join(f"{b}:{v}" for b, v in zip(ids, y))
    return hashlib.sha1(texto.encode()).hexdigest()


def atualizar_modelos(con: sqlite3.Connection) -> list[str]:
    """Treina de novo o modelo de cada tipo cujas marcacoes mudaram. Devolve os tipos retreinados."""
    marcacoes, feats = carregar_marcacoes(con), carregar_features(con)
    salvos = {t: a for t, a in con.execute("SELECT tipo, assinatura FROM modelos")}
    feitos = []
    for tipo in TIPOS:
        ids, x, y = _exemplos(tipo, marcacoes, feats)
        positivos, negativos = sum(y), len(y) - sum(y)
        if positivos < MINIMO_POR_CLASSE or negativos < MINIMO_POR_CLASSE:
            if tipo in salvos:
                with con:
                    con.execute("DELETE FROM modelos WHERE tipo = ?", (tipo,))
            continue
        assinatura = _assinatura(ids, y)
        if salvos.get(tipo) == assinatura:
            continue
        modelo = treinar_logistica(x, y)
        precisao = validacao_cruzada(x, y)
        regra = acuracia_balanceada(y, [int(prob_regra(tipo, feats[b]) >= 0.5) for b in ids])
        with con:
            con.execute(
                "INSERT OR REPLACE INTO modelos (tipo, modelo_json, features_json, positivos, negativos, "
                "precisao_cv, precisao_regra, assinatura, treinado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tipo, json.dumps(modelo), json.dumps(list(arquivo_osu.NOMES)), positivos, negativos,
                 round(precisao, 4), round(regra, 4), assinatura, _agora()),
            )
        log.info("Modelo de %s treinado com %d sim e %d nao: precisao estimada %.0f%%",
                 tipo, positivos, negativos, 100 * precisao)
        feitos.append(tipo)
    return feitos


def carregar_modelos(con: sqlite3.Connection) -> dict[str, dict]:
    saida = {}
    for tipo, modelo, nomes, pos, neg, prec, regra, quando in con.execute(
            "SELECT tipo, modelo_json, features_json, positivos, negativos, precisao_cv, precisao_regra, treinado_em "
            "FROM modelos"):
        saida[tipo] = {"modelo": json.loads(modelo), "features": json.loads(nomes), "positivos": pos,
                       "negativos": neg, "precisao_cv": prec, "precisao_regra": regra, "treinado_em": quando}
    return saida


# --- classificacao ---

def classificar(con: sqlite3.Connection) -> dict[int, dict]:
    """Por mapa: tipos (lista), e por tipo a probabilidade, a confianca e a fonte (marcacao, modelo ou regra)."""
    feats, marcacoes, modelos = carregar_features(con), carregar_marcacoes(con), carregar_modelos(con)
    saida = {}
    for bid in set(feats) | set(marcacoes):
        por_tipo = {}
        for tipo in TIPOS:
            marcado = marcacoes.get(bid, {}).get(tipo)
            if marcado is not None:
                prob, fonte = float(marcado), "marcação"
            elif bid not in feats:
                continue
            elif tipo in modelos:
                m = modelos[tipo]
                prob, fonte = prever(m["modelo"], [feats[bid].get(n, 0.0) for n in m["features"]]), "modelo"
            else:
                prob, fonte = prob_regra(tipo, feats[bid]), "regra"
            por_tipo[tipo] = {"sim": prob >= 0.5, "prob": round(prob, 4),
                              "confianca": round(max(prob, 1 - prob), 4), "fonte": fonte}
        if por_tipo:
            saida[bid] = {"tipos": [t for t in TIPOS if por_tipo.get(t, {}).get("sim")], "por_tipo": por_tipo,
                          "marcado": bid in marcacoes}
    return saida


def para_marcar(classes: dict[int, dict], limite: int = 10) -> list[tuple[int, str, float]]:
    """Mapas ainda sem marcacao com a maior duvida: a menor confianca entre os tipos. (id, tipo, confianca)"""
    candidatos = []
    for bid, c in classes.items():
        if c["marcado"]:
            continue
        tipo, info = min(c["por_tipo"].items(), key=lambda kv: kv[1]["confianca"])
        candidatos.append((info["confianca"], bid, tipo))
    candidatos.sort()
    return [(bid, tipo, conf) for conf, bid, tipo in candidatos[:limite]]


def situacao(con: sqlite3.Connection) -> list[dict]:
    """Por tipo: de onde vem a classificacao, quantas marcacoes, precisao estimada e os pesos do modelo."""
    marcacoes, modelos = carregar_marcacoes(con), carregar_modelos(con)
    saida = []
    for tipo in TIPOS:
        valores = [m[tipo] for m in marcacoes.values() if tipo in m]
        info = {
            "tipo": tipo, "nome": NOMES_TIPOS[tipo], "positivos": sum(valores), "negativos": len(valores) - sum(valores),
            "minimo": MINIMO_POR_CLASSE, "fonte": "regra",
            "regra": " ou ".join(f"{arquivo_osu.NOMES[n]} ≥ {limite:g}" for n, limite in REGRAS[tipo]),
            "precisao_cv": None, "precisao_regra": None, "aviso": None, "pesos": [],
        }
        if tipo in modelos:
            m = modelos[tipo]
            pesos = sorted(zip(m["features"], m["modelo"]["pesos"]), key=lambda p: -abs(p[1]))
            info.update(fonte="modelo", precisao_cv=m["precisao_cv"], precisao_regra=m["precisao_regra"],
                        treinado_em=m["treinado_em"],
                        pesos=[{"feature": arquivo_osu.NOMES.get(n, n), "peso": round(p, 3)} for n, p in pesos[:5]])
            if m["precisao_cv"] < PRECISAO_BAIXA:
                info["aviso"] = (f"Precisão estimada baixa ({100 * m['precisao_cv']:.0f}%): "
                                 "marque mais mapas deste tipo, principalmente os da lista para marcar.")
        saida.append(info)
    return saida

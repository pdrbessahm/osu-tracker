"""Leitura do .osu (osu! standard) e as features de padrao que alimentam a classificacao de skillsets.

Tudo e medido no mapa original (sem mods): ritmo em fracoes de batida e espacamento em raios do
circulo nao mudam com DT/HT; o BPM fica o do mapa.

Definicoes:
- intervalo = tempo entre o inicio de um objeto e o do seguinte (spinners ficam de fora);
- batida = duracao da batida no ponto de tempo vigente; o intervalo vira fracao de batida (1/2, 1/4...);
- nota rapida = intervalo de ate 0,3 batida (1/4, 1/6, 1/8); corrida = objetos seguidos por notas rapidas;
- stream = corrida de 9 notas ou mais; burst = corrida de 3 a 8 notas;
- espacamento = distancia do fim do objeto anterior ao inicio do seguinte, em raios do circulo
  (o fim do slider e aproximado pelo ultimo ponto de controle, ou pela cabeca com repeticao par);
- salto = intervalo de 1/2 batida ou mais lento com espacamento de 5 raios (2,5 circulos) ou mais.

Os limites ficam aqui em constantes para que a regra mostrada no painel seja a mesma do codigo.
"""

import hashlib
import math
import statistics
from dataclasses import dataclass

VERSAO = 1  # muda quando as features mudam: os mapas sao lidos de novo a partir do .osu guardado

NOTA_RAPIDA = 0.3          # batidas
MIN_STREAM = 9             # notas
MIN_BURST = 3
SALTO_RAIOS = 5.0
TOLERANCIA_SNAP = 0.04     # batidas
DIVISORES = {"1/1": 1.0, "1/2": 0.5, "1/3": 1 / 3, "1/4": 0.25, "1/6": 1 / 6, "1/8": 0.125,
             "2/3": 2 / 3, "3/4": 0.75, "2/1": 2.0, "3/2": 1.5}


class ArquivoInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Objeto:
    x: float
    y: float
    tempo: float
    fim_x: float
    fim_y: float
    slider: bool


@dataclass(frozen=True)
class Mapa:
    modo: int
    cs: float
    pontos_tempo: list[tuple[float, float]]   # (inicio, duracao da batida em ms), so os nao herdados
    mudancas_sv: int                          # pontos herdados que mudam a velocidade do slider
    objetos: list[Objeto]


def md5(conteudo: bytes) -> str:
    return hashlib.md5(conteudo).hexdigest()


def ler(texto: str) -> Mapa:
    secao = ""
    geral: dict[str, str] = {}
    dificuldade: dict[str, str] = {}
    tempos: list[tuple[float, float]] = []
    svs: list[float] = []
    objetos: list[Objeto] = []
    for bruta in texto.splitlines():
        linha = bruta.strip()
        if not linha or linha.startswith("//"):
            continue
        if linha.startswith("[") and linha.endswith("]"):
            secao = linha[1:-1]
            continue
        if secao in ("General", "Difficulty") and ":" in linha:
            chave, valor = linha.split(":", 1)
            (geral if secao == "General" else dificuldade)[chave.strip()] = valor.strip()
        elif secao == "TimingPoints":
            partes = linha.split(",")
            if len(partes) < 2:
                continue
            inicio, duracao = float(partes[0]), float(partes[1])
            herdado = len(partes) > 6 and partes[6].strip() == "0"
            if herdado or duracao < 0:
                svs.append(duracao)
            elif duracao > 0:
                tempos.append((inicio, duracao))
        elif secao == "HitObjects":
            partes = linha.split(",")
            if len(partes) < 4:
                continue
            x, y, tempo, tipo = float(partes[0]), float(partes[1]), float(partes[2]), int(partes[3])
            if tipo & 8:  # spinner
                continue
            fim_x, fim_y, slider = x, y, bool(tipo & 2)
            if slider and len(partes) > 6:
                pontos = [p for p in partes[5].split("|")[1:] if ":" in p]
                repeticoes = int(float(partes[6]))
                if pontos and repeticoes % 2:
                    fx, fy = pontos[-1].split(":")
                    fim_x, fim_y = float(fx), float(fy)
            objetos.append(Objeto(x, y, tempo, fim_x, fim_y, slider))
    if not tempos:
        raise ArquivoInvalido("o .osu nao tem ponto de tempo")
    objetos.sort(key=lambda o: o.tempo)
    tempos.sort()
    mudancas_sv = sum(1 for a, b in zip(svs, svs[1:]) if abs(a - b) > 1e-6)
    return Mapa(
        modo=int(geral.get("Mode", "0") or 0),
        cs=float(dificuldade.get("CircleSize", "4") or 4),
        pontos_tempo=tempos,
        mudancas_sv=mudancas_sv,
        objetos=objetos,
    )


def _batida(tempos: list[tuple[float, float]], instante: float) -> float:
    duracao = tempos[0][1]
    for inicio, d in tempos:
        if inicio > instante + 1:
            break
        duracao = d
    return duracao


def _bpm_dominante(tempos: list[tuple[float, float]], fim: float) -> tuple[float, float]:
    """(BPM que dura mais tempo no mapa, fracao do tempo fora dele por mais de 5%)."""
    duracoes: dict[float, float] = {}
    for i, (inicio, d) in enumerate(tempos):
        ate = tempos[i + 1][0] if i + 1 < len(tempos) else fim
        bpm = round(60000 / d, 1)
        duracoes[bpm] = duracoes.get(bpm, 0) + max(ate - max(inicio, 0), 0)
    total = sum(duracoes.values())
    if not total:
        return round(60000 / tempos[0][1], 1), 0.0
    dominante = max(duracoes, key=duracoes.get)
    fora = sum(t for bpm, t in duracoes.items() if abs(bpm - dominante) > 0.05 * dominante)
    return dominante, fora / total


def _snap(fracao: float) -> str | None:
    nome, valor = min(DIVISORES.items(), key=lambda kv: abs(kv[1] - fracao))
    return nome if abs(valor - fracao) <= TOLERANCIA_SNAP * max(valor, 0.25) / 0.25 else None


def features(mapa: Mapa) -> dict[str, float]:
    """Numeros que descrevem o padrao do mapa. Sao estes que a regra e o modelo usam."""
    if mapa.modo != 0:
        raise ArquivoInvalido("so mapas de osu! standard tem skillsets")
    objs = mapa.objetos
    if len(objs) < 10:
        raise ArquivoInvalido("mapa com menos de 10 objetos")
    raio = 54.4 - 4.48 * mapa.cs
    duracao_s = max((objs[-1].tempo - objs[0].tempo) / 1000, 1.0)
    bpm, fora_do_bpm = _bpm_dominante(mapa.pontos_tempo, objs[-1].tempo)

    fracoes, distancias, intervalos = [], [], []
    for a, b in zip(objs, objs[1:]):
        intervalo = b.tempo - a.tempo
        if intervalo <= 0:
            continue
        fracoes.append(intervalo / _batida(mapa.pontos_tempo, b.tempo))
        distancias.append(math.hypot(b.x - a.fim_x, b.y - a.fim_y) / raio)
        intervalos.append(intervalo)
    n = len(fracoes)
    if not n:
        raise ArquivoInvalido("objetos sem intervalo entre si")

    # corridas de notas rapidas: tamanho em notas (intervalos + 1)
    corridas, atual = [], 0
    espacos_corrida = []
    for f, d in zip(fracoes, distancias):
        if f <= NOTA_RAPIDA:
            atual += 1
            espacos_corrida.append(d)
        elif atual:
            corridas.append(atual + 1)
            atual = 0
    if atual:
        corridas.append(atual + 1)
    notas = len(objs)
    em_stream = sum(c for c in corridas if c >= MIN_STREAM)
    bursts = [c for c in corridas if MIN_BURST <= c < MIN_STREAM]

    lentos = [(f, d, t) for f, d, t in zip(fracoes, distancias, intervalos) if f >= 0.5 - TOLERANCIA_SNAP]
    saltos = [(d, t) for f, d, t in lentos if d >= SALTO_RAIOS]

    snaps = [_snap(f) for f in fracoes]
    contagem: dict[str, int] = {}
    for s in snaps:
        contagem[s or "outro"] = contagem.get(s or "outro", 0) + 1
    mudancas = sum(1 for a, b in zip(intervalos, intervalos[1:]) if not 0.9 <= b / a <= 1.1)

    return {
        "bpm": bpm,
        "fora_do_bpm": round(fora_do_bpm, 4),
        "densidade": round(notas / duracao_s, 3),
        "frac_stream": round(em_stream / notas, 4),
        "frac_burst": round(sum(bursts) / notas, 4),
        "maior_corrida": float(max(corridas, default=1)),
        "bursts_impares": round(sum(1 for c in bursts if c % 2) / len(bursts), 4) if bursts else 0.0,
        "frac_1_2": round(contagem.get("1/2", 0) / n, 4),
        "frac_1_3": round(sum(contagem.get(k, 0) for k in ("1/3", "1/6", "2/3")) / n, 4),
        "frac_saltos": round(len(saltos) / n, 4),
        "salto_medio": round(statistics.fmean(d for d, _ in saltos), 3) if saltos else 0.0,
        "velocidade_salto": round(statistics.fmean(d / (t / 1000) for d, t in saltos), 2) if saltos else 0.0,
        "espaco_corrida": round(statistics.fmean(espacos_corrida), 3) if espacos_corrida else 0.0,
        "mudanca_ritmo": round(mudancas / max(n - 1, 1), 4),
        "divisores": float(sum(1 for k, c in contagem.items() if k != "outro" and c / n >= 0.05)),
        "frac_sliders": round(sum(o.slider for o in objs) / notas, 4),
        "sv_por_minuto": round(mapa.mudancas_sv / (duracao_s / 60), 3),
    }


# Nome curto de cada feature para o painel e o modelo explicado.
NOMES = {
    "bpm": "BPM principal",
    "fora_do_bpm": "tempo fora do BPM principal",
    "densidade": "objetos por segundo",
    "frac_stream": "notas em streams (9+)",
    "frac_burst": "notas em bursts (3 a 8)",
    "maior_corrida": "maior stream (notas)",
    "bursts_impares": "bursts de tamanho impar",
    "frac_1_2": "intervalos de 1/2",
    "frac_1_3": "intervalos de 1/3 e 1/6",
    "frac_saltos": "intervalos que sao saltos",
    "salto_medio": "tamanho medio do salto (raios)",
    "velocidade_salto": "velocidade nos saltos (raios/s)",
    "espaco_corrida": "espacamento nos streams (raios)",
    "mudanca_ritmo": "mudancas de ritmo",
    "divisores": "divisores de ritmo usados",
    "frac_sliders": "sliders",
    "sv_por_minuto": "mudancas de SV por minuto",
}

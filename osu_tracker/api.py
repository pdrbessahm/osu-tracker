"""Cliente minimo da API v2 do osu!.

Referencia: https://osu.ppy.sh/docs/index.html
- Token por client credentials (scope "public"), valido por expires_in segundos.
- Termos de uso: no maximo 60 requisicoes por minuto e backoff exponencial em erro.
O token e reaproveitado entre coletas ate perto de expirar (o detector coleta a cada ~2 min).
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://osu.ppy.sh"
# 20220705 em diante devolve o Score no formato novo (statistics great/ok/meh/miss, mods como objetos).
API_VERSION = "20220705"
USER_AGENT = "osu-tracker/0.1"
INTERVALO_MIN = 1.0  # segundos entre requisicoes (1 req/s, dentro do limite de 60/min)
TENTATIVAS = 4

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    pass


class RespostaRecusada(ApiError):
    """O osu! respondeu com erro 4xx: repetir o mesmo pedido nao adianta."""


class NaoEncontrado(RespostaRecusada):
    pass


class _NaoAutorizado(ApiError):
    pass


class CredenciaisRecusadas(ApiError):
    """O osu! respondeu e recusou o client ID ou o client secret."""


class OsuApi:
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expira = 0.0
        self._ultima = 0.0

    def _requisitar(self, req: urllib.request.Request):
        for tentativa in range(TENTATIVAS):
            espera = INTERVALO_MIN - (time.monotonic() - self._ultima)
            if espera > 0:
                time.sleep(espera)
            self._ultima = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    atraso = 2 ** (tentativa + 1)
                    log.warning("HTTP %s em %s, nova tentativa em %ss", e.code, req.full_url, atraso)
                    time.sleep(atraso)
                    continue
                if e.code == 401:
                    raise _NaoAutorizado(f"HTTP 401 em {req.full_url}") from None
                if e.code == 404:
                    raise NaoEncontrado(f"HTTP 404 em {req.full_url}") from None
                if 400 <= e.code < 500:
                    raise RespostaRecusada(f"HTTP {e.code} em {req.full_url}") from None
                raise ApiError(f"HTTP {e.code} em {req.full_url}") from None
            except urllib.error.URLError as e:
                atraso = 2 ** (tentativa + 1)
                log.warning("Falha de rede (%s), nova tentativa em %ss", e.reason, atraso)
                time.sleep(atraso)
        raise ApiError(f"Sem resposta de {req.full_url} apos {TENTATIVAS} tentativas")

    def autenticar(self) -> None:
        corpo = urllib.parse.urlencode({
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
            "scope": "public",
        }).encode()
        req = urllib.request.Request(
            f"{BASE}/oauth/token",
            data=corpo,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            dados = self._requisitar(req)
        except ApiError as e:
            # 400 e 401 sao o osu! dizendo que o par ID e secret nao vale; o resto e rede ou servidor
            if isinstance(e, _NaoAutorizado) or str(e).startswith("HTTP 400"):
                raise CredenciaisRecusadas(f"Token recusado ({e}). Confira o client ID e o client secret do app OAuth.") from None
            raise ApiError(f"Nao consegui pedir o token ao osu! ({e}).") from None
        self._token = dados["access_token"]
        # Renova com 5 min de folga antes de expirar.
        self._expira = time.monotonic() + float(dados.get("expires_in", 3600)) - 300

    def get(self, caminho: str, params: dict | None = None):
        url = f"{BASE}/api/v2{caminho}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._chamar(url)

    def post(self, caminho: str, corpo: dict):
        return self._chamar(f"{BASE}/api/v2{caminho}", json.dumps(corpo).encode())

    def _chamar(self, url: str, corpo: bytes | None = None):
        for tentativa in range(2):
            if self._token is None or time.monotonic() >= self._expira:
                self.autenticar()
            cabecalhos = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "x-api-version": API_VERSION,
                "User-Agent": USER_AGENT,
            }
            if corpo is not None:
                cabecalhos["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=corpo, headers=cabecalhos)
            try:
                return self._requisitar(req)
            except _NaoAutorizado:
                # Token revogado ou expirado antes da hora: pega outro uma vez.
                self._token = None
                if tentativa:
                    raise ApiError(f"HTTP 401 em {url} mesmo com token novo") from None

    def atributos_dificuldade(self, beatmap_id: int, mods: list[dict], ruleset_id: int) -> dict:
        """Atributos de dificuldade do mapa com os mods aplicados (POST /beatmaps/{id}/attributes)."""
        resposta = self.post(f"/beatmaps/{beatmap_id}/attributes", {"mods": mods, "ruleset_id": ruleset_id})
        return resposta["attributes"]

    def buscar_usuario(self, id_ou_nick: str, mode: str | None = None) -> dict:
        """Jogador pelo ID numerico ou pelo nick (com ou sem @ na frente).

        Sem mode, a API usa o modo principal do jogador (campo playmode).
        """
        texto = id_ou_nick.strip()
        if texto.startswith("@"):
            alvo = texto
        elif texto.isdigit():
            alvo = texto
        else:
            alvo = "@" + texto
        try:
            caminho = f"/users/{urllib.parse.quote(alvo)}" + (f"/{mode}" if mode else "")
            return self.get(caminho)
        except NaoEncontrado:
            raise NaoEncontrado(f"Jogador {texto!r} nao encontrado") from None

    def jogadas_recentes(self, user_id: int, mode: str, por_pagina: int = 100, max_paginas: int = 10) -> list[dict]:
        """Jogadas recentes do jogador, incluindo fails e jogadas do lazer."""
        # A doc nao fixa o maximo de limit, entao pagina por offset ate vir lote vazio.
        jogadas: list[dict] = []
        for _ in range(max_paginas):
            lote = self.get(f"/users/{user_id}/scores/recent", {
                "include_fails": 1,
                "legacy_only": 0,
                "mode": mode,
                "limit": por_pagina,
                "offset": len(jogadas),
            })
            if not lote:
                break
            jogadas.extend(lote)
        return jogadas

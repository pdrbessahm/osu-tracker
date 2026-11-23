"""Assistente de configuracao da primeira execucao."""

import getpass
import re
from collections.abc import Callable
from pathlib import Path

from . import config
from .api import ApiError, CredenciaisRecusadas, NaoEncontrado, OsuApi

TENTATIVAS = 3

# No console do Windows o Ctrl+V nao cola no campo escondido: chega o caractere de controle no lugar do texto
DICA_COLAR = (
    "No Windows, se o Ctrl+V nao colar, clique com o botao direito do mouse dentro da janela para colar "
    "(ou use o Windows Terminal). O texto nao aparece enquanto voce cola, e normal."
)

COMO_CRIAR_APP = """\
O osu-tracker le suas jogadas pela API oficial do osu!, com um app OAuth seu
(cada jogador usa o proprio; nada e compartilhado e tudo fica neste PC).

Como criar o app (1 minuto):
  1. Entre no site do osu! e abra https://osu.ppy.sh/home/account/edit#oauth
  2. Clique em "New OAuth Application".
  3. Nome: qualquer um, por exemplo osu-tracker.
     Application Callback URL: deixe em branco.
  4. Clique em "Register application" e copie o Client ID e o Client Secret.
O Client Secret e como uma senha: nao mostre a ninguem.
"""


class Cancelado(Exception):
    pass


class Assistente:
    def __init__(
        self,
        entrada: Callable[[str], str] = input,
        senha: Callable[[str], str] = getpass.getpass,
        saida: Callable[[str], None] = print,
        criar_api: Callable[[str, str], OsuApi] = OsuApi,
    ):
        self.entrada = entrada
        self.senha = senha
        self.saida = saida
        self.criar_api = criar_api

    def _perguntar(self, texto: str, padrao: str = "") -> str:
        sufixo = f" [{padrao}]" if padrao else ""
        try:
            resposta = self.entrada(f"{texto}{sufixo}: ").strip()
        except EOFError:
            raise Cancelado from None
        return resposta or padrao

    def _sim(self, texto: str, padrao: bool) -> bool:
        resposta = self._perguntar(f"{texto} ({'S/n' if padrao else 's/N'})").lower()
        return padrao if not resposta else resposta.startswith("s") or resposta.startswith("y")

    def _credenciais(self, atual: dict) -> tuple[OsuApi, dict]:
        """Devolve a API autenticada e o que gravar na secao [osu_api] (vazio se veio do ambiente)."""
        env_id, env_secret = config.credenciais_do_ambiente()
        if env_id and env_secret:
            self.saida("Usando OSU_CLIENT_ID e OSU_CLIENT_SECRET do ambiente (.env); nao vou grava-los.")
            api = self.criar_api(env_id, env_secret)
            try:
                api.autenticar()
            except ApiError as e:
                raise Cancelado(f"As credenciais do ambiente foram recusadas: {e}") from None
            return api, {}

        self.saida(COMO_CRIAR_APP)
        for _ in range(TENTATIVAS):
            client_id = self._perguntar("Client ID", str(atual.get("client_id", "")))
            if not client_id.isdigit():
                self.saida("O Client ID e um numero. Tente de novo.")
                continue
            try:
                segredo = self.senha("Client Secret (nao aparece ao digitar; Enter mantem o atual): ").strip()
            except EOFError:
                raise Cancelado from None
            # espaco ou quebra de linha que vem junto ao colar nao faz parte do secret
            segredo = re.sub(r"\s+", "", segredo) or str(atual.get("client_secret", ""))
            if not segredo:
                self.saida("Falta o Client Secret. Tente de novo.\n" + DICA_COLAR)
                continue
            if not segredo.isprintable() or not segredo.isascii():
                self.saida("O que chegou nao parece um Client Secret: a colagem nao funcionou.\n" + DICA_COLAR)
                continue
            api = self.criar_api(client_id, segredo)
            try:
                api.autenticar()
            except CredenciaisRecusadas:
                self.saida(
                    "O osu! recusou essas credenciais. Confira se o Client ID e o Client Secret sao do mesmo app "
                    "e, se voce clicou em \"Reset client secret\", use o novo.\n" + DICA_COLAR
                )
                continue
            except ApiError as e:
                self.saida(f"Nao consegui falar com o osu!: {e} Confira a internet e tente de novo.")
                continue
            self.saida("Credenciais aceitas.")
            return api, {"client_id": client_id, "client_secret": segredo}
        raise Cancelado("Credenciais recusadas em todas as tentativas.")

    def _jogador(self, api: OsuApi, atual: dict, jogador: str | None) -> dict:
        interativo = jogador is None
        for _ in range(TENTATIVAS):
            texto = jogador or self._perguntar("Seu ID ou nick no osu!", str(atual.get("username") or atual.get("user_id") or ""))
            if not texto:
                continue
            try:
                usuario = api.buscar_usuario(texto)
            except NaoEncontrado:
                self.saida(f"Nao achei o jogador {texto!r}.")
                if not interativo:
                    raise Cancelado(f"Jogador {texto!r} nao encontrado.") from None
                continue
            except ApiError as e:
                raise Cancelado(f"Erro ao buscar o jogador: {e}") from None
            self.saida(f"Achei: {usuario['username']} (ID {usuario['id']}, modo principal {usuario.get('playmode', 'osu')}).")
            if interativo and not self._sim("E voce?", True):
                continue
            return usuario
        raise Cancelado("Jogador nao confirmado.")

    def _modo(self, padrao: str, modo: str | None, interativo: bool) -> str:
        if modo:
            if modo not in config.MODOS:
                raise Cancelado(f"Modo invalido: {modo} (use {', '.join(config.MODOS)}).")
            return modo
        if not interativo:
            return padrao
        while True:
            escolha = self._perguntar(f"Modo de jogo ({', '.join(config.MODOS)})", padrao)
            if escolha in config.MODOS:
                return escolha
            self.saida("Modo invalido.")

    def executar(self, jogador: str | None = None, modo: str | None = None, banco: str | None = None) -> int:
        caminho = config.caminho_config()
        try:
            try:
                atual = config.ler_arquivo()
            except config.ConfigError as e:
                self.saida(f"{e}\nVou criar uma configuracao nova por cima.")
                atual = {}
            self.saida(f"Configuracao do osu-tracker (fica em {caminho})\n")
            if atual and jogador is None:
                j = atual.get("jogador", {})
                self.saida(f"Ja existe configuracao para {j.get('username') or j.get('user_id')}.")
                if not self._sim("Refazer?", False):
                    self.saida("Nada mudou.")
                    return 0

            api, credenciais = self._credenciais(atual.get("osu_api", {}))
            usuario = self._jogador(api, atual.get("jogador", {}), jogador)
            mode = self._modo(usuario.get("playmode") or "osu", modo, interativo=jogador is None)

            dados: dict[str, dict] = {
                "jogador": {"user_id": int(usuario["id"]), "username": usuario["username"], "mode": mode},
                "osu_api": credenciais,
            }
            banco_final = banco or atual.get("banco", {}).get("caminho")
            if banco_final:
                dados["banco"] = {"caminho": str(Path(banco_final).expanduser())}
            if atual.get("detector"):
                dados["detector"] = atual["detector"]
            config.gravar(dados, comentario=(
                "Configuracao do osu-tracker, criada pelo assistente (python -m osu_tracker configurar).\n"
                "Opcional, secao [detector]: processos (nomes do processo do jogo), intervalo_checagem\n"
                "e intervalo_coleta (segundos). Opcional, secao [banco]: caminho do banco SQLite."
            ))
        except Cancelado as e:
            self.saida(f"Configuracao cancelada. {e}".strip())
            return 2

        self.saida(f"\nPronto. Configuracao gravada em {caminho}")
        self.saida("Para coletar enquanto voce joga: python -m osu_tracker")
        return 0

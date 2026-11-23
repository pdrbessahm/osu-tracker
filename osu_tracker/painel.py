"""Painel de analise: um servidor web local (FastAPI + uvicorn) que le o banco e mostra os graficos.

So escuta em 127.0.0.1, numa porta livre, e so le o banco (abre em modo somente leitura,
porque o detector pode estar gravando). A pagina e a biblioteca de graficos (plotly.js) vao
junto com o programa, entao funciona sem internet.
"""

import logging
import socket
import sqlite3
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analise, config

log = logging.getLogger("osu_tracker")

HOST = "127.0.0.1"


def pasta_web() -> Path:
    return config.recurso("osu_tracker/web")


def banco_configurado() -> tuple[Path | None, str]:
    """Banco da configuracao atual e o nome do jogador (lido a cada pedido: vale a configuracao nova)."""
    try:
        dados = config.ler_arquivo()
    except config.ConfigError as e:
        log.warning("%s", e)
        dados = {}
    jogador = dados.get("jogador", {})
    nome = str(jogador.get("username") or jogador.get("user_id") or "")
    return (config.caminho_banco(dados) if dados else None), nome


def _abrir_somente_leitura(caminho: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True, timeout=5)


def criar_app(obter_banco: Callable[[], tuple[Path | None, str]] = banco_configurado) -> FastAPI:
    app = FastAPI(title="osu-tracker", docs_url=None, redoc_url=None, openapi_url=None)
    web = pasta_web()
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/", include_in_schema=False)
    def pagina():
        # Sem cache: depois de atualizar o osu-tracker a pagina nova aparece na hora.
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/painel")
    def dados(dias: int | None = Query(None, ge=1, le=3650), mods: str | None = None):
        caminho, jogador = obter_banco()
        vazio = {"jogador": jogador, "mods_disponiveis": [], "aviso": None}
        if caminho is None:
            return {**vazio, "aviso": "O osu-tracker ainda não foi configurado: abra a janela e preencha a aba Configuração."}
        if not caminho.exists():
            return {**vazio, "aviso": "Ainda não há jogadas no banco. Elas aparecem aqui depois da primeira coleta."}
        try:
            con = _abrir_somente_leitura(caminho)
        except sqlite3.Error as e:
            return {**vazio, "aviso": f"Não consegui abrir o banco ({caminho}): {e}"}
        try:
            return {
                **vazio,
                "mods_disponiveis": analise.opcoes_mods(con),
                **analise.painel(con, dias, mods),
            }
        except sqlite3.Error as e:
            # Banco de uma versao antiga ainda nao migrado: o detector migra na proxima coleta.
            return {**vazio, "aviso": f"Não consegui ler o banco ({caminho}): {e}. "
                                      "Rode uma coleta com esta versão do osu-tracker e tente de novo."}
        finally:
            con.close()

    return app


class Servidor:
    """O painel rodando numa thread, para a janela: sobe numa porta livre e para junto com o programa."""

    def __init__(self, porta: int = 0, app: FastAPI | None = None):
        self.porta_pedida = porta
        self.app = app or criar_app()
        self.url: str | None = None
        self._servidor: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def iniciar(self, espera: float = 15.0) -> str:
        if self.url is not None:
            return self.url
        sock = abrir_porta(self.porta_pedida)
        porta = sock.getsockname()[1]
        self._servidor = uvicorn.Server(configuracao_uvicorn(self.app))
        self._thread = threading.Thread(target=self._servidor.run, kwargs={"sockets": [sock]},
                                        name="painel", daemon=True)
        self._thread.start()
        pausa = threading.Event()
        for _ in range(int(espera / 0.05)):
            if self._servidor.started:
                break
            if not self._thread.is_alive():
                raise RuntimeError("O servidor do painel parou ao iniciar. Veja o log.")
            pausa.wait(0.05)
        else:
            raise RuntimeError("O servidor do painel não respondeu a tempo.")
        self.url = f"http://{HOST}:{porta}/"
        log.info("Painel em %s", self.url)
        return self.url

    def rodando(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def parar(self) -> None:
        if self._servidor is not None:
            self._servidor.should_exit = True
        if self._thread is not None:
            self._thread.join(10)
        self.url = None


def abrir_porta(porta: int = 0) -> socket.socket:
    """Socket ja escutando em 127.0.0.1 (porta 0 = o sistema escolhe uma livre), sem corrida pela porta."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if porta:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, porta))
    sock.listen(128)
    return sock


def configuracao_uvicorn(app: FastAPI) -> uvicorn.Config:
    # log_config=None: no executavel de janela nao ha stdout e o log padrao do uvicorn quebraria.
    # Implementacoes fixas (h11, asyncio, sem websockets) evitam importacoes dinamicas que o
    # PyInstaller nao enxerga.
    return uvicorn.Config(app, log_config=None, access_log=False, http="h11", loop="asyncio",
                          ws="none", lifespan="off", log_level="warning")


def servir(porta: int = 0, abrir_navegador: bool = True, ao_iniciar: Callable[[str], None] | None = None,
           parar: threading.Event | None = None) -> None:
    """Roda o painel ate Ctrl+C ou ate `parar` (comando python -m osu_tracker painel)."""
    servidor = Servidor(porta)
    url = servidor.iniciar()
    if ao_iniciar is not None:
        ao_iniciar(url)
    if abrir_navegador:
        webbrowser.open(url)
    parar = parar or threading.Event()
    try:
        while not parar.wait(0.5):  # com espera curta o Ctrl+C chega tambem no Windows
            if not servidor.rodando():
                raise RuntimeError("O servidor do painel parou. Veja o log.")
    except KeyboardInterrupt:
        pass
    finally:
        servidor.parar()

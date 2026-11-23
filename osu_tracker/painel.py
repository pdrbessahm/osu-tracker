"""Painel de analise: um servidor web local (FastAPI + uvicorn) que le o banco e mostra os graficos.

So escuta em 127.0.0.1, numa porta livre, e le o banco em modo somente leitura (o detector
pode estar gravando). Escreve so em dois casos: a migracao de um banco criado por uma versao
anterior, uma vez antes da primeira leitura, e a marcacao do tipo de um mapa feita pelo jogador
na aba Progresso. A pagina e a biblioteca de graficos (plotly.js) vao junto com o programa,
entao funciona sem internet.
"""

import logging
import socket
import sqlite3
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analise, config, db, progresso, skillsets

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


def _migrar_se_preciso(caminho: Path) -> None:
    """Banco criado por uma versao anterior: migra antes de ler (uma vez so; depois nao escreve mais).

    Se nao der (arquivo somente leitura, disco cheio), segue sem migrar e a leitura mostra o aviso.
    """
    con = _abrir_somente_leitura(caminho)
    try:
        if not db.precisa_migrar(con):
            return
    finally:
        con.close()
    log.info("Banco de uma versão anterior: migrando %s", caminho)
    try:
        db.conectar(caminho).close()
    except sqlite3.Error as e:
        log.warning("Não consegui migrar o banco %s: %s", caminho, e)


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
            _migrar_se_preciso(caminho)
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
            # Banco antigo que nao deu para migrar aqui (ex.: arquivo somente leitura).
            return {**vazio, "aviso": f"Não consegui ler o banco ({caminho}): {e}. "
                                      "Confira se o arquivo pode ser gravado e rode uma coleta com esta versão do osu-tracker."}
        finally:
            con.close()

    def _ler(funcao):
        """Abre o banco so para leitura e chama funcao(con); sem banco devolve o aviso."""
        caminho, jogador = obter_banco()
        vazio = {"jogador": jogador, "aviso": None}
        if caminho is None:
            return {**vazio, "aviso": "O osu-tracker ainda não foi configurado: abra a janela e preencha a aba Configuração."}
        if not caminho.exists():
            return {**vazio, "aviso": "Ainda não há dados no banco. Eles aparecem aqui depois da primeira coleta."}
        try:
            _migrar_se_preciso(caminho)
            con = _abrir_somente_leitura(caminho)
        except sqlite3.Error as e:
            return {**vazio, "aviso": f"Não consegui abrir o banco ({caminho}): {e}"}
        try:
            return {**vazio, **funcao(con)}
        except sqlite3.Error as e:
            return {**vazio, "aviso": f"Não consegui ler o banco ({caminho}): {e}."}
        finally:
            con.close()

    @app.get("/api/progresso")
    def dados_progresso():
        return _ler(progresso.progresso)

    @app.post("/api/marcacoes")
    def gravar_marcacao(pedido: Marcacao):
        """A unica escrita do painel: o tipo de um mapa marcado pelo jogador (e o modelo treinado de novo)."""
        caminho, _ = obter_banco()
        if caminho is None or not caminho.exists():
            raise HTTPException(409, "Ainda não há banco para gravar a marcação.")
        try:
            con = db.conectar(caminho)
        except sqlite3.Error as e:
            raise HTTPException(503, f"Não consegui abrir o banco para gravar: {e}") from None
        try:
            if pedido.limpar:
                skillsets.desmarcar(con, pedido.beatmap_id)
            else:
                skillsets.marcar(con, pedido.beatmap_id, set(pedido.tipos))
            retreinados = skillsets.atualizar_modelos(con)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except sqlite3.Error as e:
            raise HTTPException(503, f"Não consegui gravar a marcação: {e}") from None
        finally:
            con.close()
        return {"ok": True, "retreinados": retreinados}

    return app


class Marcacao(BaseModel):
    beatmap_id: int = Field(gt=0)
    tipos: list[str] = []          # vazio = nenhum dos tipos
    limpar: bool = False           # tira a marcacao: o mapa volta para o classificador


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

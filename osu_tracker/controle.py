"""Controle do detector em segundo plano e leitura do status, usados pela janela.

O detector roda num processo proprio, entao fechar a janela nao o para.
- Rodando ou nao: quem segura a trava detector.lock.
- Parar: a janela cria o arquivo detector.parar, que o detector confere a cada segundo;
  se ele nao sair a tempo (coleta longa), a janela encerra o processo pelo PID em detector.pid.
Nada aqui depende do tkinter, para dar para testar sem tela.
"""

import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from . import config
from .trava import Ocupada, Trava

log = logging.getLogger("osu_tracker")

NOME_TAREFA = "osu-tracker"
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # so existe no Windows


def trava_detector() -> Trava:
    return Trava(config.pasta_app() / "detector.lock")


def arquivo_pid() -> Path:
    return config.pasta_app() / "detector.pid"


def arquivo_parar() -> Path:
    return config.pasta_app() / "detector.parar"


# --- lado do detector ---

def adquirir_trava_detector(tentativas: int = 5) -> Trava:
    """Pega a trava do detector. Insiste um pouco porque a janela a segura por instantes ao conferir o status."""
    trava = trava_detector()
    for i in range(tentativas):
        try:
            trava.adquirir()
            return trava
        except Ocupada:
            if i == tentativas - 1:
                raise
            time.sleep(0.2)
    raise Ocupada(str(trava.caminho))


def anunciar_detector(parar: threading.Event, intervalo: float = 1.0) -> None:
    """Chamado pelo detector depois de pegar a trava: grava o PID e passa a atender o pedido de parada."""
    arquivo_parar().unlink(missing_ok=True)  # pedido velho, de um detector que foi encerrado a forca
    arquivo_pid().write_text(str(os.getpid()), encoding="ascii")

    def vigiar():
        while not parar.wait(intervalo):
            if arquivo_parar().exists():
                arquivo_parar().unlink(missing_ok=True)
                log.info("Parada pedida pela janela")
                parar.set()

    threading.Thread(target=vigiar, name="pedido-de-parada", daemon=True).start()


def encerrar_detector() -> None:
    arquivo_pid().unlink(missing_ok=True)


# --- lado da janela ---

def detector_rodando() -> bool:
    trava = trava_detector()
    try:
        trava.adquirir()
    except Ocupada:
        return True
    trava.soltar()
    return False


def comando_detector() -> list[str]:
    """Como iniciar o detector sem janela de console, do executavel ou do codigo-fonte."""
    if config.EXECUTAVEL:
        return [sys.executable, "detectar"]
    python = Path(sys.executable)
    if os.name == "nt" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    return [str(python), "-m", "osu_tracker", "detectar"]


def iniciar_detector() -> bool:
    """Inicia o detector num processo separado, que continua depois que a janela fecha."""
    if detector_rodando():
        return False
    ambiente = dict(os.environ)
    # Executavel de arquivo unico: a copia nova extrai os proprios arquivos em vez de usar os desta,
    # que somem quando a janela fecha.
    ambiente["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    opcoes: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(config.RAIZ),
        "env": ambiente,
    }
    if os.name == "nt":
        opcoes["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        opcoes["start_new_session"] = True
    subprocess.Popen(comando_detector(), **opcoes)
    return True


def _processo_do_detector() -> psutil.Process | None:
    try:
        pid = int(arquivo_pid().read_text(encoding="ascii").strip())
        proc = psutil.Process(pid)
        linha = " ".join(proc.cmdline())
    except (OSError, ValueError, psutil.Error):
        return None
    # Confere que o PID ainda e do osu-tracker (o sistema reaproveita numeros de processo).
    return proc if "detectar" in linha or "osu_tracker" in linha else None


def parar_detector(espera: float = 10.0, intervalo: float = 0.2) -> bool:
    """Pede para o detector parar e espera; se demorar demais, encerra o processo. Devolve se parou."""
    if not detector_rodando():
        return True
    arquivo_parar().parent.mkdir(parents=True, exist_ok=True)
    arquivo_parar().touch()
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        if not detector_rodando():
            return True
        time.sleep(intervalo)
    proc = _processo_do_detector()
    if proc is not None:
        # No meio de uma coleta nao perde nada: o banco e transacional e a proxima coleta continua.
        proc.terminate()
        try:
            proc.wait(5)
        except psutil.TimeoutExpired:
            proc.kill()
    arquivo_parar().unlink(missing_ok=True)
    return not detector_rodando()


# --- status ---

@dataclass
class Status:
    configurado: bool
    jogador: str = ""
    erro_config: str = ""
    rodando: bool = False
    banco: Path | None = None
    total_jogadas: int = 0
    ultima_coleta: dict | None = None
    ultima_com_jogadas: dict | None = None
    erro_banco: str = ""


def _hora_local(iso_utc: str | None) -> str:
    if not iso_utc:
        return "?"
    momento = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone()
    return momento.strftime("%d/%m/%Y %H:%M")


def texto_coleta(coleta: dict | None) -> str:
    if not coleta:
        return "nenhuma ainda"
    quando = _hora_local(coleta["inicio"])
    if coleta.get("erro"):
        return f"{quando}, falhou: {coleta['erro']}"
    if coleta.get("fim") is None:
        return f"{quando}, em andamento"
    novas = coleta.get("novas") or 0
    return f"{quando}, {novas} {'jogada nova' if novas == 1 else 'jogadas novas'} de {coleta.get('recebidas') or 0} recebidas"


def _ler_banco(caminho: Path, status: Status) -> None:
    if not caminho.exists():
        return
    try:
        # Somente leitura: a janela nunca mexe no banco, que o detector pode estar gravando.
        con = sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as e:
        status.erro_banco = str(e)
        return
    con.row_factory = sqlite3.Row
    try:
        status.total_jogadas = con.execute("SELECT COUNT(*) FROM jogadas").fetchone()[0]
        linha = con.execute("SELECT * FROM coletas ORDER BY id DESC LIMIT 1").fetchone()
        status.ultima_coleta = dict(linha) if linha else None
        linha = con.execute("SELECT * FROM coletas WHERE novas > 0 ORDER BY id DESC LIMIT 1").fetchone()
        status.ultima_com_jogadas = dict(linha) if linha else None
    except sqlite3.Error as e:
        status.erro_banco = str(e)
    finally:
        con.close()


def ler_status() -> Status:
    try:
        dados = config.ler_arquivo()
    except config.ConfigError as e:
        return Status(configurado=False, erro_config=str(e), rodando=detector_rodando())
    status = Status(configurado=False, rodando=detector_rodando())
    if not dados:
        return status
    try:
        cfg = config.carregar()
    except config.ConfigError as e:
        status.erro_config = str(e)
    else:
        status.configurado = True
        status.jogador = f"{cfg.username or cfg.user_id} (modo {cfg.mode})"
    status.banco = config.caminho_banco(dados)
    _ler_banco(status.banco, status)
    return status


# --- abrir arquivos ---

def abrir(caminho: Path) -> None:
    """Abre pasta ou arquivo no programa padrao do sistema."""
    if sys.platform == "win32":
        os.startfile(caminho)  # noqa: S606 (so existe no Windows)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(caminho)])
    else:
        subprocess.Popen(["xdg-open", str(caminho)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- iniciar com o Windows (mesma logica de windows/instalar.ps1) ---

def suporta_inicio_com_windows() -> bool:
    return sys.platform == "win32"


def atalho_inicializar() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "osu-tracker.lnk"


def inicio_com_windows_ativo() -> bool:
    if not suporta_inicio_com_windows():
        return False
    if atalho_inicializar().exists():
        return True
    consulta = subprocess.run(["schtasks", "/Query", "/TN", NOME_TAREFA], capture_output=True, creationflags=SEM_JANELA)
    return consulta.returncode == 0


def _texto_ps(valor: str) -> str:
    """Texto literal do PowerShell: entre aspas simples, com a aspa simples dobrada."""
    return "'" + valor.replace("'", "''") + "'"


def comando_powershell(script: str, **parametros: str | bool) -> list[str]:
    """Linha de comando que roda um dos scripts de windows/ com os parametros dados.

    Vai por -Command com cada valor entre aspas, porque por -File um valor que comeca com hifen
    (como "-m osu_tracker detectar") seria lido como nome de parametro.
    """
    partes = ["&", _texto_ps(str(config.recurso(f"windows/{script}")))]
    for nome, valor in parametros.items():
        if valor is True:
            partes.append(f"-{nome}")
        elif valor is not False:
            partes += [f"-{nome}", _texto_ps(str(valor))]
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", " ".join(partes)]


def _powershell(script: str, **parametros: str | bool) -> None:
    resultado = subprocess.run(comando_powershell(script, **parametros),
                               capture_output=True, text=True, creationflags=SEM_JANELA)
    if resultado.returncode != 0:
        detalhe = (resultado.stderr or resultado.stdout).strip().splitlines()
        raise RuntimeError(detalhe[0] if detalhe else f"{script} saiu com codigo {resultado.returncode}")


def ativar_inicio_com_windows() -> None:
    executavel, *argumentos = comando_detector()
    _powershell("instalar.ps1", Executavel=executavel, Argumentos=" ".join(argumentos),
                Pasta=str(config.RAIZ), SemConfigurar=True, NaoIniciar=True)


def desativar_inicio_com_windows() -> None:
    _powershell("desinstalar.ps1", ManterRodando=True)

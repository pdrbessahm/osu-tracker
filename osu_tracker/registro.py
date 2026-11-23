"""Log em arquivo na pasta do jogador e, se houver terminal, na tela."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config

log = logging.getLogger("osu_tracker")


def configurar() -> None:
    if log.handlers:
        return
    pasta = config.pasta_app()
    pasta.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    arquivo = RotatingFileHandler(pasta / "osu-tracker.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    arquivo.setFormatter(fmt)
    log.addHandler(arquivo)
    # Iniciado no logon (pythonw) nao ha terminal: so o arquivo.
    if sys.stderr is not None and sys.stderr.isatty():
        tela = logging.StreamHandler()
        tela.setFormatter(fmt)
        log.addHandler(tela)
    log.setLevel(logging.INFO)

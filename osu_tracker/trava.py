"""Trava por arquivo, entre processos, no Windows e no POSIX.

O sistema operacional solta a trava sozinho se o processo morrer, entao nao sobra trava velha.
"""

import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class Ocupada(RuntimeError):
    pass


class Trava:
    def __init__(self, caminho: Path):
        self.caminho = caminho
        self._arquivo = None

    def adquirir(self) -> None:
        """Pega a trava sem esperar; levanta Ocupada se outro processo ja tem."""
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        arquivo = open(self.caminho, "a+b")
        try:
            if os.name == "nt":
                arquivo.seek(0)
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            arquivo.close()
            raise Ocupada(str(self.caminho)) from None
        self._arquivo = arquivo

    def soltar(self) -> None:
        if self._arquivo is None:
            return
        try:
            if os.name == "nt":
                self._arquivo.seek(0)
                msvcrt.locking(self._arquivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._arquivo.fileno(), fcntl.LOCK_UN)
        finally:
            self._arquivo.close()
            self._arquivo = None

    def __enter__(self):
        self.adquirir()
        return self

    def __exit__(self, *_):
        self.soltar()

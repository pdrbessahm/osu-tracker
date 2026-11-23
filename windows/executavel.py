"""Ponto de entrada do osu-tracker.exe (PyInstaller). Sem argumentos abre a janela."""

import sys

from osu_tracker.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

"""Teste manual ponta a ponta do detector, com a API real e a configuracao local do jogador.

Simula o jogo com uma copia do Python chamada osu!.exe (Linux e macOS).
Uso: .venv/bin/python tests/e2e_detector.py
Nao entra no unittest discover porque chama a API de verdade.
"""

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from osu_tracker import config  # noqa: E402
from osu_tracker.detector import processo_aberto  # noqa: E402

CMD = [sys.executable, "-m", "osu_tracker"]


def main() -> int:
    cfg = config.carregar()
    log = config.pasta_app() / "osu-tracker.log"
    inicio_log = log.stat().st_size if log.exists() else 0
    t0 = time.monotonic()

    def marca(texto):
        print(f"[{time.monotonic() - t0:5.1f}s] {texto}", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        jogo_falso = Path(tmp) / "osu!.exe"
        shutil.copy(Path(sys.executable).resolve(), jogo_falso)

        marca("detector iniciado (checagem 1 s, coleta a cada 10 s)")
        detector = subprocess.Popen(CMD + ["detectar", "--intervalo-checagem", "1", "--intervalo-coleta", "10"], cwd=RAIZ)
        time.sleep(4)

        segundo = subprocess.run(CMD + ["detectar"], cwd=RAIZ, timeout=30)
        marca(f"segundo detector saiu com codigo {segundo.returncode} (3 = ja havia um rodando)")

        marca(f"jogo aberto? {processo_aberto(cfg.processos)}")
        jogo = subprocess.Popen([jogo_falso, "-c", "import time; time.sleep(25)"])
        time.sleep(1)
        marca(f"processo osu!.exe falso iniciado; jogo aberto? {processo_aberto(cfg.processos)}")
        jogo.wait()
        marca(f"jogo falso fechado; jogo aberto? {processo_aberto(cfg.processos)}")
        time.sleep(8)

        detector.terminate()
        detector.wait(timeout=30)
        marca(f"detector encerrado (codigo {detector.returncode})")

    marca("duas coletas manuais ao mesmo tempo")
    a = subprocess.Popen(CMD + ["coletar"], cwd=RAIZ)
    b = subprocess.Popen(CMD + ["coletar"], cwd=RAIZ)
    marca(f"codigos: {a.wait()} e {b.wait()} (0 = coletou, 3 = pulada pela trava)")

    print("\n--- log desta execucao ---")
    with open(log, encoding="utf-8") as f:
        f.seek(inicio_log)
        print(f.read(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

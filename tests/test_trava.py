import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from osu_tracker import coletor, config
from osu_tracker.trava import Ocupada, Trava

RAIZ = Path(__file__).resolve().parent.parent


class ApiLenta:
    """Finge a API: segura a coleta por um tempo para outra tentar entrar junto."""

    def __init__(self, espera):
        self.espera = espera

    def jogadas_recentes(self, user_id, mode):
        time.sleep(self.espera)
        return []

    def buscar_usuario(self, id_ou_nick, mode=None):
        return {"id": 1, "statistics": {"pp": 1000.0}, "rank_history": {"mode": mode, "data": []}}


class TestTrava(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = config.Config(
            user_id=1, username="teste", mode="osu", db_path=Path(self.tmp.name) / "t.sqlite3",
            client_id="1", client_secret="x", processos=("osu!.exe",),
            intervalo_checagem=5, intervalo_coleta=120,
        )

    def test_segunda_trava_ocupada_e_liberada_depois(self):
        caminho = Path(self.tmp.name) / "a.lock"
        with Trava(caminho):
            with self.assertRaises(Ocupada):
                Trava(caminho).adquirir()
        with Trava(caminho):
            pass

    def test_duas_coletas_ao_mesmo_tempo(self):
        resultados = {}

        def rodar(nome, espera):
            resultados[nome] = coletor.coletar(self.cfg, ApiLenta(espera), motivo=nome)

        primeira = threading.Thread(target=rodar, args=("primeira", 1.0))
        primeira.start()
        time.sleep(0.3)
        rodar("segunda", 0)
        primeira.join()
        self.assertEqual(resultados, {"primeira": coletor.OK, "segunda": coletor.PULADA})
        # Depois que a primeira termina, uma nova coleta roda normalmente.
        self.assertEqual(coletor.coletar(self.cfg, ApiLenta(0), motivo="terceira"), coletor.OK)

    def test_trava_entre_processos(self):
        caminho = Path(self.tmp.name) / "coleta.lock"
        filho = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; from pathlib import Path; from osu_tracker.trava import Trava; "
             "t = Trava(Path(sys.argv[1])); t.adquirir(); print('ok', flush=True); time.sleep(30)",
             str(caminho)],
            cwd=RAIZ, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(filho.stdout.readline().strip(), "ok")
            with self.assertRaises(Ocupada):
                Trava(caminho).adquirir()
        finally:
            filho.kill()
            filho.wait()
            filho.stdout.close()
        # Processo morto: o sistema solta a trava.
        with Trava(caminho):
            pass


if __name__ == "__main__":
    unittest.main()

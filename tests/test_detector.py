import unittest

from osu_tracker.detector import Detector


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestDetector(unittest.TestCase):
    def setUp(self):
        self.relogio = Relogio()
        self.aberto = False
        self.coletas: list[tuple[float, str]] = []
        self.det = Detector(
            jogo_aberto=lambda: self.aberto,
            coletar=lambda motivo: self.coletas.append((self.relogio.t, motivo)),
            intervalo_checagem=5,
            intervalo_coleta=120,
            relogio=self.relogio,
        )

    def avancar(self, segundos):
        fim = self.relogio.t + segundos
        while self.relogio.t < fim:
            self.det.passo()
            self.relogio.t += self.det.intervalo_checagem

    def test_jogo_fechado_nao_coleta(self):
        self.avancar(3600)
        self.assertEqual(self.coletas, [])

    def test_sessao_coleta_periodica_e_final(self):
        self.avancar(60)
        self.aberto = True
        self.avancar(400)  # abriu em t=60: coletas em 180, 300, 420
        self.aberto = False
        self.avancar(60)
        motivos = [m for _, m in self.coletas]
        self.assertEqual(motivos, ["periodica", "periodica", "periodica", "final"])
        self.assertEqual([t for t, _ in self.coletas[:3]], [180, 300, 420])
        self.assertEqual(self.coletas[3][0], 460)  # primeira conferencia depois de fechar

    def test_sessao_curta_so_coleta_final(self):
        self.aberto = True
        self.avancar(30)
        self.aberto = False
        self.avancar(30)
        self.assertEqual([m for _, m in self.coletas], ["final"])

    def test_erro_na_coleta_nao_derruba(self):
        def falha(motivo):
            raise RuntimeError("banco travado")

        self.det._coletar = falha
        self.aberto = True
        with self.assertLogs("osu_tracker", "ERROR"):
            self.avancar(130)
        self.assertTrue(self.det.aberto)


if __name__ == "__main__":
    unittest.main()

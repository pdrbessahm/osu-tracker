import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from osu_tracker import config, controle
from osu_tracker.assistente import Recusado

from test_assistente import CLIENT_ID, SEGREDO_FALSO, ApiFalsa

try:
    import tkinter as tk
    from osu_tracker import janela
except ImportError:  # Python sem tkinter
    tk = janela = None


@unittest.skipIf(janela is None, "Python sem tkinter")
class TestSalvarConfiguracao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (
            mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": self.tmp.name}),
            mock.patch.object(config, "credenciais_do_ambiente", return_value=("", "")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def salvar(self, client_id=CLIENT_ID, segredo=SEGREDO_FALSO, jogador="JogadorTeste", modo=janela.MODO_PRINCIPAL if janela else ""):
        return janela.salvar_configuracao(client_id, segredo, jogador, modo, criar_api=ApiFalsa)

    def test_grava_com_o_modo_principal(self):
        # espaco e quebra de linha colados junto com o secret nao contam
        usuario = self.salvar(segredo=f" {SEGREDO_FALSO}\r\n")
        self.assertEqual((usuario["id"], usuario["mode"]), (424242, "osu"))
        cfg = config.carregar()
        self.assertEqual((cfg.user_id, cfg.mode, cfg.client_secret), (424242, "osu", SEGREDO_FALSO))

    def test_modo_escolhido(self):
        self.assertEqual(self.salvar(modo="mania")["mode"], "mania")
        self.assertEqual(config.carregar().mode, "mania")

    def test_secret_em_branco_mantem_o_salvo(self):
        self.salvar()
        self.salvar(segredo="", jogador="424242")
        self.assertEqual(config.carregar().client_secret, SEGREDO_FALSO)

    def test_recusas_nao_gravam(self):
        for kwargs, trecho in (
            ({"client_id": "abc"}, "numero"),
            ({"segredo": "errado"}, "recusou"),
            ({"segredo": ""}, "Falta o Client Secret"),
            ({"jogador": "naoexiste"}, "Nao achei"),
            ({"jogador": " "}, "Falta o seu nick"),
        ):
            with self.subTest(**kwargs), self.assertRaisesRegex(Recusado, trecho):
                self.salvar(**kwargs)
        self.assertFalse(config.caminho_config().exists())

    def test_credenciais_do_ambiente(self):
        with mock.patch.object(config, "credenciais_do_ambiente", return_value=(CLIENT_ID, SEGREDO_FALSO)):
            self.salvar(client_id="", segredo="")
            self.assertNotIn("osu_api", config.ler_arquivo())

    def test_mantem_detector_e_banco_ajustados_a_mao(self):
        self.salvar()
        caminho = config.caminho_config()
        caminho.write_text(caminho.read_text(encoding="utf-8")
                           + '\n[detector]\nintervalo_coleta = 60\n\n[banco]\ncaminho = "meu.sqlite3"\n', encoding="utf-8")
        self.salvar(modo="taiko")
        cfg = config.carregar()
        self.assertEqual((cfg.intervalo_coleta, cfg.db_path.name, cfg.mode), (60, "meu.sqlite3", "taiko"))


def _tela():
    if tk is None:
        return None
    try:
        raiz = tk.Tk()
    except tk.TclError:  # sem tela (servidor ou CI de Linux)
        return None
    raiz.withdraw()
    return raiz


@unittest.skipIf(janela is None, "Python sem tkinter")
class TestJanela(unittest.TestCase):
    def setUp(self):
        self.raiz = _tela()
        if self.raiz is None:
            self.skipTest("sem tela para abrir a janela")
        self.addCleanup(self.raiz.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (
            mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": self.tmp.name}),
            mock.patch.object(config, "credenciais_do_ambiente", return_value=("", "")),
            mock.patch.object(controle, "suporta_inicio_com_windows", return_value=False),
            mock.patch.object(janela.messagebox, "showerror", side_effect=AssertionError("erro inesperado na janela")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def esperar(self, condicao, limite=5.0):
        fim = time.monotonic() + limite
        while time.monotonic() < fim:
            self.raiz.update()
            if condicao():
                return
            time.sleep(0.02)
        self.fail("a janela nao chegou ao estado esperado")

    def test_primeira_execucao(self):
        j = janela.Janela(self.raiz, criar_api=ApiFalsa)
        self.raiz.update()
        # Sem configuracao: abre na aba Configuracao e o detector nao pode ser iniciado.
        self.assertEqual(j.abas.select(), str(j.aba_config))
        self.assertIn("disabled", j.btn_detector.state())
        self.assertEqual(j.lbl_detector["text"], "Parado")
        self.assertEqual(str(j.ent_segredo["show"]), "•")

        j.ent_id.insert(0, CLIENT_ID)
        self.raiz.clipboard_clear()
        self.raiz.clipboard_append(f"  {SEGREDO_FALSO}\n")
        j.colar(j.ent_segredo)  # o mesmo do botao direito > Colar
        self.assertEqual(j.ent_segredo.get(), SEGREDO_FALSO)
        j.ent_jogador.insert(0, "JogadorTeste")
        j.salvar()
        self.esperar(lambda: "Tudo certo" in str(j.lbl_resultado["text"]))

        self.assertEqual(config.carregar().user_id, 424242)
        self.assertEqual(j.abas.select(), str(j.aba_status))
        self.assertEqual(j.lbl_jogador["text"], "JogadorTeste (modo osu)")
        self.assertNotIn("disabled", j.btn_detector.state())
        self.assertEqual(j.lbl_total["text"], "0")
        self.assertEqual(j.ent_segredo.get(), "")  # o secret salvo nao volta para a tela
        self.assertIn("deixe o campo em branco", str(j.lbl_dica["text"]))

    def test_credenciais_recusadas_aparecem_na_janela(self):
        j = janela.Janela(self.raiz, criar_api=ApiFalsa)
        j.ent_id.insert(0, CLIENT_ID)
        j.ent_segredo.insert(0, "errado")
        j.ent_jogador.insert(0, "JogadorTeste")
        j.salvar()
        self.esperar(lambda: "recusou" in str(j.lbl_resultado["text"]))
        self.assertNotIn("disabled", j.btn_salvar.state())
        self.assertFalse(config.caminho_config().exists())

    def test_botao_do_detector(self):
        (Path(self.tmp.name) / "config.toml").write_text(
            '[jogador]\nuser_id = 1\nmode = "osu"\n[osu_api]\nclient_id = "1"\nclient_secret = "x"\n', encoding="utf-8")
        rodando = {"valor": False}
        with mock.patch.object(controle, "detector_rodando", side_effect=lambda: rodando["valor"]), \
             mock.patch.object(controle, "iniciar_detector", side_effect=lambda: rodando.update(valor=True) or True), \
             mock.patch.object(controle, "parar_detector", side_effect=lambda: rodando.update(valor=False) or True):
            j = janela.Janela(self.raiz, criar_api=ApiFalsa)
            self.assertEqual(j.abas.select(), str(j.aba_status))
            self.assertEqual(j.btn_detector["text"], "Iniciar")
            j.btn_detector.invoke()
            self.esperar(lambda: j.lbl_detector["text"] == "Rodando")
            self.assertEqual(j.btn_detector["text"], "Parar")
            j.btn_detector.invoke()
            self.esperar(lambda: j.lbl_detector["text"] == "Parado")


if __name__ == "__main__":
    unittest.main()

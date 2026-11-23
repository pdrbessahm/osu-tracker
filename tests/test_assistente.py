import logging
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from osu_tracker import __main__ as cli
from osu_tracker import config
from osu_tracker.api import ApiError, NaoEncontrado
from osu_tracker.assistente import Assistente

CLIENT_ID = "12345"
SEGREDO_FALSO = "segredo-falso-de-teste"


class ApiFalsa:
    def __init__(self, client_id, client_secret):
        self.ok = (client_id, client_secret) == (CLIENT_ID, SEGREDO_FALSO)

    def autenticar(self):
        if not self.ok:
            raise ApiError("HTTP 401")

    def buscar_usuario(self, texto, mode=None):
        if texto.lstrip("@").lower() in ("jogadorteste", "424242"):
            return {"id": 424242, "username": "JogadorTeste", "playmode": "osu"}
        raise NaoEncontrado(texto)


class TestAssistente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (
            mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": self.tmp.name}),
            mock.patch.object(config, "credenciais_do_ambiente", return_value=("", "")),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.telas: list[str] = []

    def rodar(self, respostas, senhas, **kwargs):
        respostas, senhas = iter(respostas), iter(senhas)
        a = Assistente(
            entrada=lambda _: next(respostas),
            senha=lambda _: next(senhas),
            saida=self.telas.append,
            criar_api=ApiFalsa,
        )
        return a.executar(**kwargs)

    def test_primeira_execucao_completa(self):
        codigo = self.rodar(
            # id invalido, id certo com segredo errado, id certo, nick errado, nick certo, confirma, modo padrao
            ["abc", CLIENT_ID, CLIENT_ID, "naoexiste", "JogadorTeste", "s", ""],
            ["errado", SEGREDO_FALSO],
        )
        self.assertEqual(codigo, 0)
        texto = "\n".join(self.telas)
        self.assertIn("New OAuth Application", texto)
        self.assertIn("recusou", texto)
        self.assertIn("Nao achei", texto)
        self.assertNotIn(SEGREDO_FALSO, texto)  # o segredo nunca aparece na tela

        caminho = Path(self.tmp.name) / "config.toml"
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
        self.assertEqual(dados["jogador"], {"user_id": 424242, "username": "JogadorTeste", "mode": "osu"})
        self.assertEqual(dados["osu_api"]["client_id"], CLIENT_ID)
        if os.name == "posix":
            self.assertEqual(caminho.stat().st_mode & 0o777, 0o600)

        cfg = config.carregar()
        self.assertEqual((cfg.user_id, cfg.client_secret), (424242, SEGREDO_FALSO))
        self.assertEqual(cfg.db_path, Path(self.tmp.name) / config.BANCO_PADRAO)
        self.assertEqual(cfg.intervalo_coleta, config.INTERVALO_COLETA_PADRAO)

    def test_credenciais_do_ambiente_nao_sao_gravadas(self):
        with mock.patch.object(config, "credenciais_do_ambiente", return_value=(CLIENT_ID, SEGREDO_FALSO)):
            # Com --jogador nada e perguntado; o modo vem do modo principal do jogador.
            codigo = self.rodar([], [], jogador="424242", banco="/tmp/outro.sqlite3")
            self.assertEqual(codigo, 0)
            dados = config.ler_arquivo()
            self.assertNotIn("osu_api", dados)
            self.assertEqual(dados["jogador"]["mode"], "osu")
            self.assertEqual(dados["banco"]["caminho"], "/tmp/outro.sqlite3")
            self.assertEqual(config.carregar().client_secret, SEGREDO_FALSO)

    def test_ja_configurado_nao_muda_sem_confirmar(self):
        self.rodar([CLIENT_ID, "424242", "s", ""], [SEGREDO_FALSO])
        antes = (Path(self.tmp.name) / "config.toml").read_text()
        self.assertEqual(self.rodar(["n"], []), 0)
        self.assertEqual((Path(self.tmp.name) / "config.toml").read_text(), antes)

    def test_sem_configuracao_explica_o_que_falta(self):
        with self.assertRaisesRegex(config.ConfigError, "configurar"):
            config.carregar()

    def test_inicio_sem_terminal_e_sem_configuracao(self):
        # Como no logon do Windows (pythonw): nao ha onde perguntar, entao registra no log o que falta e sai.
        log = logging.getLogger("osu_tracker")
        self.addCleanup(lambda: [log.removeHandler(h) or h.close() for h in list(log.handlers)])
        with mock.patch.object(cli, "_tem_terminal", return_value=False):
            self.assertEqual(cli.main([]), 2)
        registro = (Path(self.tmp.name) / "osu-tracker.log").read_text(encoding="utf-8")
        self.assertIn("ERROR Sem configuracao", registro)
        self.assertIn("python -m osu_tracker configurar", registro)
        self.assertFalse((Path(self.tmp.name) / "config.toml").exists())

    def test_ctrl_d_cancela_sem_gravar(self):
        def eof(_):
            raise EOFError

        a = Assistente(entrada=eof, senha=eof, saida=self.telas.append, criar_api=ApiFalsa)
        self.assertEqual(a.executar(), 2)
        self.assertFalse((Path(self.tmp.name) / "config.toml").exists())


if __name__ == "__main__":
    unittest.main()

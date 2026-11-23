import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from osu_tracker import config, controle, db

RAIZ = Path(__file__).resolve().parent.parent

# Faz o papel do detector: pega a trava, anuncia o PID e espera o pedido de parada (ou o ignora).
DETECTOR_FALSO = textwrap.dedent("""
    import sys, threading, time
    from osu_tracker import controle
    trava = controle.adquirir_trava_detector()
    parar = threading.Event()
    if sys.argv[1] == "obediente":
        controle.anunciar_detector(parar, intervalo=0.05)
    else:
        controle.arquivo_pid().write_text(str(__import__("os").getpid()), encoding="ascii")
    print("pronto", flush=True)
    parar.wait(30)
    controle.encerrar_detector()
    trava.soltar()
""")


def configurar(pasta: Path, **extra) -> None:
    linhas = ['[jogador]', 'user_id = 424242', 'username = "JogadorTeste"', 'mode = "osu"',
              '[osu_api]', 'client_id = "1"', 'client_secret = "x"']
    for secao, valores in extra.items():
        linhas.append(f"[{secao}]")
        linhas += [f'{k} = "{v}"' for k, v in valores.items()]
    (pasta / "config.toml").write_text("\n".join(linhas) + "\n", encoding="utf-8")


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        for p in (
            mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": self.tmp.name}),
            mock.patch.object(config, "credenciais_do_ambiente", return_value=("", "")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_sem_configuracao(self):
        s = controle.ler_status()
        self.assertFalse(s.configurado)
        self.assertFalse(s.rodando)
        self.assertEqual((s.total_jogadas, s.ultima_coleta), (0, None))
        self.assertEqual(controle.texto_coleta(s.ultima_coleta), "nenhuma ainda")

    def test_configuracao_com_erro_de_formato(self):
        (self.pasta / "config.toml").write_text("[jogador\n", encoding="utf-8")
        s = controle.ler_status()
        self.assertFalse(s.configurado)
        self.assertIn("erro de formato", s.erro_config)

    def test_banco_com_coletas(self):
        configurar(self.pasta)
        con = db.conectar(self.pasta / config.BANCO_PADRAO)
        con.execute("INSERT INTO coletas (inicio, fim, recebidas, novas) VALUES "
                    "('2026-11-23T10:00:00Z', '2026-11-23T10:00:02Z', 12, 3)")
        con.execute("INSERT INTO coletas (inicio, fim, recebidas, novas) VALUES "
                    "('2026-11-23T10:02:00Z', '2026-11-23T10:02:01Z', 12, 0)")
        con.commit()
        con.close()

        s = controle.ler_status()
        self.assertTrue(s.configurado)
        self.assertEqual(s.jogador, "JogadorTeste (modo osu)")
        self.assertEqual(s.ultima_coleta["novas"], 0)
        self.assertEqual(s.ultima_com_jogadas["novas"], 3)
        self.assertRegex(controle.texto_coleta(s.ultima_com_jogadas), r"^\d\d/\d\d/2026 \d\d:\d\d, 3 jogadas novas de 12 recebidas$")
        self.assertIn("1 jogada nova de", controle.texto_coleta({"inicio": "2026-11-23T10:00:00Z", "fim": "x", "novas": 1, "recebidas": 5}))
        self.assertIn("em andamento", controle.texto_coleta({"inicio": "2026-11-23T10:00:00Z", "fim": None}))
        self.assertIn("falhou: sem rede", controle.texto_coleta({"inicio": "2026-11-23T10:00:00Z", "fim": "x", "erro": "sem rede"}))

    def test_status_nao_cria_banco_nem_escreve_nele(self):
        configurar(self.pasta, banco={"caminho": "outro.sqlite3"})
        s = controle.ler_status()
        self.assertEqual(s.banco, self.pasta / "outro.sqlite3")
        self.assertFalse(s.banco.exists())
        db.conectar(s.banco).close()
        antes = s.banco.stat().st_mtime_ns
        controle.ler_status()
        self.assertEqual(s.banco.stat().st_mtime_ns, antes)

    def test_banco_ilegivel_vira_aviso(self):
        configurar(self.pasta)
        (self.pasta / config.BANCO_PADRAO).write_bytes(b"isto nao e um banco sqlite" * 100)
        s = controle.ler_status()
        self.assertTrue(s.erro_banco)


class TestDetectorEmSegundoPlano(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"OSU_TRACKER_DIR": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def iniciar_falso(self, tipo: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", DETECTOR_FALSO, tipo], cwd=RAIZ,
                                stdout=subprocess.PIPE, text=True)
        def fim():
            try:
                proc.wait(5)  # depois de parar_detector ele sai sozinho em instantes
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()

        self.addCleanup(fim)
        self.assertEqual(proc.stdout.readline().strip(), "pronto")
        return proc

    def test_parar_pelo_pedido(self):
        proc = self.iniciar_falso("obediente")
        self.assertTrue(controle.detector_rodando())
        self.assertTrue(controle.parar_detector(espera=5, intervalo=0.05))
        self.assertEqual(proc.wait(5), 0)  # saiu sozinho, pelo caminho normal
        self.assertFalse(controle.detector_rodando())
        self.assertFalse(controle.arquivo_parar().exists())
        self.assertFalse(controle.arquivo_pid().exists())

    def test_encerra_o_processo_se_nao_atender(self):
        proc = self.iniciar_falso("teimoso")
        inicio = time.monotonic()
        self.assertTrue(controle.parar_detector(espera=0.5, intervalo=0.05))
        self.assertLess(time.monotonic() - inicio, 5)
        self.assertIsNotNone(proc.wait(5))
        self.assertFalse(controle.detector_rodando())
        self.assertFalse(controle.arquivo_parar().exists())

    def test_nao_encerra_processo_alheio_com_o_mesmo_pid(self):
        controle.arquivo_pid().parent.mkdir(parents=True, exist_ok=True)
        controle.arquivo_pid().write_text(str(os.getpid()), encoding="ascii")  # este processo nao e o detector
        with mock.patch.object(controle.psutil, "Process") as processo:
            processo.return_value.cmdline.return_value = ["algum-outro-programa"]
            self.assertIsNone(controle._processo_do_detector())

    def test_pedido_velho_nao_para_o_detector_novo(self):
        controle.arquivo_parar().parent.mkdir(parents=True, exist_ok=True)
        controle.arquivo_parar().touch()  # sobrou de um detector encerrado a forca
        proc = self.iniciar_falso("obediente")
        time.sleep(0.3)
        self.assertIsNone(proc.poll())
        self.assertTrue(controle.parar_detector(espera=5, intervalo=0.05))

    def test_iniciar_detector_em_processo_separado(self):
        with mock.patch.object(controle.subprocess, "Popen") as popen:
            self.assertTrue(controle.iniciar_detector())
        comando = popen.call_args.args[0]
        opcoes = popen.call_args.kwargs
        self.assertEqual(comando[-3:], ["-m", "osu_tracker", "detectar"])
        self.assertEqual(opcoes["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertIs(opcoes["stdin"], subprocess.DEVNULL)
        if os.name == "nt":
            self.assertTrue(opcoes["creationflags"] & subprocess.DETACHED_PROCESS)
        else:
            self.assertTrue(opcoes["start_new_session"])

    def test_nao_inicia_segundo_detector(self):
        self.iniciar_falso("obediente")
        with mock.patch.object(controle.subprocess, "Popen") as popen:
            self.assertFalse(controle.iniciar_detector())
        popen.assert_not_called()
        controle.parar_detector(espera=5, intervalo=0.05)

    def test_comando_no_executavel(self):
        with mock.patch.object(config, "EXECUTAVEL", True):
            self.assertEqual(controle.comando_detector(), [sys.executable, "detectar"])


class TestInicioComWindows(unittest.TestCase):
    def test_valor_com_hifen_e_aspas_vai_como_texto(self):
        comando = controle.comando_powershell("instalar.ps1", Executavel=r"C:\Jogos\Pedro's\osu-tracker.exe",
                                              Argumentos="-m osu_tracker detectar", SemConfigurar=True, NaoIniciar=False)
        self.assertEqual(comando[:6], ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"])
        script = comando[6]
        self.assertTrue(script.startswith("& '"))
        self.assertIn(str(Path("windows") / "instalar.ps1"), script)
        self.assertIn(r"-Executavel 'C:\Jogos\Pedro''s\osu-tracker.exe'", script)
        self.assertIn("-Argumentos '-m osu_tracker detectar'", script)
        self.assertTrue(script.endswith("-SemConfigurar"))
        self.assertNotIn("NaoIniciar", script)

    def test_scripts_aceitam_os_parametros_usados(self):
        instalar = (RAIZ / "windows" / "instalar.ps1").read_text(encoding="utf-8")
        for nome in ("Executavel", "Argumentos", "Pasta", "SemConfigurar", "NaoIniciar"):
            self.assertRegex(instalar, rf"\[(string|switch)\]\${nome}\b")
        desinstalar = (RAIZ / "windows" / "desinstalar.ps1").read_text(encoding="utf-8")
        self.assertRegex(desinstalar, r"\[switch\]\$ManterRodando\b")

    def test_fora_do_windows(self):
        if sys.platform == "win32":
            self.skipTest("so fora do Windows")
        self.assertFalse(controle.suporta_inicio_com_windows())
        self.assertFalse(controle.inicio_com_windows_ativo())


if __name__ == "__main__":
    unittest.main()

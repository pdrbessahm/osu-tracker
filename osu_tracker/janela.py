"""Janela do osu-tracker: configuracao e status.

Feita em tkinter, que vem com o Python e entra no executavel sem pacote extra.
A janela so configura e mostra; quem coleta e o detector, num processo proprio,
entao fechar a janela nao para nada.
"""

import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from . import config, controle
from .api import OsuApi
from .assistente import Recusado, buscar_jogador, gravar_configuracao, limpar_segredo, testar_credenciais
from .trava import Ocupada, Trava

URL_APPS = "https://osu.ppy.sh/home/account/edit#oauth"
MODO_PRINCIPAL = "modo principal do jogador"
ATUALIZAR_MS = 3000
VERDE, VERMELHO, CINZA = "#1a7f37", "#b42318", "#666666"

COMO_CRIAR_APP = (
    "O osu-tracker lê suas jogadas pela API oficial do osu!, com um app OAuth seu. "
    "Cada jogador cria o próprio e tudo fica neste PC.\n\n"
    "1. Abra a página de apps OAuth (botão abaixo), logado no osu!.\n"
    "2. Clique em \"New OAuth Application\", dê qualquer nome e deixe a Callback URL em branco.\n"
    "3. Copie o Client ID e o Client Secret para os campos abaixo.\n"
    "O Client Secret é como uma senha: não mostre a ninguém."
)


def salvar_configuracao(client_id: str, segredo: str, jogador: str, modo: str,
                        criar_api: Callable[[str, str], OsuApi] = OsuApi) -> dict:
    """Mesmo teste do assistente de console; grava e devolve o jogador achado. Levanta Recusado."""
    try:
        atual = config.ler_arquivo()
    except config.ConfigError:
        atual = {}  # arquivo estragado: a configuracao nova vai por cima, como no console
    env_id, env_secret = config.credenciais_do_ambiente()
    if env_id and env_secret:
        api = testar_credenciais(env_id, env_secret, criar_api)
        credenciais = {}  # do ambiente (.env): nao grava
    else:
        segredo = limpar_segredo(segredo) or str(atual.get("osu_api", {}).get("client_secret", ""))
        api = testar_credenciais(client_id, segredo, criar_api)
        credenciais = {"client_id": client_id.strip(), "client_secret": segredo}
    usuario = buscar_jogador(api, jogador)
    mode = modo if modo in config.MODOS else (usuario.get("playmode") or "osu")
    gravar_configuracao(atual, credenciais, usuario, mode)
    return {**usuario, "mode": mode}


class Janela:
    def __init__(self, raiz: tk.Tk, criar_api: Callable[[str, str], OsuApi] = OsuApi):
        self.raiz = raiz
        self.criar_api = criar_api
        self.ocupado = False  # iniciando ou parando o detector: o status nao sobrescreve o botao
        self.status: controle.Status | None = None
        raiz.title("osu-tracker")
        raiz.minsize(520, 0)
        raiz.report_callback_exception = self._erro_inesperado

        self.abas = ttk.Notebook(raiz)
        self.abas.pack(fill="both", expand=True, padx=8, pady=8)
        self.aba_status = ttk.Frame(self.abas, padding=16)
        self.aba_config = ttk.Frame(self.abas, padding=16)
        self.abas.add(self.aba_status, text="Status")
        self.abas.add(self.aba_config, text="Configuração")
        self._montar_status()
        self._montar_config()

        self.carregar_config()
        self.atualizar()
        if self.status is not None and not self.status.configurado:
            self.abas.select(self.aba_config)
        if controle.suporta_inicio_com_windows():
            self._em_segundo_plano(controle.inicio_com_windows_ativo, self._mostrar_inicio_com_windows)

    # --- montagem ---

    def _montar_status(self) -> None:
        f = self.aba_status
        f.columnconfigure(1, weight=1)

        def linha(n: int, rotulo: str) -> ttk.Label:
            ttk.Label(f, text=rotulo).grid(row=n, column=0, sticky="nw", padx=(0, 16), pady=4)
            valor = ttk.Label(f, text="", wraplength=360, justify="left")
            valor.grid(row=n, column=1, sticky="w", pady=4)
            return valor

        self.lbl_jogador = linha(0, "Jogador")
        ttk.Label(f, text="Detector").grid(row=1, column=0, sticky="w", padx=(0, 16), pady=4)
        caixa = ttk.Frame(f)
        caixa.grid(row=1, column=1, sticky="w", pady=4)
        self.lbl_detector = ttk.Label(caixa, text="")
        self.lbl_detector.pack(side="left")
        self.btn_detector = ttk.Button(caixa, text="Iniciar", command=self.alternar_detector)
        self.btn_detector.pack(side="left", padx=(12, 0))
        self.lbl_coleta = linha(2, "Última coleta")
        self.lbl_com_jogadas = linha(3, "Última com jogadas novas")
        self.lbl_total = linha(4, "Jogadas no banco")

        botoes = ttk.Frame(f)
        botoes.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Button(botoes, text="Abrir pasta de dados", command=self.abrir_pasta).pack(side="left")
        ttk.Button(botoes, text="Abrir log", command=self.abrir_log).pack(side="left", padx=(8, 0))

        self.var_inicio = tk.BooleanVar(value=False)
        texto = "Iniciar o detector junto com o Windows"
        if not controle.suporta_inicio_com_windows():
            texto += " (só no Windows)"
        self.chk_inicio = ttk.Checkbutton(f, text=texto, variable=self.var_inicio, command=self.alternar_inicio)
        self.chk_inicio.grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
        if not controle.suporta_inicio_com_windows():
            self.chk_inicio.state(["disabled"])

        self.lbl_aviso = ttk.Label(f, text="", foreground=VERMELHO, wraplength=480, justify="left")
        self.lbl_aviso.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            f, foreground=CINZA, wraplength=480, justify="left",
            text="Fechar esta janela não para o detector: ele continua guardando suas jogadas em segundo plano.",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _montar_config(self) -> None:
        f = self.aba_config
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text=COMO_CRIAR_APP, wraplength=480, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Abrir a página de apps OAuth do osu!", command=lambda: webbrowser.open(URL_APPS)).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 12))

        self.ent_id = ttk.Entry(f, width=40)
        self.ent_segredo = ttk.Entry(f, width=40, show="•")
        self.ent_jogador = ttk.Entry(f, width=40)
        self.cmb_modo = ttk.Combobox(f, width=37, state="readonly", values=(MODO_PRINCIPAL, *config.MODOS))
        for n, (rotulo, campo) in enumerate((
            ("Client ID", self.ent_id),
            ("Client Secret", self.ent_segredo),
            ("Seu nick ou ID no osu!", self.ent_jogador),
            ("Modo de jogo", self.cmb_modo),
        ), start=2):
            ttk.Label(f, text=rotulo).grid(row=n, column=0, sticky="w", padx=(0, 12), pady=4)
            campo.grid(row=n, column=1, sticky="we", pady=4)
        for campo in (self.ent_id, self.ent_segredo, self.ent_jogador):
            self._menu_de_contexto(campo, oculto=campo is self.ent_segredo)

        self.var_mostrar = tk.BooleanVar(value=False)
        self.chk_mostrar = ttk.Checkbutton(f, text="Mostrar", variable=self.var_mostrar, command=self._mostrar_segredo)
        self.chk_mostrar.grid(row=3, column=2, sticky="w", padx=(8, 0))

        self.lbl_dica = ttk.Label(f, text="", foreground=CINZA, wraplength=480, justify="left")
        self.lbl_dica.grid(row=6, column=0, columnspan=3, sticky="w")
        self.btn_salvar = ttk.Button(f, text="Testar e salvar", command=self.salvar)
        self.btn_salvar.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 4))
        self.lbl_resultado = ttk.Label(f, text="", wraplength=480, justify="left")
        self.lbl_resultado.grid(row=8, column=0, columnspan=3, sticky="w")
        self.ent_segredo.bind("<Return>", lambda _: self.salvar())
        self.ent_jogador.bind("<Return>", lambda _: self.salvar())

    def _menu_de_contexto(self, campo: ttk.Entry, oculto: bool) -> None:
        menu = tk.Menu(campo, tearoff=False)
        if not oculto:  # o secret oculto nao sai do campo por copiar ou recortar
            menu.add_command(label="Recortar", command=lambda: campo.event_generate("<<Cut>>"))
            menu.add_command(label="Copiar", command=lambda: campo.event_generate("<<Copy>>"))
        menu.add_command(label="Colar", command=lambda: self.colar(campo))
        menu.add_separator()
        menu.add_command(label="Limpar", command=lambda: campo.delete(0, "end"))

        def abrir(evento):
            campo.focus_set()
            menu.tk_popup(evento.x_root, evento.y_root)
            menu.grab_release()

        campo.bind("<Button-3>", abrir)
        if sys.platform == "darwin":
            campo.bind("<Button-2>", abrir)
            campo.bind("<Control-Button-1>", abrir)
        # Com teclado de outro alfabeto (ex.: russo) o Tk nao reconhece Ctrl+V; a tecla fisica e a mesma.
        campo.bind("<Control-KeyPress>", self._atalho_por_tecla_fisica)

    @staticmethod
    def _atalho_por_tecla_fisica(evento):
        if sys.platform != "win32" or evento.keysym.lower() in ("v", "c", "x"):
            return None
        acao = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>"}.get(evento.keycode)
        if acao:
            evento.widget.event_generate(acao)
            return "break"
        return None

    def colar(self, campo: ttk.Entry) -> None:
        try:
            texto = self.raiz.clipboard_get()
        except tk.TclError:
            return  # area de transferencia vazia ou sem texto
        if campo.selection_present():
            campo.delete("sel.first", "sel.last")
        campo.insert("insert", texto.strip())

    def _mostrar_segredo(self) -> None:
        self.ent_segredo.configure(show="" if self.var_mostrar.get() else "•")

    # --- configuracao ---

    def carregar_config(self) -> None:
        try:
            atual = config.ler_arquivo()
        except config.ConfigError as e:
            atual = {}
            self._resultado(f"{e}\nSalvar aqui cria uma configuração nova.", VERMELHO)
        api = atual.get("osu_api", {})
        jogador = atual.get("jogador", {})
        self._trocar_texto(self.ent_id, str(api.get("client_id", "")))
        self._trocar_texto(self.ent_jogador, str(jogador.get("username") or jogador.get("user_id") or ""))
        self.cmb_modo.set(jogador.get("mode") if jogador.get("mode") in config.MODOS else MODO_PRINCIPAL)
        self.ent_segredo.delete(0, "end")

        env_id, env_secret = config.credenciais_do_ambiente()
        if env_id and env_secret:
            for campo in (self.ent_id, self.ent_segredo):
                campo.delete(0, "end")
                campo.state(["disabled"])
            self.chk_mostrar.state(["disabled"])
            self.lbl_dica.configure(text="Usando OSU_CLIENT_ID e OSU_CLIENT_SECRET do ambiente (.env); "
                                         "eles têm prioridade e não são gravados.")
        elif api.get("client_secret"):
            self.lbl_dica.configure(text="Já há um Client Secret salvo: deixe o campo em branco para manter.")
        else:
            self.lbl_dica.configure(text="Para colar: Ctrl+V ou botão direito do mouse > Colar.")

    @staticmethod
    def _trocar_texto(campo: ttk.Entry, texto: str) -> None:
        campo.delete(0, "end")
        campo.insert(0, texto)

    def _resultado(self, texto: str, cor: str = "") -> None:
        self.lbl_resultado.configure(text=texto, foreground=cor)

    def salvar(self) -> None:
        if str(self.btn_salvar["state"]) == "disabled":
            return
        dados = (self.ent_id.get(), self.ent_segredo.get(), self.ent_jogador.get(), self.cmb_modo.get())
        self.btn_salvar.state(["disabled"])
        self._resultado("Testando com o osu!...", CINZA)
        self._em_segundo_plano(lambda: salvar_configuracao(*dados, criar_api=self.criar_api), self._salvou)

    def _salvou(self, usuario: dict | None, erro: BaseException | None) -> None:
        self.btn_salvar.state(["!disabled"])
        if isinstance(erro, Recusado):
            self._resultado(str(erro), VERMELHO)
            return
        if erro is not None:
            raise erro
        self.carregar_config()
        self._resultado(
            f"Tudo certo: {usuario['username']} (ID {usuario['id']}), modo {usuario['mode']}. Configuração salva.", VERDE)
        self.atualizar()
        self.abas.select(self.aba_status)
        if self.status is not None and self.status.rodando:
            # O detector leu a configuracao antiga ao iniciar: reinicia para valer a nova.
            self._detector_em_segundo_plano("Reiniciando...", lambda: controle.parar_detector() and self._iniciar_e_esperar())

    # --- status ---

    def atualizar(self) -> None:
        self.status = s = controle.ler_status()
        self.lbl_jogador.configure(text=s.jogador or "ainda não configurado (aba Configuração)")
        if not self.ocupado:
            self.lbl_detector.configure(text="Rodando" if s.rodando else "Parado", foreground=VERDE if s.rodando else VERMELHO)
            self.btn_detector.configure(text="Parar" if s.rodando else "Iniciar")
            # Sem configuracao o detector so registraria o erro e sairia.
            self.btn_detector.state(["!disabled"] if s.rodando or s.configurado else ["disabled"])
        self.lbl_coleta.configure(text=controle.texto_coleta(s.ultima_coleta))
        self.lbl_com_jogadas.configure(text=controle.texto_coleta(s.ultima_com_jogadas))
        self.lbl_total.configure(text=str(s.total_jogadas))
        self.lbl_aviso.configure(text=s.erro_config or (f"Erro ao ler o banco: {s.erro_banco}" if s.erro_banco else ""))
        if self._agendado is not None:
            self.raiz.after_cancel(self._agendado)
        self._agendado = self.raiz.after(ATUALIZAR_MS, self.atualizar)

    _agendado: str | None = None

    def alternar_detector(self) -> None:
        if self.status is not None and self.status.rodando:
            self._detector_em_segundo_plano("Parando...", controle.parar_detector)
        else:
            self._detector_em_segundo_plano("Iniciando...", self._iniciar_e_esperar)

    @staticmethod
    def _iniciar_e_esperar() -> bool:
        controle.iniciar_detector()
        # O processo novo leva um instante para pegar a trava; ate la o status diria "Parado".
        for _ in range(50):
            if controle.detector_rodando():
                return True
            threading.Event().wait(0.2)
        return False

    def _detector_em_segundo_plano(self, texto: str, acao: Callable[[], object]) -> None:
        self.ocupado = True
        self.lbl_detector.configure(text=texto, foreground=CINZA)
        self.btn_detector.state(["disabled"])

        def fim(resultado, erro):
            self.ocupado = False
            self.atualizar()
            if erro is not None:
                raise erro
            if texto == "Parando..." and not resultado:
                messagebox.showwarning("osu-tracker", "O detector não parou. Veja o log.", parent=self.raiz)
            if texto != "Parando..." and not self.status.rodando:
                messagebox.showwarning("osu-tracker", "O detector não ficou rodando. Veja o log.", parent=self.raiz)

        self._em_segundo_plano(acao, fim)

    def abrir_pasta(self) -> None:
        pasta = config.pasta_app()
        pasta.mkdir(parents=True, exist_ok=True)
        controle.abrir(pasta)

    def abrir_log(self) -> None:
        caminho = config.caminho_log()
        if not caminho.exists():
            messagebox.showinfo("osu-tracker", f"Ainda não há log em {caminho}.\nEle aparece quando o detector rodar.",
                                parent=self.raiz)
            return
        controle.abrir(caminho)

    def _mostrar_inicio_com_windows(self, ativo, erro) -> None:
        self.var_inicio.set(bool(ativo) and erro is None)

    def alternar_inicio(self) -> None:
        querer = self.var_inicio.get()
        self.chk_inicio.state(["disabled"])

        def fim(_, erro):
            self.chk_inicio.state(["!disabled"])
            if erro is not None:
                self.var_inicio.set(not querer)
                messagebox.showerror("osu-tracker", f"Não consegui {'ativar' if querer else 'desativar'} o inicio "
                                                    f"com o Windows:\n{erro}", parent=self.raiz)

        acao = controle.ativar_inicio_com_windows if querer else controle.desativar_inicio_com_windows
        self._em_segundo_plano(acao, fim)

    # --- utilidades ---

    def _em_segundo_plano(self, funcao: Callable[[], object], ao_terminar: Callable[[object, BaseException | None], None]) -> None:
        """Roda fora da tela (rede, processos) e chama ao_terminar(resultado, erro) de volta na tela."""
        saida: dict = {}

        def trabalho():
            try:
                saida["resultado"] = funcao()
            except BaseException as e:  # noqa: BLE001 (repassado para a tela)
                saida["erro"] = e
            saida["fim"] = True

        def conferir():
            if "fim" in saida:
                ao_terminar(saida.get("resultado"), saida.get("erro"))
            else:
                self.raiz.after(100, conferir)

        threading.Thread(target=trabalho, daemon=True).start()
        self.raiz.after(100, conferir)

    def _erro_inesperado(self, tipo, valor, tb) -> None:
        detalhe = "".join(traceback.format_exception(tipo, valor, tb))
        messagebox.showerror("osu-tracker", f"Erro inesperado:\n{valor}\n\n{detalhe[-1500:]}", parent=self.raiz)


def _nitidez_no_windows() -> None:
    """Sem isso o Windows estica a janela em telas com escala acima de 100% e o texto fica borrado."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> int:
    _nitidez_no_windows()
    trava = Trava(config.pasta_app() / "janela.lock")
    try:
        trava.adquirir()
    except Ocupada:
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showinfo("osu-tracker", "O osu-tracker já está aberto em outra janela.")
        raiz.destroy()
        return 0
    try:
        raiz = tk.Tk()
        Janela(raiz)
        raiz.mainloop()
    finally:
        trava.soltar()
    return 0

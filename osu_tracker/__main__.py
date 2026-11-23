"""Linha de comando do osu-tracker.

    python -m osu_tracker              detector (roda o assistente antes, se ainda nao houver configuracao)
    python -m osu_tracker configurar   assistente de configuracao
    python -m osu_tracker coletar      uma coleta agora
    python -m osu_tracker detectar     so o detector
    python -m osu_tracker janela       janela de configuracao e status (padrao no osu-tracker.exe)
    python -m osu_tracker painel       painel com os graficos, no navegador
    python -m osu_tracker marcar       marca o tipo de um mapa (stream, jump, tech, alternate ou nenhum)
"""

import argparse
import dataclasses
import logging
import signal
import sys
import threading

from . import config, coletor, controle, registro
from .api import OsuApi
from .assistente import Assistente
from .detector import Detector, processo_aberto
from .trava import Ocupada

log = logging.getLogger("osu_tracker")


def _tem_terminal() -> bool:
    return sys.stdin is not None and sys.stdin.isatty() and sys.stdout is not None


def _carregar() -> config.Config | None:
    try:
        return config.carregar()
    except config.ConfigError as e:
        log.error("%s", e)
        return None


def cmd_configurar(args) -> int:
    return Assistente().executar(jogador=args.jogador, modo=args.modo, banco=args.banco)


def cmd_coletar(args) -> int:
    registro.configurar()
    cfg = _carregar()
    if cfg is None:
        return coletor.SEM_CONFIG
    return coletor.coletar(cfg, motivo="manual")


def cmd_detectar(args) -> int:
    registro.configurar()
    cfg = _carregar()
    if cfg is None:
        return coletor.SEM_CONFIG
    if args.intervalo_checagem:
        cfg = dataclasses.replace(cfg, intervalo_checagem=args.intervalo_checagem)
    if args.intervalo_coleta:
        cfg = dataclasses.replace(cfg, intervalo_coleta=args.intervalo_coleta)

    try:
        instancia = controle.adquirir_trava_detector()
    except Ocupada:
        log.info("O detector ja esta rodando; esta copia vai sair.")
        return coletor.PULADA

    api = OsuApi(cfg.client_id, cfg.client_secret)  # um token so, reaproveitado entre coletas
    detector = Detector(
        jogo_aberto=lambda: processo_aberto(cfg.processos),
        coletar=lambda motivo: coletor.coletar(cfg, api, motivo),
        intervalo_checagem=cfg.intervalo_checagem,
        intervalo_coleta=cfg.intervalo_coleta,
    )
    parar = threading.Event()
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: parar.set())
    try:
        controle.anunciar_detector(parar)  # PID e pedido de parada vindo da janela
        detector.rodar(parar)
    except KeyboardInterrupt:
        log.info("Detector encerrado pelo usuario")
    finally:
        controle.encerrar_detector()
        instancia.soltar()
    return 0


def cmd_janela(args) -> int:
    from . import janela  # so carrega o tkinter quando a janela e pedida
    return janela.main()


def cmd_painel(args) -> int:
    registro.configurar()
    from . import painel  # so carrega o servidor web quando o painel e pedido

    def avisar(url: str) -> None:
        if sys.stdout is not None:
            print(f"Painel em {url}  (Ctrl+C para fechar)", flush=True)

    painel.servir(porta=args.porta, abrir_navegador=not args.sem_navegador, ao_iniciar=avisar)
    return 0


def cmd_marcar(args) -> int:
    """Marca o tipo de um mapa, ou lista os mapas que mais ensinariam o classificador."""
    from . import db, skillsets
    registro.configurar()
    cfg = _carregar()
    if cfg is None:
        return coletor.SEM_CONFIG
    con = db.conectar(cfg.db_path)
    try:
        if args.sugerir or args.beatmap_id is None:
            classes = skillsets.classificar(con)
            nomes = dict(con.execute("SELECT beatmap_id, COALESCE(artista || ' - ', '') || titulo || ' [' || versao || ']' FROM mapas"))
            sugeridos = skillsets.para_marcar(classes)
            if not sugeridos:
                print("Nenhum mapa para marcar: ainda nao ha mapas lidos sem marcacao.")
            for bid, tipo, conf in sugeridos:
                tipos = ", ".join(classes[bid]["tipos"]) or "nenhum"
                print(f"{bid}  {nomes.get(bid) or 'Mapa ' + str(bid)}\n    agora: {tipos}; mais duvida em {tipo} ({conf:.0%})")
            return 0
        if args.limpar:
            skillsets.desmarcar(con, args.beatmap_id)
            print(f"Marcacao do mapa {args.beatmap_id} removida: volta a valer o classificador.")
        else:
            tipos = set(args.tipos)
            if not tipos:
                print("Diga os tipos (stream, jump, tech, alternate) ou nenhum.", file=sys.stderr)
                return 2
            if "nenhum" in tipos:
                if len(tipos) > 1:
                    print("'nenhum' nao combina com outros tipos.", file=sys.stderr)
                    return 2
                tipos = set()
            try:
                skillsets.marcar(con, args.beatmap_id, tipos)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 2
            print(f"Mapa {args.beatmap_id} marcado como {', '.join(sorted(tipos)) or 'nenhum'}.")
        for tipo in skillsets.atualizar_modelos(con):
            print(f"Modelo de {tipo} treinado de novo.")
        return 0
    finally:
        con.close()


def _usar_console_de_quem_chamou() -> None:
    """O osu-tracker.exe e de janela (sem console proprio, para o duplo clique nao abrir um console).

    Chamado de um terminal com um comando (coletar, detectar, --help), escreve nesse terminal.
    """
    if not (config.EXECUTAVEL and sys.platform == "win32" and sys.stdout is None):
        return
    import ctypes
    import psutil
    # No executavel de arquivo unico o pai direto e o carregador do PyInstaller, que nao tem console;
    # o terminal e o pai dele.
    try:
        pai = psutil.Process().parent()
        candidatos = [p.pid for p in (pai, pai.parent() if pai else None) if p is not None]
    except psutil.Error:
        return
    if not any(ctypes.windll.kernel32.AttachConsole(pid) for pid in candidatos):
        return
    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115 (vive ate o fim)
    sys.stderr = sys.stdout


def cmd_padrao(args) -> int:
    if not config.caminho_config().exists():
        if not _tem_terminal():
            registro.configurar()
            log.error("Sem configuracao em %s. Rode uma vez num terminal: python -m osu_tracker configurar",
                      config.caminho_config())
            return coletor.SEM_CONFIG
        codigo = Assistente().executar()
        if codigo != 0:
            return codigo
    return cmd_detectar(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m osu_tracker", description="Guarda suas jogadas de osu! num banco local.")
    parser.set_defaults(func=cmd_padrao, intervalo_checagem=None, intervalo_coleta=None)
    sub = parser.add_subparsers(title="comandos")

    p = sub.add_parser("configurar", help="assistente de configuracao (credenciais e jogador)")
    p.add_argument("--jogador", help="ID ou nick (sem perguntar)")
    p.add_argument("--modo", choices=config.MODOS)
    p.add_argument("--banco", help="caminho do banco SQLite (padrao: na pasta do osu-tracker)")
    p.set_defaults(func=cmd_configurar)

    p = sub.add_parser("coletar", help="faz uma coleta agora")
    p.set_defaults(func=cmd_coletar)

    p = sub.add_parser("detectar", help="fica de olho no jogo e coleta enquanto ele esta aberto")
    p.add_argument("--intervalo-checagem", type=float, help="segundos entre conferencias do processo")
    p.add_argument("--intervalo-coleta", type=float, help="segundos entre coletas com o jogo aberto")
    p.set_defaults(func=cmd_detectar)

    p = sub.add_parser("janela", help="janela de configuracao e status")
    p.set_defaults(func=cmd_janela)

    p = sub.add_parser("painel", help="painel com os graficos, no navegador (so neste PC)")
    p.add_argument("--porta", type=int, default=0, help="porta em 127.0.0.1 (padrao: uma livre)")
    p.add_argument("--sem-navegador", action="store_true", help="so sobe o painel, sem abrir o navegador")
    p.set_defaults(func=cmd_painel)

    p = sub.add_parser("marcar", help="marca o tipo de um mapa (ensina o classificador de skillsets)",
                       description="Ex.: marcar 5587991 stream jump | marcar 5587991 nenhum | "
                                   "marcar 5587991 --limpar | marcar --sugerir")
    p.add_argument("beatmap_id", type=int, nargs="?", help="ID do beatmap (a dificuldade, nao o set)")
    p.add_argument("tipos", nargs="*", metavar="tipo", help="stream, jump, tech, alternate (um ou mais) ou nenhum")
    p.add_argument("--limpar", action="store_true", help="tira a marcacao do mapa")
    p.add_argument("--sugerir", action="store_true", help="lista os mapas em que o classificador tem mais duvida")
    p.set_defaults(func=cmd_marcar)

    if argv is None:
        argv = sys.argv[1:]
    if not argv and config.EXECUTAVEL:
        argv = ["janela"]  # duplo clique no osu-tracker.exe abre a janela
    if argv[:1] != ["janela"]:
        _usar_console_de_quem_chamou()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

"""Linha de comando do osu-tracker.

    python -m osu_tracker              detector (roda o assistente antes, se ainda nao houver configuracao)
    python -m osu_tracker configurar   assistente de configuracao
    python -m osu_tracker coletar      uma coleta agora
    python -m osu_tracker detectar     so o detector
"""

import argparse
import dataclasses
import logging
import signal
import sys
import threading

from . import config, coletor, registro
from .api import OsuApi
from .assistente import Assistente
from .detector import Detector, processo_aberto
from .trava import Ocupada, Trava

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

    instancia = Trava(config.pasta_app() / "detector.lock")
    try:
        instancia.adquirir()
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
        detector.rodar(parar)
    except KeyboardInterrupt:
        log.info("Detector encerrado pelo usuario")
    finally:
        instancia.soltar()
    return 0


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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

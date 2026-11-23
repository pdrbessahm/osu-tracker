"""Coleta as jogadas recentes (com fails) e grava no SQLite sem duplicar."""

import logging
from datetime import datetime, timezone

from . import config, db, mapas
from .api import ApiError, OsuApi
from .trava import Ocupada, Trava

log = logging.getLogger("osu_tracker")

OK, FALHOU, SEM_CONFIG, PULADA = 0, 1, 2, 3


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trava_coleta() -> Trava:
    return Trava(config.pasta_app() / "coleta.lock")


def coletar(cfg: config.Config, api: OsuApi | None = None, motivo: str = "manual") -> int:
    """Uma coleta. So roda se nenhuma outra estiver rodando (neste ou em outro processo)."""
    trava = trava_coleta()
    try:
        trava.adquirir()
    except Ocupada:
        log.info("Coleta (%s) pulada: outra coleta esta em andamento", motivo)
        return PULADA
    try:
        return _coletar(cfg, api or OsuApi(cfg.client_id, cfg.client_secret), motivo)
    finally:
        trava.soltar()


def _coletar(cfg: config.Config, api: OsuApi, motivo: str) -> int:
    con = db.conectar(cfg.db_path)
    try:
        cur = con.execute("INSERT INTO coletas (inicio) VALUES (?)", (_agora(),))
        coleta_id = cur.lastrowid
        con.commit()
        try:
            scores = api.jogadas_recentes(cfg.user_id, cfg.mode)
            with con:
                novas = db.gravar_jogadas(con, scores)
                con.execute(
                    "UPDATE coletas SET fim = ?, recebidas = ?, novas = ? WHERE id = ?",
                    (_agora(), len(scores), novas, coleta_id),
                )
            total = con.execute("SELECT COUNT(*) FROM jogadas").fetchone()[0]
            log.info("Coleta (%s) ok: %d recebidas, %d novas, %d no banco", motivo, len(scores), novas, total)
        except (ApiError, KeyError, ValueError) as e:
            with con:
                con.execute("UPDATE coletas SET fim = ?, erro = ? WHERE id = ?", (_agora(), str(e), coleta_id))
            log.error("Coleta (%s) falhou: %s", motivo, e)
            return FALHOU
        # As jogadas ja estao salvas: um problema no enriquecimento fica no log e a proxima coleta continua.
        try:
            buscados, faltam = mapas.enriquecer(con, api)
        except (KeyError, ValueError, TypeError) as e:
            log.error("Enriquecimento dos mapas falhou: %r", e)
        else:
            if buscados or faltam:
                log.info("Mapas: %d combinacoes de mods enriquecidas, %d para as proximas coletas", buscados, faltam)
        return OK
    finally:
        con.close()

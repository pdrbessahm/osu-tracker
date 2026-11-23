"""Detector do jogo: substitui o agendamento fixo.

- Jogo fechado: so confere se o processo do osu! existe, a cada poucos segundos, sem chamar a API.
- Jogo aberto: coleta a cada ~2 min.
- Jogo fechou: coleta final.
A API guarda as jogadas recentes por um tempo, entao uma coleta perdida (rede caiu, PC desligou
logo depois do jogo) e recuperada na proxima sessao.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable

import psutil

log = logging.getLogger("osu_tracker")


def processo_aberto(nomes: Iterable[str]) -> bool:
    alvo = {n.lower() for n in nomes}
    for proc in psutil.process_iter(["name"]):
        nome = proc.info.get("name") or ""
        if nome.lower() in alvo:
            return True
    return False


class Detector:
    def __init__(
        self,
        jogo_aberto: Callable[[], bool],
        coletar: Callable[[str], object],
        intervalo_checagem: float,
        intervalo_coleta: float,
        relogio: Callable[[], float] = time.monotonic,
    ):
        self._jogo_aberto = jogo_aberto
        self._coletar = coletar
        self.intervalo_checagem = intervalo_checagem
        self.intervalo_coleta = intervalo_coleta
        self._relogio = relogio
        self.aberto = False
        self._proxima_coleta = 0.0

    def passo(self) -> None:
        """Uma conferencia do processo e, se for a hora, uma coleta."""
        try:
            aberto = self._jogo_aberto()
        except psutil.Error as e:
            log.warning("Nao consegui listar os processos: %s", e)
            return
        if aberto and not self.aberto:
            self.aberto = True
            self._proxima_coleta = self._relogio() + self.intervalo_coleta
            log.info("osu! aberto: coletando a cada %.0f s", self.intervalo_coleta)
        elif aberto and self._relogio() >= self._proxima_coleta:
            self._coletar_sem_derrubar("periodica")
            self._proxima_coleta = self._relogio() + self.intervalo_coleta
        elif not aberto and self.aberto:
            self.aberto = False
            log.info("osu! fechado: coleta final")
            self._coletar_sem_derrubar("final")

    def _coletar_sem_derrubar(self, motivo: str) -> None:
        # O detector fica no ar o dia todo: erro inesperado numa coleta nao pode encerra-lo.
        try:
            self._coletar(motivo)
        except Exception:
            log.exception("Erro inesperado na coleta (%s)", motivo)

    def rodar(self, parar: threading.Event) -> None:
        log.info("Detector iniciado: conferindo o processo do osu! a cada %.0f s", self.intervalo_checagem)
        while not parar.is_set():
            self.passo()
            parar.wait(self.intervalo_checagem)
        log.info("Detector encerrado")

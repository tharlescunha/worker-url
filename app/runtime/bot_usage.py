from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

# Protege leitura/gravação do bots_registry.json entre threads
# (várias tasks e o sync de bots rodam ao mesmo tempo).
REGISTRY_LOCK = threading.RLock()

_INSTALL_WAIT_TIMEOUT_SECONDS = 900


class BotBusyError(RuntimeError):
    pass


class BotUsage:
    """
    Coordena uso x atualização de cada bot no disco.

    - Execuções do mesmo bot podem rodar juntas.
    - Instalar/atualizar um bot (git reset, pip) exige que nenhuma execução
      dele esteja em andamento, e impede novas execuções durante a operação.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._running: dict[str, int] = {}
        self._installing: set[str] = set()

    def running_count(self, bot_id: str) -> int:
        with self._cond:
            return self._running.get(bot_id, 0)

    @contextmanager
    def running(self, bot_id: str) -> Iterator[None]:
        bot_id = str(bot_id)

        with self._cond:
            ok = self._cond.wait_for(
                lambda: bot_id not in self._installing,
                timeout=_INSTALL_WAIT_TIMEOUT_SECONDS,
            )
            if not ok:
                raise BotBusyError(
                    f"Bot {bot_id} continua em atualização após "
                    f"{_INSTALL_WAIT_TIMEOUT_SECONDS}s."
                )
            self._running[bot_id] = self._running.get(bot_id, 0) + 1

        try:
            yield
        finally:
            with self._cond:
                self._running[bot_id] = max(0, self._running.get(bot_id, 1) - 1)
                self._cond.notify_all()

    @contextmanager
    def exclusive(self, bot_id: str) -> Iterator[None]:
        """Espera o bot ficar ocioso e o reserva para instalação/atualização."""
        bot_id = str(bot_id)

        with self._cond:
            ok = self._cond.wait_for(
                lambda: bot_id not in self._installing
                and self._running.get(bot_id, 0) == 0,
                timeout=_INSTALL_WAIT_TIMEOUT_SECONDS,
            )
            if not ok:
                raise BotBusyError(
                    f"Bot {bot_id} em uso por outra execução; atualização não pôde começar."
                )
            self._installing.add(bot_id)

        try:
            yield
        finally:
            with self._cond:
                self._installing.discard(bot_id)
                self._cond.notify_all()

    def try_exclusive(self, bot_id: str):
        """
        Como `exclusive`, mas sem esperar. Devolve um context manager ou None
        se o bot estiver em uso (o chamador deve adiar a atualização).
        """
        bot_id = str(bot_id)

        with self._cond:
            if bot_id in self._installing or self._running.get(bot_id, 0) > 0:
                return None
            self._installing.add(bot_id)

        @contextmanager
        def _release() -> Iterator[None]:
            try:
                yield
            finally:
                with self._cond:
                    self._installing.discard(bot_id)
                    self._cond.notify_all()

        return _release()


bot_usage = BotUsage()

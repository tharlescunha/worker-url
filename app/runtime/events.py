from __future__ import annotations

import threading
import time

from app.runtime.task_client import TaskApiClient

_DEDUPE_WINDOW_SECONDS = 600


class EventReporter:
    """
    Envia eventos do worker ao backend (best effort).

    Nunca levanta exceção e não repete o mesmo evento dentro de uma janela,
    para que falha recorrente (ex.: sync a cada 5 min) não vire spam.
    """

    def __init__(self, api: TaskApiClient, logger) -> None:
        self.api = api
        self.logger = logger
        self._lock = threading.Lock()
        self._last_sent: dict[tuple[str, str], float] = {}

    def report(
        self,
        event_type: str,
        message: str,
        *,
        reason: str | None = None,
        task_id: int | None = None,
        bot_id: int | str | None = None,
        execution_mode: str | None = None,
        dedupe: bool = True,
    ) -> None:
        key = (event_type, f"{reason}|{bot_id}|{message[:200]}")
        now = time.monotonic()

        if dedupe:
            with self._lock:
                last = self._last_sent.get(key)
                if last is not None and now - last < _DEDUPE_WINDOW_SECONDS:
                    return
                self._last_sent[key] = now

        try:
            self.api.send_runtime_event(
                event_type=event_type,
                task_id=task_id,
                bot_id=bot_id,
                execution_mode=execution_mode,
                reason=reason,
                message=message[:2000],
            )
        except Exception as exc:
            self.logger.warning(
                "Não foi possível enviar evento ao backend | tipo=%s erro=%s",
                event_type,
                exc,
            )

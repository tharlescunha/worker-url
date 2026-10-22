"""
Envia as linhas do worker.log para o backend (tela de Logs do painel).

Um handler de logging guarda cada linha numa fila em memória (limitada) e uma
thread manda em lotes a cada poucos segundos. Nada aqui pode atrapalhar a
execução dos robôs: se o backend estiver fora, a fila só descarta as mais antigas;
backend antigo (404) desliga o envio por um tempo.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import UTC, datetime

from app.core.exceptions import ApiRequestError
from app.core.http_client import HttpClient

RUNNER_LOGS_PATH = "/api/v1/worker/runner-logs/"

SHIP_INTERVAL_SECONDS = 3
MAX_LINES_PER_BATCH = 500  # mesmo limite do backend
MAX_QUEUED_LINES = 5000
MAX_MESSAGE_LENGTH = 4000
MAX_BACKOFF_SECONDS = 60
DISABLED_AFTER_404_SECONDS = 600


class RemoteLogHandler(logging.Handler):
    """Guarda as linhas do log para a thread de envio. Nunca bloqueia nem levanta erro."""

    def __init__(self, max_lines: int = MAX_QUEUED_LINES) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(message)s"))
        self._lines: deque[dict] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = {
                "logged_at": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "message": self.format(record)[:MAX_MESSAGE_LENGTH],
            }
            with self._lock:
                self._lines.append(line)
        except Exception:
            pass

    def take(self, limit: int) -> list[dict]:
        with self._lock:
            batch = []
            while self._lines and len(batch) < limit:
                batch.append(self._lines.popleft())
            return batch

    def give_back(self, batch: list[dict]) -> None:
        """Devolve um lote que falhou para a frente da fila (o mais antigo sai se lotar)."""
        with self._lock:
            room = self._lines.maxlen - len(self._lines)
            for line in reversed(batch[-room:] if room > 0 else []):
                self._lines.appendleft(line)

    def pending(self) -> int:
        with self._lock:
            return len(self._lines)


class LogShipperThread(threading.Thread):
    def __init__(
        self,
        handler: RemoteLogHandler,
        base_url: str,
        runner_uuid: str,
        runner_token: str,
        logger: logging.Logger,
        client: HttpClient | None = None,
    ) -> None:
        super().__init__(daemon=True, name="worker-log-shipper")
        self.handler = handler
        # Client próprio, sem JWT: a rota do worker autentica por uuid+token.
        self.client = client or HttpClient(base_url=base_url)
        self.runner_uuid = runner_uuid
        self.runner_token = runner_token
        self.logger = logger
        self._stop_event = threading.Event()
        self._failures = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            wait = self.ship_once()
            self._stop_event.wait(wait)

    def ship_once(self) -> float:
        """Envia o que estiver na fila. Devolve quantos segundos esperar até a próxima vez."""
        while True:
            batch = self.handler.take(MAX_LINES_PER_BATCH)
            if not batch:
                break

            try:
                self.client.post(
                    RUNNER_LOGS_PATH,
                    {"uuid": self.runner_uuid, "token": self.runner_token, "lines": batch},
                    retry_auth=False,
                )
            except ApiRequestError as exc:
                if "HTTP 404" in str(exc):
                    # Backend sem a tela de Logs: descarta e tenta de novo mais tarde.
                    self.logger.warning(
                        "Backend sem suporte a envio de log (404); envio pausado por %ss.",
                        DISABLED_AFTER_404_SECONDS,
                    )
                    return DISABLED_AFTER_404_SECONDS
                return self._on_failure(batch, exc)
            except Exception as exc:
                return self._on_failure(batch, exc)

            if self._failures:
                self.logger.info("Envio do log para o painel restabelecido.")
            self._failures = 0

            if len(batch) < MAX_LINES_PER_BATCH:
                break

        return SHIP_INTERVAL_SECONDS

    def _on_failure(self, batch: list[dict], exc: Exception) -> float:
        self.handler.give_back(batch)
        self._failures += 1
        if self._failures == 1 or self._failures % 20 == 0:
            self.logger.warning(
                "Falha ao enviar log para o painel (%s seguidas): %s", self._failures, exc
            )
        return min(MAX_BACKOFF_SECONDS, SHIP_INTERVAL_SECONDS * (2 ** min(self._failures, 5)))

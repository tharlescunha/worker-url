from __future__ import annotations

import threading
from dataclasses import dataclass

from app.core.config_models import RunnerData
from app.core.constants import (
    EXECUTION_MODE_BACKGROUND,
    EXECUTION_MODE_FOREGROUND,
    MAX_BACKGROUND_CONCURRENCY_CAP,
    MAX_FOREGROUND_CONCURRENCY,
)
from app.core.http_client import HttpClient
from app.runtime.events import EventReporter
from app.runtime.task_executor import execute_task, get_execution_mode


@dataclass
class RunningTask:
    task_id: int
    thread: threading.Thread
    execution_mode: str


class TaskExecutionManager:
    """
    Gerenciador de execução local.

    Regras de concorrência:
    - até N tasks BACKGROUND ao mesmo tempo (N = max_concurrency do runner,
      limitado a MAX_BACKGROUND_CONCURRENCY_CAP);
    - no máximo 1 task FOREGROUND (precisa de foco de tela) por vez;
    - uma foreground pode rodar junto com várias background.
    """

    def __init__(
        self,
        client: HttpClient,
        runner: RunnerData,
        logger,
        events: EventReporter | None = None,
    ) -> None:
        self.client = client
        self.runner = runner
        self.logger = logger
        self.events = events

        self._lock = threading.Lock()
        self._running: dict[int, RunningTask] = {}

    # --- limites ---------------------------------------------------------

    def background_limit(self) -> int:
        configured = int(self.runner.config.max_concurrency or 1)
        return max(1, min(configured, MAX_BACKGROUND_CONCURRENCY_CAP))

    def foreground_limit(self) -> int:
        return MAX_FOREGROUND_CONCURRENCY

    def limit_for(self, execution_mode: str) -> int:
        if execution_mode == EXECUTION_MODE_FOREGROUND:
            return self.foreground_limit()
        return self.background_limit()

    # --- contagens -------------------------------------------------------

    def cleanup_finished(self) -> None:
        with self._lock:
            finished_ids = [
                task_id
                for task_id, item in self._running.items()
                if not item.thread.is_alive()
            ]

            for task_id in finished_ids:
                self._running.pop(task_id, None)

    def active_count(self, execution_mode: str | None = None) -> int:
        self.cleanup_finished()

        with self._lock:
            return sum(
                1
                for item in self._running.values()
                if execution_mode is None or item.execution_mode == execution_mode
            )

    def has_capacity(self, execution_mode: str) -> bool:
        return self.active_count(execution_mode) < self.limit_for(execution_mode)

    def available_modes(self) -> list[str]:
        """Modos que ainda comportam uma task, foreground primeiro (é o escasso)."""
        return [
            mode
            for mode in (EXECUTION_MODE_FOREGROUND, EXECUTION_MODE_BACKGROUND)
            if self.has_capacity(mode)
        ]

    # --- início de task --------------------------------------------------

    def can_start_task(self, task_data: dict) -> tuple[bool, str]:
        task_id = int(task_data["task_id"])
        execution_mode = get_execution_mode(task_data)

        with self._lock:
            if task_id in self._running:
                return False, "Task já está em execução local."

        if not self.has_capacity(execution_mode):
            return False, f"Capacidade máxima de execução {execution_mode} atingida."

        return True, "Worker disponível para executar a task."

    def start_task(self, task_data: dict) -> bool:
        task_id = int(task_data["task_id"])
        execution_mode = get_execution_mode(task_data)

        can_start, reason = self.can_start_task(task_data)
        if not can_start:
            self.logger.info(
                "Task não iniciada localmente | task_id=%s execution_mode=%s motivo=%s",
                task_id,
                execution_mode,
                reason,
            )
            return False

        thread = threading.Thread(
            target=self._run_task,
            args=(task_data,),
            daemon=False,
            name=f"task-{task_id}",
        )

        with self._lock:
            self._running[task_id] = RunningTask(
                task_id=task_id,
                thread=thread,
                execution_mode=execution_mode,
            )

        thread.start()
        return True

    def _run_task(self, task_data: dict) -> None:
        task_id = int(task_data["task_id"])

        try:
            execute_task(
                client=self.client,
                runner=self.runner,
                task_data=task_data,
                logger=self.logger,
                events=self.events,
            )
        except Exception:
            # execute_task já trata e reporta; isto só impede a thread de sumir calada.
            self.logger.exception("Erro inesperado na thread da task | task_id=%s", task_id)
        finally:
            with self._lock:
                self._running.pop(task_id, None)

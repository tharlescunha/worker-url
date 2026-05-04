from __future__ import annotations

import threading
from dataclasses import dataclass

from app.core.config_models import AuthData, RunnerData
from app.runtime.task_executor import execute_task, get_execution_mode


@dataclass
class RunningTask:
    task_id: int
    thread: threading.Thread
    execution_mode: str


class TaskExecutionManager:
    """
    Gerenciador simples de execução local.

    Agora existe apenas um worker:
    - roda no terminal do usuário
    - executa foreground e background no mesmo processo
    - respeita max_concurrency
    """

    def __init__(
        self,
        auth: AuthData,
        access_token: str,
        runner: RunnerData,
        logger,
    ) -> None:
        self.auth = auth
        self.access_token = access_token
        self.runner = runner
        self.logger = logger

        self._lock = threading.Lock()
        self._running: dict[int, RunningTask] = {}

    def cleanup_finished(self) -> None:
        with self._lock:
            finished_ids = [
                task_id
                for task_id, item in self._running.items()
                if not item.thread.is_alive()
            ]

            for task_id in finished_ids:
                self._running.pop(task_id, None)

    def active_count(self) -> int:
        self.cleanup_finished()

        with self._lock:
            return len(self._running)

    def has_capacity(self, max_concurrency: int) -> bool:
        return self.active_count() < max(1, int(max_concurrency or 1))

    def can_start_task(self, task_data: dict) -> tuple[bool, str]:
        task_id = int(task_data["task_id"])

        with self._lock:
            if task_id in self._running:
                return False, "Task já está em execução local."

        if not self.has_capacity(self.runner.config.max_concurrency):
            return False, "Capacidade máxima de execução atingida."

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
            daemon=True,
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
                auth=self.auth,
                access_token=self.access_token,
                runner=self.runner,
                task_data=task_data,
                logger=self.logger,
            )
        finally:
            with self._lock:
                self._running.pop(task_id, None)
                
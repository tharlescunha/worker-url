from __future__ import annotations

import threading
from dataclasses import dataclass

from app.core.config_models import AuthData, RunnerData
from app.core.constants import (
    EXECUTION_MODE_BACKGROUND,
    EXECUTION_MODE_FOREGROUND,
)
from app.runtime.task_executor import execute_task, get_execution_mode


@dataclass
class RunningTask:
    task_id: int
    thread: threading.Thread
    execution_mode: str


class TaskExecutionManager:
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

    def active_background_count(self) -> int:
        self.cleanup_finished()
        with self._lock:
            return sum(
                1
                for item in self._running.values()
                if item.execution_mode == EXECUTION_MODE_BACKGROUND
            )

    def active_foreground_count(self) -> int:
        self.cleanup_finished()
        with self._lock:
            return sum(
                1
                for item in self._running.values()
                if item.execution_mode == EXECUTION_MODE_FOREGROUND
            )

    def has_capacity(self, max_concurrency: int) -> bool:
        return self.active_count() < max(1, max_concurrency)

    def has_foreground_capacity(self) -> bool:
        return self.active_foreground_count() < 1

    def has_background_capacity(self, max_concurrency: int) -> bool:
        return self.active_count() < max(1, max_concurrency)

    def can_start_task(self, task_data: dict) -> tuple[bool, str]:
        execution_mode = get_execution_mode(task_data)

        if execution_mode == EXECUTION_MODE_FOREGROUND:
            if not self.has_foreground_capacity():
                return False, "Já existe uma task foreground em execução nesta máquina."
            return True, "Capacidade foreground disponível."

        if not self.has_background_capacity(self.runner.config.max_concurrency):
            return False, "Capacidade máxima de execução atingida para tasks background."

        return True, "Capacidade background disponível."

    def start_task(self, task_data: dict) -> bool:
        task_id = int(task_data["task_id"])
        execution_mode = get_execution_mode(task_data)

        with self._lock:
            if task_id in self._running:
                return False

            if execution_mode == EXECUTION_MODE_FOREGROUND:
                foreground_running = any(
                    item.execution_mode == EXECUTION_MODE_FOREGROUND
                    for item in self._running.values()
                )
                if foreground_running:
                    return False

            if execution_mode == EXECUTION_MODE_BACKGROUND:
                total_running = len(self._running)
                if total_running >= max(1, self.runner.config.max_concurrency):
                    return False

            thread = threading.Thread(
                target=self._run_task,
                args=(task_data,),
                daemon=True,
                name=f"task-{task_id}",
            )

            self._running[task_id] = RunningTask(
                task_id=task_id,
                thread=thread,
                execution_mode=execution_mode,
            )
            thread.start()
            return True

    def _run_task(self, task_data: dict) -> None:
        task_id = task_data.get("task_id")
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
                self._running.pop(int(task_id), None)
                
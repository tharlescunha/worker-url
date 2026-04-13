from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.constants import (
    EXECUTION_MODE_FOREGROUND,
    INTERACTIVE_AGENT_FOREGROUND_CONCURRENCY,
    INTERACTIVE_AGENT_QUEUE_DIR,
    INTERACTIVE_AGENT_RESULT_POLL_INTERVAL_SECONDS,
    INTERACTIVE_AGENT_RESULTS_DIR,
    INTERACTIVE_AGENT_STALE_RESULT_TTL_HOURS,
)
from app.core.json_store import load_json
from app.runtime.foreground_session import is_interactive_agent_active
from app.runtime.task_client import TaskApiClient


@dataclass
class ForegroundExecutionResult:
    success: bool
    status: str
    final_message: str
    execution_request_id: str
    result_payload: dict | None = None
    stdout_text: str | None = None
    stderr_text: str | None = None
    exit_code: int | None = None


class ForegroundExecutionError(Exception):
    pass


class InteractiveAgentUnavailableError(ForegroundExecutionError):
    pass


class ForegroundExecutionTimeoutError(ForegroundExecutionError):
    pass


class ForegroundExecutor:
    def __init__(
        self,
        *,
        task_api: TaskApiClient,
        runner,
        logger,
    ) -> None:
        self.task_api = task_api
        self.runner = runner
        self.logger = logger

        INTERACTIVE_AGENT_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        INTERACTIVE_AGENT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def can_accept_foreground_task(self) -> tuple[bool, str]:
        active, state = is_interactive_agent_active()
        if not active:
            return False, state.message or "Agente interativo não está ativo."

        running_requests = self._count_pending_foreground_requests()
        if running_requests >= INTERACTIVE_AGENT_FOREGROUND_CONCURRENCY:
            return False, "Executor foreground já está ocupado com outra task."

        return True, "Agente interativo ativo e disponível."

    def execute(
        self,
        *,
        task_data: dict,
        prepared_task_data: dict,
        python_exe: Path,
        entrypoint: Path,
        bot_local_path: Path,
        payload_file: Path,
        timeout_seconds: int,
    ) -> ForegroundExecutionResult:
        can_accept, message = self.can_accept_foreground_task()
        if not can_accept:
            raise InteractiveAgentUnavailableError(message)

        execution_request_id = str(uuid.uuid4())
        request_file = self._build_request_file_path(execution_request_id)
        result_file = self._build_result_file_path(execution_request_id)

        request_payload = self._build_request_payload(
            execution_request_id=execution_request_id,
            task_data=task_data,
            prepared_task_data=prepared_task_data,
            python_exe=python_exe,
            entrypoint=entrypoint,
            bot_local_path=bot_local_path,
            payload_file=payload_file,
            timeout_seconds=timeout_seconds,
        )

        self._write_request_file(request_file, request_payload)
        self.logger.info(
            "Task foreground enviada para o agente interativo | task_id=%s request_id=%s",
            task_data.get("task_id"),
            execution_request_id,
        )

        try:
            result_payload = self._wait_for_result(
                execution_request_id=execution_request_id,
                result_file=result_file,
                timeout_seconds=timeout_seconds,
            )
        finally:
            self._safe_delete_file(request_file)

        result = self._parse_result_payload(
            execution_request_id=execution_request_id,
            result_payload=result_payload,
        )

        self._safe_delete_file(result_file)
        return result

    def cleanup_stale_results(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=INTERACTIVE_AGENT_STALE_RESULT_TTL_HOURS)

        for file_path in INTERACTIVE_AGENT_RESULTS_DIR.glob("*.json"):
            try:
                modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC)
                if modified_at < cutoff:
                    file_path.unlink(missing_ok=True)
            except Exception:
                continue

    def _wait_for_result(
        self,
        *,
        execution_request_id: str,
        result_file: Path,
        timeout_seconds: int,
    ) -> dict:
        deadline = time.time() + max(5, int(timeout_seconds))

        while time.time() <= deadline:
            if result_file.exists():
                try:
                    return load_json(result_file)
                except Exception as exc:
                    raise ForegroundExecutionError(
                        f"Arquivo de resultado foreground inválido para request_id={execution_request_id}: {exc}"
                    ) from exc

            time.sleep(INTERACTIVE_AGENT_RESULT_POLL_INTERVAL_SECONDS)

        raise ForegroundExecutionTimeoutError(
            f"Timeout aguardando resultado do agente interativo | request_id={execution_request_id}"
        )

    def _build_request_payload(
        self,
        *,
        execution_request_id: str,
        task_data: dict,
        prepared_task_data: dict,
        python_exe: Path,
        entrypoint: Path,
        bot_local_path: Path,
        payload_file: Path,
        timeout_seconds: int,
    ) -> dict:
        return {
            "execution_request_id": execution_request_id,
            "created_at": datetime.now(UTC).isoformat(),
            "runner_id": self.runner.id,
            "runner_uuid": self.runner.uuid,
            "task_id": task_data.get("task_id"),
            "automation_id": task_data.get("automation_id"),
            "bot_id": task_data.get("bot_id"),
            "bot_version_id": task_data.get("bot_version_id"),
            "execution_mode": EXECUTION_MODE_FOREGROUND,
            "timeout_seconds": timeout_seconds,
            "python_executable": str(python_exe),
            "entrypoint": str(entrypoint),
            "working_directory": str(bot_local_path),
            "payload_file": str(payload_file),
            "task_payload": prepared_task_data,
            "environment": {
                "ORKAFLOW_TASK_FILE": str(payload_file),
                "ORKAFLOW_TASK_ID": str(task_data.get("task_id") or ""),
                "ORKAFLOW_AUTOMATION_ID": str(task_data.get("automation_id") or ""),
                "ORKAFLOW_RUNNER_ID": str(self.runner.id),
                "ORKAFLOW_RUNNER_UUID": self.runner.uuid,
                "ORKAFLOW_EXECUTION_MODE": EXECUTION_MODE_FOREGROUND,
            },
        }

    def _parse_result_payload(
        self,
        *,
        execution_request_id: str,
        result_payload: dict,
    ) -> ForegroundExecutionResult:
        if not isinstance(result_payload, dict):
            raise ForegroundExecutionError(
                f"Resultado foreground inválido para request_id={execution_request_id}"
            )

        success = bool(result_payload.get("success", False))
        status = str(result_payload.get("status") or "error")
        final_message = str(result_payload.get("final_message") or "Execução foreground sem mensagem final.")
        stdout_text = result_payload.get("stdout_text")
        stderr_text = result_payload.get("stderr_text")
        exit_code = result_payload.get("exit_code")

        try:
            exit_code = int(exit_code) if exit_code is not None else None
        except Exception:
            exit_code = None

        return ForegroundExecutionResult(
            success=success,
            status=status,
            final_message=final_message,
            execution_request_id=execution_request_id,
            result_payload=result_payload,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            exit_code=exit_code,
        )

    def _count_pending_foreground_requests(self) -> int:
        total = 0
        for file_path in INTERACTIVE_AGENT_QUEUE_DIR.glob("*.json"):
            if file_path.is_file():
                total += 1
        return total

    def _build_request_file_path(self, execution_request_id: str) -> Path:
        return INTERACTIVE_AGENT_QUEUE_DIR / f"{execution_request_id}.json"

    def _build_result_file_path(self, execution_request_id: str) -> Path:
        return INTERACTIVE_AGENT_RESULTS_DIR / f"{execution_request_id}.json"

    def _write_request_file(self, request_file: Path, payload: dict) -> None:
        temp_file = request_file.with_suffix(".tmp")
        temp_file.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp_file, request_file)

    def _safe_delete_file(self, file_path: Path) -> None:
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception as exc:
            self.logger.warning(
                "Não foi possível remover arquivo temporário do foreground | file=%s erro=%s",
                file_path,
                exc,
            )
            
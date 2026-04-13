from __future__ import annotations

from dataclasses import dataclass

from app.core.constants import RUNTIME_EVENT_PATH
from app.core.http_client import HttpClient


NEXT_TASK_PATH = "/api/v1/worker/tasks/next"
CLAIM_TASK_PATH = "/api/v1/worker/tasks/{task_id}/claim"
STATUS_TASK_PATH = "/api/v1/worker/tasks/{task_id}/status"
FINISH_TASK_PATH = "/api/v1/worker/tasks/{task_id}/finish"
LOG_TASK_PATH = "/api/v1/worker/tasks/{task_id}/logs"
ERROR_TASK_PATH = "/api/v1/worker/tasks/{task_id}/errors"
TELEMETRY_TASK_PATH = "/api/v1/worker/tasks/{task_id}/telemetry"
HEARTBEAT_PATH = "/api/v1/worker/heartbeat/"
RESOLVE_CREDENTIAL_PATH = "/api/v1/worker/credentials/{credential_id}/resolve"

LIST_ACTIVE_TASKS_PATH = "/api/v1/worker/tasks/active"
RELEASE_LOCK_PATH = "/api/v1/worker/tasks/{task_id}/release-lock"
RELEASE_STARTUP_LOCKS_PATH = "/api/v1/worker/tasks/release-startup-locks"


@dataclass
class TaskApiClient:
    client: HttpClient
    runner_uuid: str
    runner_token: str

    def _auth_payload(self) -> dict:
        return {
            "uuid": self.runner_uuid,
            "token": self.runner_token,
        }

    def next_task(self) -> dict:
        return self.client.post(NEXT_TASK_PATH, self._auth_payload())

    def claim_task(self, task_id: int) -> dict:
        return self.client.post(
            CLAIM_TASK_PATH.format(task_id=task_id),
            self._auth_payload(),
        )

    def list_active_tasks(self) -> dict:
        return self.client.post(
            LIST_ACTIVE_TASKS_PATH,
            self._auth_payload(),
        )

    def release_task_lock(self, task_id: int) -> dict:
        return self.client.post(
            RELEASE_LOCK_PATH.format(task_id=task_id),
            self._auth_payload(),
        )

    def release_startup_locks(self) -> dict:
        return self.client.post(
            RELEASE_STARTUP_LOCKS_PATH,
            self._auth_payload(),
        )

    def update_status(
        self,
        task_id: int,
        status: str,
        items_processed: int | None = None,
        items_failed: int | None = None,
        final_message: str | None = None,
    ) -> dict:
        payload = self._auth_payload()
        payload["status"] = status

        if items_processed is not None:
            payload["items_processed"] = items_processed
        if items_failed is not None:
            payload["items_failed"] = items_failed
        if final_message is not None:
            payload["final_message"] = final_message

        return self.client.patch(
            STATUS_TASK_PATH.format(task_id=task_id),
            payload,
        )

    def finish_task(
        self,
        task_id: int,
        status: str,
        final_message: str | None = None,
        items_processed: int = 0,
        items_failed: int = 0,
    ) -> dict:
        payload = self._auth_payload()
        payload["status"] = status
        payload["final_message"] = final_message
        payload["items_processed"] = items_processed
        payload["items_failed"] = items_failed

        return self.client.post(
            FINISH_TASK_PATH.format(task_id=task_id),
            payload,
        )

    def send_log(
        self,
        task_id: int,
        level: str,
        message: str,
        reference: str | None = None,
        error_type: str | None = None,
        sequence_number: int | None = None,
        event_code: str | None = None,
    ) -> dict:
        payload = self._auth_payload()
        payload["level"] = level
        payload["message"] = message
        payload["source"] = "worker"

        if reference is not None:
            payload["reference"] = reference
        if error_type is not None:
            payload["error_type"] = error_type
        if sequence_number is not None:
            payload["sequence_number"] = sequence_number
        if event_code is not None:
            payload["event_code"] = event_code

        return self.client.post(
            LOG_TASK_PATH.format(task_id=task_id),
            payload,
        )

    def send_error(
        self,
        task_id: int,
        error_type: str,
        message: str,
        stacktrace: str | None = None,
        code: str | None = None,
        is_retryable: bool = False,
    ) -> dict:
        payload = self._auth_payload()
        payload["error_type"] = error_type
        payload["message"] = message
        payload["stacktrace"] = stacktrace
        payload["source"] = "worker"
        payload["is_retryable"] = is_retryable

        if code is not None:
            payload["code"] = code

        return self.client.post(
            ERROR_TASK_PATH.format(task_id=task_id),
            payload,
        )

    def send_telemetry(
        self,
        task_id: int,
        *,
        captured_at: str,
        execution_started_at: str | None = None,
        execution_finished_at: str | None = None,
        duration_seconds: float | None = None,
        cpu_percent_avg: float | None = None,
        cpu_percent_peak: float | None = None,
        memory_used_mb_avg: float | None = None,
        memory_used_mb_peak: float | None = None,
        process_memory_mb_peak: float | None = None,
        disk_read_mb: float | None = None,
        disk_write_mb: float | None = None,
        net_sent_mb: float | None = None,
        net_recv_mb: float | None = None,
        exit_code: int | None = None,
        telemetry_status: str | None = None,
        message: str | None = None,
        payload_json: str | None = None,
    ) -> dict:
        payload = self._auth_payload()
        payload["captured_at"] = captured_at
        payload["execution_started_at"] = execution_started_at
        payload["execution_finished_at"] = execution_finished_at
        payload["duration_seconds"] = duration_seconds
        payload["cpu_percent_avg"] = cpu_percent_avg
        payload["cpu_percent_peak"] = cpu_percent_peak
        payload["memory_used_mb_avg"] = memory_used_mb_avg
        payload["memory_used_mb_peak"] = memory_used_mb_peak
        payload["process_memory_mb_peak"] = process_memory_mb_peak
        payload["disk_read_mb"] = disk_read_mb
        payload["disk_write_mb"] = disk_write_mb
        payload["net_sent_mb"] = net_sent_mb
        payload["net_recv_mb"] = net_recv_mb
        payload["exit_code"] = exit_code
        payload["telemetry_status"] = telemetry_status
        payload["message"] = message
        payload["payload_json"] = payload_json

        return self.client.post(
            TELEMETRY_TASK_PATH.format(task_id=task_id),
            payload,
        )

    def heartbeat(self, ip: str | None = None, running_tasks: int = 0) -> dict:
        payload = self._auth_payload()
        payload["ip"] = ip
        payload["running_tasks"] = running_tasks
        return self.client.post(HEARTBEAT_PATH, payload)

    def resolve_credential(
        self,
        credential_id: int,
        keys: list[str] | None = None,
    ) -> dict:
        payload = self._auth_payload()
        payload["keys"] = keys or []

        return self.client.post(
            RESOLVE_CREDENTIAL_PATH.format(credential_id=credential_id),
            payload,
        )

    def send_runtime_event(
        self,
        *,
        event_type: str,
        task_id: int | None = None,
        automation_id: int | None = None,
        bot_id: int | str | None = None,
        execution_mode: str | None = None,
        reason: str | None = None,
        message: str | None = None,
        extra_payload: dict | None = None,
    ) -> dict:
        payload = self._auth_payload()
        payload["event_type"] = event_type
        payload["task_id"] = task_id
        payload["automation_id"] = automation_id
        payload["bot_id"] = bot_id
        payload["execution_mode"] = execution_mode
        payload["reason"] = reason
        payload["message"] = message

        if extra_payload:
            payload.update(extra_payload)

        return self.client.post(RUNTIME_EVENT_PATH, payload)

    def try_send_runtime_event(
        self,
        *,
        event_type: str,
        task_id: int | None = None,
        automation_id: int | None = None,
        bot_id: int | str | None = None,
        execution_mode: str | None = None,
        reason: str | None = None,
        message: str | None = None,
        extra_payload: dict | None = None,
        logger=None,
    ) -> bool:
        try:
            self.send_runtime_event(
                event_type=event_type,
                task_id=task_id,
                automation_id=automation_id,
                bot_id=bot_id,
                execution_mode=execution_mode,
                reason=reason,
                message=message,
                extra_payload=extra_payload,
            )
            return True
        except Exception as exc:
            if logger is not None:
                logger.warning(
                    "Falha ao enviar runtime_event | event_type=%s task_id=%s erro=%s",
                    event_type,
                    task_id,
                    exc,
                )
            return False
        
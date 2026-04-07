from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import psutil

from app.core.config_models import AuthData, BotRegistryItem, BotsRegistry, RunnerData
from app.core.constants import BOTS_REGISTRY_FILE, TMP_DIR
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.runtime.task_client import TaskApiClient
from app.sync.bot_installer import install_or_update_bot


LOG_LEVEL_INFO = "info"
LOG_LEVEL_ERROR = "error"

TASK_STATUS_RUNNING = "running"
TASK_STATUS_FINISHED = "finished"
TASK_STATUS_ERROR = "error"
TASK_STATUS_TIMEOUT = "timeout"


def execute_task(
    auth: AuthData,
    access_token: str,
    runner: RunnerData,
    task_data: dict,
    logger,
) -> None:
    client = HttpClient(base_url=auth.base_url)
    client.set_token(access_token)

    api = TaskApiClient(
        client=client,
        runner_uuid=runner.uuid,
        runner_token=runner.runner_token,
    )

    task_id = int(task_data["task_id"])
    process: subprocess.Popen | None = None
    task_file: Path | None = None
    telemetry_collector: ProcessTelemetryCollector | None = None
    execution_started_at = datetime.now(UTC)

    try:
        bot = _resolve_bot_for_task(task_data)
        bot = _ensure_bot_ready(bot)

        python_exe = Path(bot.venv_path) / "Scripts" / "python.exe"
        entrypoint = Path(bot.local_path) / (bot.entrypoint or "main.py")

        if not python_exe.exists():
            raise RuntimeError(f"Python da venv não encontrado: {python_exe}")

        if not entrypoint.exists():
            raise RuntimeError(f"Entrypoint do bot não encontrado: {entrypoint}")

        prepared_task_data = _prepare_task_data_for_execution(
            task_data=task_data,
            api=api,
            logger=logger,
        )

        task_file = _write_task_payload_file(prepared_task_data)

        api.update_status(
            task_id=task_id,
            status=TASK_STATUS_RUNNING,
            final_message="Task iniciada pelo worker.",
        )

        logger.info("Task iniciada | task_id=%s", task_id)

        timeout_seconds = task_data.get("timeout_seconds") or bot.timeout_default or 300

        env = os.environ.copy()
        env["ORKAFLOW_TASK_FILE"] = str(task_file)
        env["ORKAFLOW_TASK_ID"] = str(task_id)
        env["ORKAFLOW_AUTOMATION_ID"] = str(task_data.get("automation_id") or "")
        env["ORKAFLOW_RUNNER_ID"] = str(runner.id)
        env["ORKAFLOW_RUNNER_UUID"] = runner.uuid

        command = [
            str(python_exe),
            str(entrypoint),
            str(task_file),
        ]

        net_before = psutil.net_io_counters()
        process = subprocess.Popen(
            command,
            cwd=str(Path(bot.local_path)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        telemetry_collector = ProcessTelemetryCollector(process.pid)
        telemetry_collector.start()

        stdout_text, stderr_text = process.communicate(timeout=timeout_seconds)

        telemetry = telemetry_collector.stop(
            execution_started_at=execution_started_at,
            execution_finished_at=datetime.now(UTC),
            exit_code=process.returncode,
            net_before=net_before,
            net_after=psutil.net_io_counters(),
            telemetry_status="finished" if process.returncode == 0 else "error",
            message="Task finalizada com sucesso." if process.returncode == 0 else f"Bot finalizou com código de saída {process.returncode}.",
        )

        _log_process_output_locally(logger, task_id, stdout_text, stderr_text)

        try:
            _send_output_logs(api, task_id, stdout_text, stderr_text)
        except Exception:
            logger.exception("Falha ao registrar stdout/stderr da task | task_id=%s", task_id)

        try:
            _send_telemetry(api, task_id, telemetry, logger)
        except Exception:
            logger.exception("Falha ao enviar telemetria da task | task_id=%s", task_id)

        if process.returncode == 0:
            api.finish_task(
                task_id=task_id,
                status=TASK_STATUS_FINISHED,
                final_message="Task finalizada com sucesso.",
                items_processed=0,
                items_failed=0,
            )
            logger.info("Task finalizada com sucesso | task_id=%s", task_id)
            return

        error_message = f"Bot finalizou com código de saída {process.returncode}."
        trace_text = _build_stacktrace(stderr_text, stdout_text)
        final_message = error_message

        try:
            api.send_error(
                task_id=task_id,
                error_type="bot_exit_code",
                message=error_message,
                stacktrace=trace_text,
                code=str(process.returncode),
                is_retryable=False,
            )
        except Exception as exc:
            logger.exception("Falha ao registrar task_error | task_id=%s", task_id)
            final_message = _compose_error_final_message(
                error_message,
                trace_text,
                f"Falha ao registrar task_error: {exc}",
            )
        else:
            final_message = _compose_error_final_message(error_message, trace_text)

        api.finish_task(
            task_id=task_id,
            status=TASK_STATUS_ERROR,
            final_message=final_message,
            items_processed=0,
            items_failed=1,
        )
        logger.error("Task finalizada com erro | task_id=%s returncode=%s", task_id, process.returncode)

    except subprocess.TimeoutExpired:
        if process and process.pid:
            _kill_process_tree(process.pid)

        timeout_message = "Task excedeu o timeout e foi encerrada pelo worker."

        telemetry = None
        if telemetry_collector is not None:
            try:
                telemetry = telemetry_collector.stop(
                    execution_started_at=execution_started_at,
                    execution_finished_at=datetime.now(UTC),
                    exit_code=None,
                    net_before=None,
                    net_after=psutil.net_io_counters(),
                    telemetry_status="timeout",
                    message=timeout_message,
                )
            except Exception:
                logger.exception("Falha ao coletar telemetria de timeout | task_id=%s", task_id)

        if telemetry is not None:
            try:
                _send_telemetry(api, task_id, telemetry, logger)
            except Exception:
                logger.exception("Falha ao enviar telemetria de timeout | task_id=%s", task_id)

        try:
            api.send_log(
                task_id=task_id,
                level=LOG_LEVEL_ERROR,
                message=timeout_message,
                error_type="timeout",
            )
        except Exception:
            logger.exception("Falha ao registrar log de timeout | task_id=%s", task_id)

        final_message = timeout_message

        try:
            api.send_error(
                task_id=task_id,
                error_type="timeout",
                message=timeout_message,
                stacktrace="TimeoutExpired",
                code="TIMEOUT",
                is_retryable=False,
            )
        except Exception as exc:
            logger.exception("Falha ao registrar erro de timeout | task_id=%s", task_id)
            final_message = _compose_error_final_message(
                timeout_message,
                "TimeoutExpired",
                f"Falha ao registrar task_error: {exc}",
            )
        else:
            final_message = _compose_error_final_message(timeout_message, "TimeoutExpired")

        try:
            api.finish_task(
                task_id=task_id,
                status=TASK_STATUS_TIMEOUT,
                final_message=final_message,
                items_processed=0,
                items_failed=1,
            )
        except Exception:
            logger.exception("Falha ao finalizar task com timeout | task_id=%s", task_id)

        logger.error("Task finalizada por timeout | task_id=%s", task_id)

    except Exception as exc:
        message = f"Erro ao executar task: {exc}"
        stacktrace = traceback.format_exc()

        telemetry = None
        if telemetry_collector is not None:
            try:
                telemetry = telemetry_collector.stop(
                    execution_started_at=execution_started_at,
                    execution_finished_at=datetime.now(UTC),
                    exit_code=process.returncode if process else None,
                    net_before=None,
                    net_after=psutil.net_io_counters(),
                    telemetry_status="execution_error",
                    message=message,
                )
            except Exception:
                logger.exception("Falha ao coletar telemetria de erro | task_id=%s", task_id)

        if telemetry is not None:
            try:
                _send_telemetry(api, task_id, telemetry, logger)
            except Exception:
                logger.exception("Falha ao enviar telemetria de erro | task_id=%s", task_id)

        logger.exception("Erro ao executar task | task_id=%s", task_id)

        try:
            api.send_log(
                task_id=task_id,
                level=LOG_LEVEL_ERROR,
                message=message,
                error_type="execution_error",
            )
        except Exception:
            logger.exception("Falha ao registrar log de erro | task_id=%s", task_id)

        final_message = message

        try:
            api.send_error(
                task_id=task_id,
                error_type="execution_error",
                message=message,
                stacktrace=stacktrace,
                code="EXECUTION_ERROR",
                is_retryable=False,
            )
        except Exception as api_exc:
            logger.exception("Falha ao registrar erro da task | task_id=%s", task_id)
            final_message = _compose_error_final_message(
                message,
                stacktrace,
                f"Falha ao registrar task_error: {api_exc}",
            )
        else:
            final_message = _compose_error_final_message(message, stacktrace)

        try:
            api.finish_task(
                task_id=task_id,
                status=TASK_STATUS_ERROR,
                final_message=final_message,
                items_processed=0,
                items_failed=1,
            )
        except Exception:
            logger.exception("Falha ao finalizar task com erro | task_id=%s", task_id)

        logger.error("Task finalizada com erro | task_id=%s", task_id)

    finally:
        if telemetry_collector is not None:
            telemetry_collector.ensure_stopped()

        if task_file and task_file.exists():
            try:
                task_file.unlink()
            except Exception:
                logger.warning("Não foi possível remover o arquivo temporário da task: %s", task_file)


class ProcessTelemetryCollector:
    def __init__(self, pid: int, interval_seconds: float = 1.0) -> None:
        self.pid = pid
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.cpu_samples: list[float] = []
        self.memory_machine_samples_mb: list[float] = []
        self.process_memory_samples_mb: list[float] = []

        self.disk_read_bytes_last: int | None = None
        self.disk_write_bytes_last: int | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"telemetry-{self.pid}",
        )
        self._thread.start()

    def stop(
        self,
        *,
        execution_started_at: datetime,
        execution_finished_at: datetime,
        exit_code: int | None,
        net_before,
        net_after,
        telemetry_status: str,
        message: str | None,
    ) -> dict:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        duration_seconds = round((execution_finished_at - execution_started_at).total_seconds(), 3)

        disk_read_mb = None
        disk_write_mb = None
        if self.disk_read_bytes_last is not None:
            disk_read_mb = round(self.disk_read_bytes_last / (1024 * 1024), 3)
        if self.disk_write_bytes_last is not None:
            disk_write_mb = round(self.disk_write_bytes_last / (1024 * 1024), 3)

        net_sent_mb = None
        net_recv_mb = None
        if net_before is not None and net_after is not None:
            net_sent_mb = round(max(0, net_after.bytes_sent - net_before.bytes_sent) / (1024 * 1024), 3)
            net_recv_mb = round(max(0, net_after.bytes_recv - net_before.bytes_recv) / (1024 * 1024), 3)

        cpu_percent_avg = _avg_or_none(self.cpu_samples)
        cpu_percent_peak = _max_or_none(self.cpu_samples)
        memory_used_mb_avg = _avg_or_none(self.memory_machine_samples_mb)
        memory_used_mb_peak = _max_or_none(self.memory_machine_samples_mb)
        process_memory_mb_peak = _max_or_none(self.process_memory_samples_mb)

        payload = {
            "cpu_samples": self.cpu_samples,
            "memory_machine_samples_mb": self.memory_machine_samples_mb,
            "process_memory_samples_mb": self.process_memory_samples_mb,
            "disk_read_mb": disk_read_mb,
            "disk_write_mb": disk_write_mb,
            "net_sent_mb": net_sent_mb,
            "net_recv_mb": net_recv_mb,
        }

        return {
            "captured_at": execution_finished_at.isoformat(),
            "execution_started_at": execution_started_at.isoformat(),
            "execution_finished_at": execution_finished_at.isoformat(),
            "duration_seconds": duration_seconds,
            "cpu_percent_avg": cpu_percent_avg,
            "cpu_percent_peak": cpu_percent_peak,
            "memory_used_mb_avg": memory_used_mb_avg,
            "memory_used_mb_peak": memory_used_mb_peak,
            "process_memory_mb_peak": process_memory_mb_peak,
            "disk_read_mb": disk_read_mb,
            "disk_write_mb": disk_write_mb,
            "net_sent_mb": net_sent_mb,
            "net_recv_mb": net_recv_mb,
            "exit_code": exit_code,
            "telemetry_status": telemetry_status,
            "message": message,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }

    def ensure_stopped(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self) -> None:
        try:
            proc = psutil.Process(self.pid)
            proc.cpu_percent(interval=None)

            while not self._stop_event.is_set():
                if not proc.is_running():
                    break

                try:
                    cpu_percent = psutil.cpu_percent(interval=None)
                    self.cpu_samples.append(round(cpu_percent, 3))
                except Exception:
                    pass

                try:
                    memory_machine = psutil.virtual_memory()
                    self.memory_machine_samples_mb.append(round(memory_machine.used / (1024 * 1024), 3))
                except Exception:
                    pass

                try:
                    proc_memory = proc.memory_info().rss
                    self.process_memory_samples_mb.append(round(proc_memory / (1024 * 1024), 3))
                except Exception:
                    pass

                try:
                    io = proc.io_counters()
                    self.disk_read_bytes_last = getattr(io, "read_bytes", None)
                    self.disk_write_bytes_last = getattr(io, "write_bytes", None)
                except Exception:
                    pass

                time.sleep(self.interval_seconds)
        except Exception:
            return


def _send_telemetry(api: TaskApiClient, task_id: int, telemetry: dict, logger) -> None:
    api.send_telemetry(
        task_id=task_id,
        captured_at=telemetry.get("captured_at"),
        execution_started_at=telemetry.get("execution_started_at"),
        execution_finished_at=telemetry.get("execution_finished_at"),
        duration_seconds=telemetry.get("duration_seconds"),
        cpu_percent_avg=telemetry.get("cpu_percent_avg"),
        cpu_percent_peak=telemetry.get("cpu_percent_peak"),
        memory_used_mb_avg=telemetry.get("memory_used_mb_avg"),
        memory_used_mb_peak=telemetry.get("memory_used_mb_peak"),
        process_memory_mb_peak=telemetry.get("process_memory_mb_peak"),
        disk_read_mb=telemetry.get("disk_read_mb"),
        disk_write_mb=telemetry.get("disk_write_mb"),
        net_sent_mb=telemetry.get("net_sent_mb"),
        net_recv_mb=telemetry.get("net_recv_mb"),
        exit_code=telemetry.get("exit_code"),
        telemetry_status=telemetry.get("telemetry_status"),
        message=telemetry.get("message"),
        payload_json=telemetry.get("payload_json"),
    )
    logger.info("Telemetria enviada com sucesso | task_id=%s", task_id)


def _avg_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _max_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(max(values), 3)


def _resolve_bot_for_task(task_data: dict) -> BotRegistryItem:
    registry = load_model(BOTS_REGISTRY_FILE, BotsRegistry)
    if not registry:
        raise RuntimeError("bots_registry.json não encontrado.")

    task_bot_id = task_data.get("bot_id")
    task_bot_version_id = task_data.get("bot_version_id")

    for bot in registry.bots:
        if task_bot_id is not None and str(bot.bot_id) == str(task_bot_id):
            if task_bot_version_id is None or getattr(bot, "bot_version_id", None) == task_bot_version_id:
                return bot

    if task_bot_version_id is not None:
        for bot in registry.bots:
            if getattr(bot, "bot_version_id", None) == task_bot_version_id:
                return bot

    raise RuntimeError(
        f"Bot não encontrado no registry local para task_id={task_data.get('task_id')} "
        f"bot_id={task_bot_id} bot_version_id={task_bot_version_id}"
    )


def _ensure_bot_ready(bot: BotRegistryItem) -> BotRegistryItem:
    registry = load_model(BOTS_REGISTRY_FILE, BotsRegistry)
    if not registry:
        raise RuntimeError("bots_registry.json não encontrado.")

    target: BotRegistryItem | None = None
    for item in registry.bots:
        if item.bot_id == bot.bot_id:
            target = item
            break

    if not target:
        raise RuntimeError(f"Bot {bot.bot_id} não encontrado no registry.")

    if not target.linked:
        raise RuntimeError(f"Bot {target.bot_id} está desvinculado do runner.")

    needs_prepare = False

    if not target.local_path or not target.venv_path:
        needs_prepare = True
    elif target.installed_version != target.expected_version:
        needs_prepare = True
    elif target.expected_commit and target.installed_commit != target.expected_commit:
        needs_prepare = True
    elif target.last_install_status in ("error", "not_installed", "outdated"):
        needs_prepare = True

    if needs_prepare:
        result = install_or_update_bot(target)
        target.local_path = result.local_path
        target.venv_path = result.venv_path
        target.installed_version = target.expected_version
        target.installed_commit = result.installed_commit
        target.requirements_hash = result.requirements_hash
        target.last_install_status = "ok"
        target.last_install_message = result.message
        save_model(BOTS_REGISTRY_FILE, registry)

    return target


def _prepare_task_data_for_execution(
    task_data: dict,
    api: TaskApiClient,
    logger,
) -> dict:
    prepared = dict(task_data)
    original_parameters = task_data.get("parameters") or []

    prepared_parameters: list[dict] = []
    for param in original_parameters:
        prepared_parameters.append(
            _resolve_parameter_for_execution(
                param=param,
                api=api,
                logger=logger,
            )
        )

    prepared["parameters"] = prepared_parameters
    return prepared


def _resolve_parameter_for_execution(
    param: dict,
    api: TaskApiClient,
    logger,
) -> dict:
    if not isinstance(param, dict):
        return param

    if param.get("parameter_name") != "parameters_json":
        return param

    raw_value = param.get("parameter_value")
    if not raw_value or not isinstance(raw_value, str):
        return param

    try:
        parsed_value = json.loads(raw_value)
    except Exception:
        return param

    if not isinstance(parsed_value, dict):
        return param

    dados_acesso = parsed_value.get("dados_acesso")
    if not isinstance(dados_acesso, dict):
        return param

    credential_id = dados_acesso.get("credential_id")
    itens = dados_acesso.get("itens")

    if not credential_id or not isinstance(itens, dict) or not itens:
        return param

    requested_keys = [str(key) for key in itens.keys()]

    try:
        response = api.resolve_credential(
            credential_id=int(credential_id),
            keys=requested_keys,
        )
    except Exception as exc:
        logger.exception(
            "Falha ao resolver dados_acesso da credencial | credential_id=%s erro=%s",
            credential_id,
            exc,
        )
        return param

    resolved_dados_acesso = response.get("dados_acesso")
    if not isinstance(resolved_dados_acesso, dict):
        return param

    final_dados_acesso: dict[str, str | None] = {}
    for key_name, original_value in itens.items():
        final_dados_acesso[key_name] = resolved_dados_acesso.get(key_name, original_value)

    parsed_value["dados_acesso"] = final_dados_acesso

    updated_param = dict(param)
    updated_param["parameter_value"] = json.dumps(
        parsed_value,
        ensure_ascii=False,
    )
    return updated_param


def _write_task_payload_file(task_data: dict) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    task_file = TMP_DIR / f"task_{task_data['task_id']}.json"
    payload = {
        "task_id": task_data.get("task_id"),
        "automation_id": task_data.get("automation_id"),
        "bot_id": task_data.get("bot_id"),
        "bot_version_id": task_data.get("bot_version_id"),
        "priority": task_data.get("priority"),
        "status": task_data.get("status"),
        "correlation_id": task_data.get("correlation_id"),
        "queue_name": task_data.get("queue_name"),
        "requested_start_at": task_data.get("requested_start_at"),
        "timeout_seconds": task_data.get("timeout_seconds"),
        "inactivity_timeout_seconds": task_data.get("inactivity_timeout_seconds"),
        "parameters": task_data.get("parameters", []),
    }

    task_file.write_text(
        json.dumps(payload, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    return task_file


def _send_output_logs(api: TaskApiClient, task_id: int, stdout_text: str, stderr_text: str) -> None:
    sequence = 1

    for line in _normalize_lines(stdout_text):
        api.send_log(
            task_id=task_id,
            level=LOG_LEVEL_INFO,
            message=line,
            sequence_number=sequence,
        )
        sequence += 1

    for line in _normalize_lines(stderr_text):
        api.send_log(
            task_id=task_id,
            level=LOG_LEVEL_ERROR,
            message=line,
            error_type="stderr",
            sequence_number=sequence,
        )
        sequence += 1


def _log_process_output_locally(logger, task_id: int, stdout_text: str | None, stderr_text: str | None) -> None:
    for line in _normalize_lines(stdout_text):
        logger.info("[BOT][task_id=%s] %s", task_id, line)

    for line in _normalize_lines(stderr_text):
        logger.error("[BOT][task_id=%s] %s", task_id, line)


def _normalize_lines(text: str | None) -> list[str]:
    if not text:
        return []

    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.append(_shorten_text(line, 4000))
    return lines


def _build_stacktrace(stderr_text: str | None, stdout_text: str | None) -> str | None:
    if stderr_text and stderr_text.strip():
        return _shorten_text(stderr_text, 12000)
    if stdout_text and stdout_text.strip():
        return _shorten_text(stdout_text, 12000)
    return None


def _compose_error_final_message(message: str, stacktrace: str | None, extra: str | None = None) -> str:
    parts = [message]
    if extra:
        parts.append(extra)
    if stacktrace:
        parts.append("TRACEBACK:")
        parts.append(_shorten_text(stacktrace, 2500))
    return _shorten_text("\n".join(parts), 3500)


def _shorten_text(text: str, max_len: int) -> str:
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _kill_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    
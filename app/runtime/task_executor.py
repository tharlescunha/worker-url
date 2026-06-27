from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
import queue

import psutil

from app.core.config_models import AuthData, BotRegistryItem, BotsRegistry, RunnerData
from app.core.constants import (
    BOTS_REGISTRY_FILE,
    EXECUTION_MODE_BACKGROUND,
    EXECUTION_MODE_FOREGROUND,
    TMP_DIR,
)
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.runtime.screenshot_reporter import ScreenshotReporter
from app.runtime.task_client import TaskApiClient
from app.sync.bot_installer import git_pull_for_auto_update, install_or_update_bot, resolve_bot_install_paths


LOG_LEVEL_INFO = "info"
LOG_LEVEL_WARNING = "warning"
LOG_LEVEL_ERROR = "error"

TASK_STATUS_RUNNING = "running"
TASK_STATUS_FINISHED = "finished"
TASK_STATUS_ERROR = "error"
TASK_STATUS_TIMEOUT = "timeout"
TASK_STATUS_CANCELED = "canceled"
TASK_STATUS_FORCED_STOP = "forced_stop"

ASYNC_LOG_QUEUE_MAX_SIZE = 2000
ASYNC_LOG_SHUTDOWN_TIMEOUT_SECONDS = 5
STOP_STATUS_CHECK_INTERVAL_SECONDS = 5
RESULT_JSON_MAX_BYTES = 1024 * 1024
MAX_CAPTURED_OUTPUT_LINES = 1000

STDERR_ERROR_MARKERS = (
    "traceback",
    "exception",
    "error",
    "erro",
    "failed",
    "falha",
    "fatal",
    "critical",
)


class TaskStopRequested(RuntimeError):
    def __init__(self, remote_status: str):
        super().__init__("Task interrompida por solicitacao remota.")
        self.remote_status = remote_status


def get_execution_mode(task_data: dict) -> str:
    execution_mode = str(
        task_data.get("execution_mode")
        or task_data.get("bot_execution_mode")
        or EXECUTION_MODE_BACKGROUND
    ).strip().lower()

    if execution_mode not in {EXECUTION_MODE_BACKGROUND, EXECUTION_MODE_FOREGROUND}:
        return EXECUTION_MODE_BACKGROUND

    return execution_mode


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
    execution_mode = get_execution_mode(task_data)

    process: subprocess.Popen | None = None
    task_file: Path | None = None
    result_file: Path | None = None
    telemetry_collector: ProcessTelemetryCollector | None = None
    screenshot_reporter: ScreenshotReporter | None = None

    execution_started_at = datetime.now(UTC)

    try:
        bot = _resolve_bot_for_task(task_data)
        bot = _ensure_bot_ready(
            bot,
            api=api,
            task_id=task_id,
            logger=logger,
        )

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

        task_file = _write_task_payload_file(
            task_data=prepared_task_data,
            execution_mode=execution_mode,
        )
        result_file = _build_result_file_path(task_id)

        api.update_status(
            task_id=task_id,
            status=TASK_STATUS_RUNNING,
            final_message="Task iniciada pelo worker.",
        )

        screenshot_reporter = ScreenshotReporter(
            api=api,
            interval_seconds=10,
            logger=logger,
        )
        screenshot_reporter.start()

        timeout_seconds = int(
            task_data.get("timeout_seconds")
            or bot.timeout_default
            or 300
        )

        env = os.environ.copy()
        env["ORKAFLOW_TASK_FILE"] = str(task_file)
        env["ORKAFLOW_RESULT_FILE"] = str(result_file)
        env["ORKAFLOW_TASK_ID"] = str(task_id)
        env["ORKAFLOW_AUTOMATION_ID"] = str(task_data.get("automation_id") or "")
        env["ORKAFLOW_RUNNER_ID"] = str(runner.id)
        env["ORKAFLOW_RUNNER_UUID"] = runner.uuid
        env["ORKAFLOW_EXECUTION_MODE"] = execution_mode
        env["ORKAFLOW_WORKER_ROLE"] = "local"

        command = [
            str(python_exe),
            str(entrypoint),
            str(task_file),
        ]

        logger.info(
            "Executando task | task_id=%s execution_mode=%s command=%s",
            task_id,
            execution_mode,
            command,
        )

        print(f"Executando task {task_id} | modo={execution_mode}")

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
            shell=False,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

        telemetry_collector = ProcessTelemetryCollector(process.pid)
        telemetry_collector.start()

        if process.stdout is None or process.stderr is None:
            raise RuntimeError("stdout/stderr do processo não foram inicializados.")

        stdout_text, stderr_text = _consume_process_output_live(
            process=process,
            timeout_seconds=timeout_seconds,
            logger=logger,
            task_id=task_id,
            api=api,
        )

        telemetry = telemetry_collector.stop(
            execution_started_at=execution_started_at,
            execution_finished_at=datetime.now(UTC),
            exit_code=process.returncode,
            net_before=net_before,
            net_after=psutil.net_io_counters(),
            telemetry_status="finished" if process.returncode == 0 else "error",
            message=(
                "Task finalizada com sucesso."
                if process.returncode == 0
                else f"Bot finalizou com código de saída {process.returncode}."
            ),
            execution_mode=execution_mode,
        )

        try:
            _send_telemetry(api, task_id, telemetry, logger)
        except Exception:
            logger.exception("Falha ao enviar telemetria da task | task_id=%s", task_id)

        if process.returncode == 0:
            result_json = _read_task_result_json(
                result_file=result_file,
                api=api,
                task_id=task_id,
                logger=logger,
            )
            api.finish_task(
                task_id=task_id,
                status=TASK_STATUS_FINISHED,
                final_message="Task finalizada com sucesso.",
                items_processed=0,
                items_failed=0,
                result_json=result_json,
            )

            logger.info("Task finalizada com sucesso | task_id=%s", task_id)
            print(f"Task finalizada com sucesso | task_id={task_id}")
            return

        error_message = f"Bot finalizou com código de saída {process.returncode}."
        trace_text = _build_stacktrace(stderr_text, stdout_text)

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
        print(f"Task finalizada com erro | task_id={task_id} | exit_code={process.returncode}")

    except TaskStopRequested as exc:
        stop_message = "Task interrompida por solicitacao remota."

        telemetry = None
        if telemetry_collector is not None:
            try:
                telemetry = telemetry_collector.stop(
                    execution_started_at=execution_started_at,
                    execution_finished_at=datetime.now(UTC),
                    exit_code=None,
                    net_before=None,
                    net_after=psutil.net_io_counters(),
                    telemetry_status="canceled",
                    message=stop_message,
                    execution_mode=execution_mode,
                )
            except Exception:
                logger.exception("Falha ao coletar telemetria de parada | task_id=%s", task_id)

        if telemetry is not None:
            try:
                _send_telemetry(api, task_id, telemetry, logger)
            except Exception:
                logger.exception("Falha ao enviar telemetria de parada | task_id=%s", task_id)

        try:
            api.send_log(
                task_id=task_id,
                level=LOG_LEVEL_WARNING,
                message=stop_message,
                event_code="remote_stop",
            )
        except Exception:
            logger.exception("Falha ao registrar log de parada | task_id=%s", task_id)

        if exc.remote_status != TASK_STATUS_FORCED_STOP:
            try:
                api.finish_task(
                    task_id=task_id,
                    status=TASK_STATUS_CANCELED,
                    final_message=stop_message,
                    items_processed=0,
                    items_failed=1,
                )
            except Exception:
                logger.exception("Falha ao finalizar task cancelada | task_id=%s", task_id)

        logger.warning("Task interrompida por parada remota | task_id=%s", task_id)
        print(f"Task interrompida por parada remota | task_id={task_id}")

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
                    execution_mode=execution_mode,
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

        try:
            api.send_error(
                task_id=task_id,
                error_type="timeout",
                message=timeout_message,
                stacktrace="TimeoutExpired",
                code="TIMEOUT",
                is_retryable=False,
            )
            final_message = _compose_error_final_message(timeout_message, "TimeoutExpired")
        except Exception as exc:
            logger.exception("Falha ao registrar erro de timeout | task_id=%s", task_id)
            final_message = _compose_error_final_message(
                timeout_message,
                "TimeoutExpired",
                f"Falha ao registrar task_error: {exc}",
            )

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
        print(f"Task finalizada por timeout | task_id={task_id}")

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
                    execution_mode=execution_mode,
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

        try:
            api.send_error(
                task_id=task_id,
                error_type="execution_error",
                message=message,
                stacktrace=stacktrace,
                code="EXECUTION_ERROR",
                is_retryable=False,
            )
            final_message = _compose_error_final_message(message, stacktrace)
        except Exception as api_exc:
            logger.exception("Falha ao registrar erro da task | task_id=%s", task_id)
            final_message = _compose_error_final_message(
                message,
                stacktrace,
                f"Falha ao registrar task_error: {api_exc}",
            )

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

        print(f"Task finalizada com erro | task_id={task_id}")

    finally:
        if screenshot_reporter is not None:
            try:
                screenshot_reporter.stop(send_final=True)
            except Exception:
                logger.exception("Falha ao finalizar ScreenshotReporter | task_id=%s", task_id)

        if telemetry_collector is not None:
            telemetry_collector.ensure_stopped()

        if task_file and task_file.exists():
            try:
                task_file.unlink()
            except Exception:
                logger.warning("Não foi possível remover o arquivo temporário da task: %s", task_file)

        if result_file and result_file.exists():
            try:
                result_file.unlink()
            except Exception:
                logger.warning("Não foi possível remover o arquivo temporário de resultado: %s", result_file)


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
        execution_mode: str,
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

        payload = {
            "cpu_samples": self.cpu_samples,
            "memory_machine_samples_mb": self.memory_machine_samples_mb,
            "process_memory_samples_mb": self.process_memory_samples_mb,
            "disk_read_mb": disk_read_mb,
            "disk_write_mb": disk_write_mb,
            "net_sent_mb": net_sent_mb,
            "net_recv_mb": net_recv_mb,
            "execution_mode": execution_mode,
        }

        return {
            "captured_at": execution_finished_at.isoformat(),
            "execution_started_at": execution_started_at.isoformat(),
            "execution_finished_at": execution_finished_at.isoformat(),
            "duration_seconds": duration_seconds,
            "cpu_percent_avg": _avg_or_none(self.cpu_samples),
            "cpu_percent_peak": _max_or_none(self.cpu_samples),
            "memory_used_mb_avg": _avg_or_none(self.memory_machine_samples_mb),
            "memory_used_mb_peak": _max_or_none(self.memory_machine_samples_mb),
            "process_memory_mb_peak": _max_or_none(self.process_memory_samples_mb),
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
                    self.cpu_samples.append(round(psutil.cpu_percent(interval=None), 3))
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


def _send_worker_task_log(
    api: TaskApiClient,
    task_id: int,
    logger,
    message: str,
    level: str = LOG_LEVEL_INFO,
) -> None:
    if level == LOG_LEVEL_ERROR:
        logger.error("[WORKER][task_id=%s] %s", task_id, message)
    else:
        logger.info("[WORKER][task_id=%s] %s", task_id, message)

    try:
        api.send_log(
            task_id=task_id,
            level=level,
            message=f"[worker] {message}",
            event_code="worker_log",
        )
    except Exception:
        logger.warning("Falha ao enviar log do worker | task_id=%s", task_id)


def _resolve_bot_for_task(task_data: dict) -> BotRegistryItem:
    registry = load_model(BOTS_REGISTRY_FILE, BotsRegistry)

    if not registry:
        raise RuntimeError("bots_registry.json não encontrado.")

    task_bot_id = task_data.get("bot_id")
    task_bot_version_id = task_data.get("bot_version_id")

    for bot in registry.bots:
        if task_bot_id is not None and str(bot.bot_id) == str(task_bot_id):
            if task_bot_version_id is None or bot.bot_version_id == task_bot_version_id:
                return bot

    if task_bot_version_id is not None:
        for bot in registry.bots:
            if bot.bot_version_id == task_bot_version_id:
                return bot

    raise RuntimeError(
        f"Bot não encontrado no registry local para task_id={task_data.get('task_id')} "
        f"bot_id={task_bot_id} bot_version_id={task_bot_version_id}"
    )


def _ensure_bot_ready(
    bot: BotRegistryItem,
    api: TaskApiClient,
    task_id: int,
    logger,
) -> BotRegistryItem:
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

    if target.auto_update and target.local_path:
        from pathlib import Path as _Path
        bot_dir = _Path(target.local_path)
        try:
            new_commit = git_pull_for_auto_update(
                bot_dir,
                branch=target.branch,
                progress_callback=lambda msg: _send_worker_task_log(api, task_id, logger, msg),
            )
            if new_commit and new_commit != target.installed_commit:
                _send_worker_task_log(
                    api, task_id, logger,
                    f"auto_update: novo commit detectado {new_commit[:12]} — bot sera reinstalado.",
                )
                target.installed_commit = None
                try:
                    api.register_auto_version(
                        bot_id=target.bot_id,
                        commit_hash=new_commit,
                        branch=target.branch,
                    )
                except Exception as exc:
                    logger.warning("Falha ao registrar auto_version no backend | bot_id=%s erro=%s", target.bot_id, exc)
        except Exception as exc:
            logger.warning("Falha no auto_update do bot %s: %s", target.bot_id, exc)

    needs_prepare = False
    expected_bot_dir, expected_venv_dir = resolve_bot_install_paths(target)

    if not target.local_path or not target.venv_path:
        needs_prepare = True
    elif Path(target.local_path) != expected_bot_dir:
        needs_prepare = True
    elif Path(target.venv_path) != expected_venv_dir:
        needs_prepare = True
    elif target.installed_version != target.expected_version:
        needs_prepare = True
    elif target.expected_commit and target.installed_commit != target.expected_commit:
        needs_prepare = True
    elif target.last_install_status in ("error", "not_installed", "outdated"):
        needs_prepare = True

    if needs_prepare:
        _send_worker_task_log(
            api,
            task_id,
            logger,
            f"Bot {target.name or target.bot_id} precisa ser preparado antes da task.",
        )

        result = install_or_update_bot(
            target,
            progress_callback=lambda message: _send_worker_task_log(
                api,
                task_id,
                logger,
                message,
            ),
        )

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
    prepared["execution_mode"] = get_execution_mode(task_data)

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


def _build_result_file_path(task_id: int) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR / f"task_{task_id}_result.json"


def _read_task_result_json(
    *,
    result_file: Path | None,
    api: TaskApiClient,
    task_id: int,
    logger,
) -> str | None:
    if result_file is None or not result_file.exists():
        return None

    try:
        file_size = result_file.stat().st_size
    except Exception as exc:
        _warn_result_json_ignored(
            api=api,
            task_id=task_id,
            logger=logger,
            message=f"Nao foi possivel ler o arquivo de resultado: {exc}",
        )
        return None

    if file_size <= 0:
        return None

    if file_size > RESULT_JSON_MAX_BYTES:
        _warn_result_json_ignored(
            api=api,
            task_id=task_id,
            logger=logger,
            message=(
                "Arquivo de resultado ignorado: tamanho acima do limite de "
                f"{RESULT_JSON_MAX_BYTES} bytes."
            ),
        )
        return None

    try:
        raw_result = result_file.read_text(encoding="utf-8").strip()
    except Exception as exc:
        _warn_result_json_ignored(
            api=api,
            task_id=task_id,
            logger=logger,
            message=f"Nao foi possivel abrir o arquivo de resultado: {exc}",
        )
        return None

    if not raw_result:
        return None

    try:
        parsed_result = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        _warn_result_json_ignored(
            api=api,
            task_id=task_id,
            logger=logger,
            message=f"JSON de resultado invalido; workflow dinamico ignorado: {exc}",
        )
        return None

    if not isinstance(parsed_result, dict):
        _warn_result_json_ignored(
            api=api,
            task_id=task_id,
            logger=logger,
            message="JSON de resultado precisa ser um objeto; workflow dinamico ignorado.",
        )
        return None

    return json.dumps(parsed_result, ensure_ascii=False, separators=(",", ":"))


def _warn_result_json_ignored(
    *,
    api: TaskApiClient,
    task_id: int,
    logger,
    message: str,
) -> None:
    logger.warning("%s | task_id=%s", message, task_id)
    try:
        api.send_log(
            task_id=task_id,
            level=LOG_LEVEL_WARNING,
            message=message,
            event_code="result_json_ignored",
        )
    except Exception:
        logger.exception("Falha ao registrar aviso de result_json | task_id=%s", task_id)


def _write_task_payload_file(task_data: dict, execution_mode: str) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    task_id = task_data.get("task_id") or task_data.get("id")

    if not task_id:
        raise RuntimeError(f"task_id não encontrado no payload da task: {task_data}")

    task_file = TMP_DIR / f"task_{task_id}.json"
    temp_file = TMP_DIR / f"task_{task_id}.json.tmp"
    raw_parameters = task_data.get("parameters") or []
    parameters = raw_parameters if isinstance(raw_parameters, list) else []
    parameters = _sort_parameters_for_bot(parameters)
    parameters_map = _build_parameters_map(parameters)
    credentials_json = _extract_json_parameter(parameters_map, "parameters_json")
    runtime_parameters = _extract_json_parameter(parameters_map, "runtime_parameters_json")

    payload = {
        "task_id": task_id,
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
        "execution_mode": execution_mode,
        "parameters": parameters,
        "parameters_map": parameters_map,
        "parameters_json": credentials_json,
        "credentials_json": credentials_json,
        "runtime_parameters": runtime_parameters,
        "runtime_parameters_json": runtime_parameters,
        "raw_task": task_data,
    }

    content = json.dumps(payload, indent=4, ensure_ascii=False)

    if not content.strip():
        raise RuntimeError("Payload temporário ficou vazio antes de salvar.")

    temp_file.write_text(content, encoding="utf-8")

    if not temp_file.exists() or temp_file.stat().st_size <= 0:
        raise RuntimeError(f"Arquivo temporário foi criado vazio: {temp_file}")

    os.replace(temp_file, task_file)

    if not task_file.exists() or task_file.stat().st_size <= 0:
        raise RuntimeError(f"Arquivo final da task ficou vazio: {task_file}")

    return task_file


def _sort_parameters_for_bot(parameters: list[dict]) -> list[dict]:
    """Garante que parameters_json (dados_acesso) vem antes dos demais parâmetros."""
    priority = {"parameters_json": 0}
    return sorted(parameters, key=lambda p: priority.get(p.get("parameter_name", ""), 1))


def _build_parameters_map(parameters: list[dict]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}

    for param in parameters:
        if not isinstance(param, dict):
            continue

        name = param.get("parameter_name")
        if not name:
            continue

        value = param.get("parameter_value")
        result[str(name)] = value if value is None else str(value)

    return result


def _extract_json_parameter(parameters_map: dict[str, str | None], parameter_name: str):
    raw_value = parameters_map.get(parameter_name)

    if not raw_value:
        return None

    try:
        return json.loads(raw_value)
    except Exception:
        return None


def _send_output_logs(
    api: TaskApiClient,
    task_id: int,
    stdout_text: str | None,
    stderr_text: str | None,
) -> None:
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
        level, error_type = _classify_stderr_log(line)
        api.send_log(
            task_id=task_id,
            level=level,
            message=line,
            error_type=error_type,
            sequence_number=sequence,
        )
        sequence += 1


def _log_process_output_locally(
    logger,
    task_id: int,
    stdout_text: str | None,
    stderr_text: str | None,
) -> None:
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


def _compose_error_final_message(
    message: str,
    stacktrace: str | None,
    extra: str | None = None,
) -> str:
    parts = [message]

    if extra:
        parts.append(extra)

    if stacktrace:
        parts.append("TRACEBACK:")
        parts.append(_shorten_text(stacktrace, 2500))

    return _shorten_text("\n".join(parts), 3500)


def _shorten_text(text: str | None, max_len: int) -> str:
    if text is None:
        return ""

    if len(text) <= max_len:
        return text

    return text[:max_len]


def _classify_stderr_log(line: str) -> tuple[str, str | None]:
    text = (line or "").strip().lower()
    if any(marker in text for marker in STDERR_ERROR_MARKERS):
        return LOG_LEVEL_ERROR, "stderr"

    return LOG_LEVEL_WARNING, None


def _raise_if_remote_stop_requested(
    *,
    process: subprocess.Popen,
    api: TaskApiClient,
    task_id: int,
    logger,
) -> None:
    try:
        status_payload = api.check_task_status(task_id)
    except Exception as exc:
        logger.warning(
            "Falha ao consultar status remoto da task | task_id=%s erro=%s",
            task_id,
            exc,
        )
        return

    remote_status = str(status_payload.get("status") or "").strip().lower()
    stop_requested = bool(status_payload.get("stop_requested"))

    if not stop_requested and remote_status not in {
        TASK_STATUS_CANCELED,
        TASK_STATUS_FORCED_STOP,
    }:
        return

    logger.warning(
        "Parada remota detectada; encerrando processo local | task_id=%s status=%s",
        task_id,
        remote_status or "stop_requested",
    )
    _kill_process_tree(process.pid)
    raise TaskStopRequested(remote_status or "stop_requested")


def _kill_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )


def _avg_or_none(values: list[float]) -> float | None:
    if not values:
        return None

    return round(sum(values) / len(values), 3)


def _max_or_none(values: list[float]) -> float | None:
    if not values:
        return None

    return round(max(values), 3)

def _reader_thread(pipe, output_queue: queue.Queue, stream_name: str) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if line:
                output_queue.put((stream_name, line.rstrip()))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


class AsyncTaskLogSender:
    def __init__(self, api: TaskApiClient, task_id: int, logger) -> None:
        self.api = api
        self.task_id = task_id
        self.logger = logger
        self.queue: queue.Queue = queue.Queue(maxsize=ASYNC_LOG_QUEUE_MAX_SIZE)
        self.stop_event = threading.Event()
        self.dropped_count = 0
        self.failed_count = 0
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"log-sender-{task_id}",
        )

    def start(self) -> None:
        self.thread.start()

    def enqueue(
        self,
        *,
        level: str,
        message: str,
        error_type: str | None,
        sequence_number: int,
    ) -> None:
        try:
            self.queue.put_nowait(
                {
                    "level": level,
                    "message": message,
                    "error_type": error_type,
                    "sequence_number": sequence_number,
                }
            )
        except queue.Full:
            self.dropped_count += 1

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=ASYNC_LOG_SHUTDOWN_TIMEOUT_SECONDS)

        if self.thread.is_alive():
            self.logger.warning(
                "Envio assincrono de logs ainda em andamento | task_id=%s pendentes=%s",
                self.task_id,
                self.queue.qsize(),
            )

        if self.dropped_count:
            self.logger.warning(
                "Logs descartados por fila cheia | task_id=%s total=%s",
                self.task_id,
                self.dropped_count,
            )

        if self.failed_count:
            self.logger.warning(
                "Falhas no envio assincrono de logs | task_id=%s total=%s",
                self.task_id,
                self.failed_count,
            )

    def _run(self) -> None:
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                self.api.send_log(
                    task_id=self.task_id,
                    level=item["level"],
                    message=item["message"],
                    error_type=item["error_type"],
                    sequence_number=item["sequence_number"],
                )
            except Exception:
                self.failed_count += 1
            finally:
                self.queue.task_done()

def _consume_process_output_live(
    *,
    process: subprocess.Popen,
    timeout_seconds: int,
    logger,
    task_id: int,
    api: TaskApiClient,
) -> tuple[str, str]:
    output_queue: queue.Queue = queue.Queue()

    stdout_thread = threading.Thread(
        target=_reader_thread,
        args=(process.stdout, output_queue, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader_thread,
        args=(process.stderr, output_queue, "stderr"),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    start_time = time.time()
    last_status_check_at = 0.0
    sequence = 1
    log_sender = AsyncTaskLogSender(api=api, task_id=task_id, logger=logger)
    log_sender.start()

    try:
        while True:
            if process.poll() is not None and output_queue.empty():
                break

            if time.time() - start_time > timeout_seconds:
                _kill_process_tree(process.pid)
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)

            if time.time() - last_status_check_at >= STOP_STATUS_CHECK_INTERVAL_SECONDS:
                last_status_check_at = time.time()
                _raise_if_remote_stop_requested(
                    process=process,
                    api=api,
                    task_id=task_id,
                    logger=logger,
                )

            try:
                stream_name, line = output_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            line = _shorten_text(line, 4000)

            if stream_name == "stdout":
                _append_capped(stdout_lines, line, MAX_CAPTURED_OUTPUT_LINES)
                logger.info("[BOT][task_id=%s] %s", task_id, line)
                level = LOG_LEVEL_INFO
                error_type = None
            else:
                _append_capped(stderr_lines, line, MAX_CAPTURED_OUTPUT_LINES)
                level, error_type = _classify_stderr_log(line)
                if level == LOG_LEVEL_ERROR:
                    logger.error("[BOT][task_id=%s] %s", task_id, line)
                else:
                    logger.warning("[BOT][task_id=%s] %s", task_id, line)

            log_sender.enqueue(
                level=level,
                message=line,
                error_type=error_type,
                sequence_number=sequence,
            )

            sequence += 1

        while True:
            try:
                stream_name, line = output_queue.get_nowait()
            except queue.Empty:
                break

            line = _shorten_text(line, 4000)

            if stream_name == "stdout":
                _append_capped(stdout_lines, line, MAX_CAPTURED_OUTPUT_LINES)
                level = LOG_LEVEL_INFO
                error_type = None
            else:
                _append_capped(stderr_lines, line, MAX_CAPTURED_OUTPUT_LINES)
                level, error_type = _classify_stderr_log(line)

            log_sender.enqueue(
                level=level,
                message=line,
                error_type=error_type,
                sequence_number=sequence,
            )
            sequence += 1
    finally:
        log_sender.close()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

    return "\n".join(stdout_lines), "\n".join(stderr_lines)


def _append_capped(lines: list[str], line: str, max_lines: int) -> None:
    lines.append(line)
    if len(lines) > max_lines:
        del lines[: len(lines) - max_lines]

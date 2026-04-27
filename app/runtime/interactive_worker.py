from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from app.core.config_models import AuthData, RunnerData
from app.core.constants import AUTH_FILE, EXECUTION_MODE_FOREGROUND, RUNNER_FILE
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.core.logging_config import setup_logging
from app.core.security import unprotect_text
from app.runtime.foreground_session import get_interactive_session_info
from app.runtime.task_client import TaskApiClient
from app.runtime.task_executor import execute_task, get_execution_mode


ERROR_ALREADY_EXISTS = 183
INTERACTIVE_WORKER_MUTEX_NAME = "Global\\OrkaFlowInteractiveWorker"


def _acquire_single_instance_mutex(logger):
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        mutex_handle = kernel32.CreateMutexW(
            None,
            True,
            INTERACTIVE_WORKER_MUTEX_NAME,
        )

        last_error = kernel32.GetLastError()

        if not mutex_handle:
            logger.error("Não foi possível criar mutex do interactive worker.")
            return None, False

        if last_error == ERROR_ALREADY_EXISTS:
            logger.warning("Interactive Worker já está em execução. Encerrando nova instância.")
            return mutex_handle, False

        return mutex_handle, True

    except Exception as exc:
        logger.warning("Falha ao validar instância única do interactive worker: %s", exc)
        return None, True


def _build_task_api(auth: AuthData, access_token: str, runner: RunnerData) -> tuple[HttpClient, TaskApiClient]:
    client = HttpClient(base_url=auth.base_url)
    client.set_token(access_token)

    task_api = TaskApiClient(
        client=client,
        runner_uuid=runner.uuid,
        runner_token=runner.runner_token,
    )
    return client, task_api


def _interactive_session_ready() -> tuple[bool, str]:
    session_info = get_interactive_session_info()

    if not session_info.has_active_session:
        return False, session_info.message or "Nenhuma sessão interativa ativa encontrada."

    return True, session_info.message or "Sessão interativa ativa encontrada."


def _recover_startup_locks(task_api: TaskApiClient, runner: RunnerData, logger) -> None:
    try:
        recovery = task_api.release_startup_locks()
    except Exception as exc:
        logger.warning(
            "Falha ao executar recuperação inicial do interactive worker | runner_id=%s erro=%s",
            runner.id,
            exc,
        )
        return

    logger.warning(
        "Recuperação inicial do interactive worker concluída | runner_id=%s tasks_finalizadas=%s task_locks_liberados=%s runner_locks_liberados=%s",
        runner.id,
        recovery.get("tasks_finalized", 0),
        recovery.get("task_locks_released", 0),
        recovery.get("runner_locks_released", 0),
    )


def _should_skip_before_claim(task_data: dict, logger) -> tuple[bool, str]:
    execution_mode = get_execution_mode(task_data)

    if execution_mode != EXECUTION_MODE_FOREGROUND:
        return True, f"Task não é foreground. execution_mode={execution_mode}"

    session_ready, session_reason = _interactive_session_ready()
    if not session_ready:
        return True, session_reason

    return False, "Task foreground elegível para execução interativa."


def _execute_foreground_directly(
    *,
    auth: AuthData,
    access_token: str,
    runner: RunnerData,
    task_data: dict,
    logger,
) -> None:
    execution_mode = get_execution_mode(task_data)
    task_id = task_data.get("task_id")

    if execution_mode != EXECUTION_MODE_FOREGROUND:
        logger.warning(
            "Task ignorada no interactive worker por não ser foreground | task_id=%s execution_mode=%s",
            task_id,
            execution_mode,
        )
        return

    logger.info(
        "Iniciando execução foreground direta no interactive worker | task_id=%s",
        task_id,
    )

    execute_task(
        auth=auth,
        access_token=access_token,
        runner=runner,
        task_data=task_data,
        logger=logger,
    )


def main() -> None:
    os.environ["ORKAFLOW_WORKER_ROLE"] = "interactive"

    logger = setup_logging()

    mutex_handle, can_run = _acquire_single_instance_mutex(logger)
    if not can_run:
        print("Interactive Worker já está em execução.")
        return

    print("=== INICIANDO INTERACTIVE WORKER ===")

    auth = load_model(AUTH_FILE, AuthData)
    runner = load_model(RUNNER_FILE, RunnerData)

    if not auth or not runner:
        print("Interactive Worker não configurado. auth.json ou runner.json não encontrados.")
        logger.error("Interactive Worker não configurado. auth.json ou runner.json não encontrados.")
        return

    access_token = unprotect_text(auth.encrypted_access_token)
    _, task_api = _build_task_api(auth, access_token, runner)

    try:
        _recover_startup_locks(task_api, runner, logger)
    except Exception as exc:
        logger.exception("Erro na recuperação inicial do interactive worker: %s", exc)

    while True:
        try:
            save_model(RUNNER_FILE, runner)

            session_ready, session_reason = _interactive_session_ready()
            if not session_ready:
                logger.warning(
                    "Sessão interativa indisponível no interactive worker | motivo=%s",
                    session_reason,
                )
                time.sleep(max(3, runner.config.polling_interval))
                continue

            try:
                task_api.heartbeat(
                    ip=runner.ip,
                    running_tasks=0,
                )
            except Exception as exc:
                logger.warning("Falha ao enviar heartbeat do interactive worker: %s", exc)

            next_task = task_api.next_task(EXECUTION_MODE_FOREGROUND)

            if not next_task.get("found"):
                time.sleep(runner.config.polling_interval)
                continue

            task_id = next_task.get("task_id")
            execution_mode = get_execution_mode(next_task)

            should_skip, reason = _should_skip_before_claim(next_task, logger)
            if should_skip:
                logger.info(
                    "Task ignorada pelo interactive worker antes do claim | task_id=%s execution_mode=%s motivo=%s",
                    task_id,
                    execution_mode,
                    reason,
                )
                time.sleep(2)
                continue

            try:
                task_api.claim_task(int(task_id))
            except Exception as exc:
                logger.warning(
                    "Falha ao dar claim na task no interactive worker | task_id=%s erro=%s",
                    task_id,
                    exc,
                )
                time.sleep(2)
                continue

            _execute_foreground_directly(
                auth=auth,
                access_token=access_token,
                runner=runner,
                task_data=next_task,
                logger=logger,
            )

        except Exception as exc:
            print(f"[INTERACTIVE_WORKER] erro no ciclo: {exc}")
            logger.exception("Erro no ciclo do interactive worker: %s", exc)

        time.sleep(max(1, runner.config.polling_interval))


if __name__ == "__main__":
    main()
    
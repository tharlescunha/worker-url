from __future__ import annotations

import os
import time

from app.core.config_models import AuthData, RunnerData
from app.core.constants import AUTH_FILE, RUNNER_FILE
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.core.logging_config import setup_logging
from app.core.security import unprotect_text
from app.runtime.task_client import TaskApiClient
from app.runtime.task_executor import get_execution_mode
from app.runtime.task_manager import TaskExecutionManager
from app.sync.bot_sync import sync_bots


def recover_runner_startup_tasks(task_api: TaskApiClient, runner: RunnerData, logger) -> None:
    try:
        recovery = task_api.release_startup_locks()
    except Exception as exc:
        logger.warning(
            "Falha ao executar recuperação inicial | runner_id=%s erro=%s",
            runner.id,
            exc,
        )
        return

    logger.warning(
        "Recuperação inicial concluída | runner_id=%s tasks_finalizadas=%s task_locks_liberados=%s runner_locks_liberados=%s",
        runner.id,
        recovery.get("tasks_finalized", 0),
        recovery.get("task_locks_released", 0),
        recovery.get("runner_locks_released", 0),
    )


def build_task_api(
    auth: AuthData,
    access_token: str,
    runner: RunnerData,
) -> tuple[HttpClient, TaskApiClient]:
    client = HttpClient(base_url=auth.base_url)
    client.set_token(access_token)

    task_api = TaskApiClient(
        client=client,
        runner_uuid=runner.uuid,
        runner_token=runner.runner_token,
    )

    return client, task_api


def fetch_next_task(task_api: TaskApiClient) -> dict:
    return task_api.next_task(None)


def main() -> None:
    os.environ["ORKAFLOW_WORKER_ROLE"] = "local"

    logger = setup_logging()

    print("=== ORKAFLOW WORKER ===")
    print("Worker iniciado em modo simples.")
    print("Deixe este terminal aberto para executar as atividades.")
    print()

    auth = load_model(AUTH_FILE, AuthData)
    runner = load_model(RUNNER_FILE, RunnerData)

    if not auth or not runner:
        print("Worker não configurado. auth.json ou runner.json não encontrados.")
        logger.error("Worker não configurado. auth.json ou runner.json não encontrados.")
        return

    access_token = unprotect_text(auth.encrypted_access_token)
    client, task_api = build_task_api(auth, access_token, runner)

    manager = TaskExecutionManager(
        auth=auth,
        access_token=access_token,
        runner=runner,
        logger=logger,
    )

    recover_runner_startup_tasks(task_api, runner, logger)

    while True:
        try:
            if runner.config.auto_update_bots:
                sync_bots(client, runner)
                save_model(RUNNER_FILE, runner)

            manager.cleanup_finished()
            active_count = manager.active_count()

            try:
                task_api.heartbeat(
                    ip=runner.ip,
                    running_tasks=active_count,
                )
            except Exception as exc:
                logger.warning("Falha ao enviar heartbeat: %s", exc)

            while manager.has_capacity(runner.config.max_concurrency):
                next_task = fetch_next_task(task_api)

                if not next_task.get("found"):
                    break

                task_id = int(next_task["task_id"])
                execution_mode = get_execution_mode(next_task)

                can_start, reason = manager.can_start_task(next_task)
                if not can_start:
                    logger.info(
                        "Task ignorada antes do claim | task_id=%s execution_mode=%s motivo=%s",
                        task_id,
                        execution_mode,
                        reason,
                    )
                    break

                try:
                    task_api.claim_task(task_id)
                except Exception as exc:
                    logger.warning(
                        "Falha ao dar claim na task | task_id=%s erro=%s",
                        task_id,
                        exc,
                    )
                    break

                started = manager.start_task(next_task)

                if not started:
                    logger.warning(
                        "Task não iniciada após claim | task_id=%s execution_mode=%s",
                        task_id,
                        execution_mode,
                    )

                    try:
                        task_api.finish_task(
                            task_id=task_id,
                            status="canceled",
                            final_message=(
                                "Task cancelada porque o worker não conseguiu "
                                "iniciar a execução local após o claim."
                            ),
                            items_processed=0,
                            items_failed=1,
                        )
                    except Exception:
                        logger.exception(
                            "Falha ao cancelar task não iniciada após claim | task_id=%s",
                            task_id,
                        )

                    break

                print(f"Task iniciada | task_id={task_id} | modo={execution_mode}")
                logger.info(
                    "Task enviada para execução local | task_id=%s execution_mode=%s",
                    task_id,
                    execution_mode,
                )

        except Exception as exc:
            print(f"[WORKER] erro no ciclo: {exc}")
            logger.exception("Erro no ciclo do worker: %s", exc)

        time.sleep(max(1, int(runner.config.polling_interval or 10)))


if __name__ == "__main__":
    main()
    
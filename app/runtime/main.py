from __future__ import annotations

import time

from app.core.config_models import AuthData, RunnerData
from app.core.constants import AUTH_FILE, RUNNER_FILE
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.core.logging_config import setup_logging
from app.core.security import unprotect_text
from app.runtime.task_client import TaskApiClient
from app.runtime.task_manager import TaskExecutionManager
from app.sync.bot_sync import sync_bots


def recover_runner_startup_tasks(task_api: TaskApiClient, runner: RunnerData, logger) -> None:
    try:
        response = task_api.list_active_tasks()
    except Exception as exc:
        logger.warning(
            "Falha ao consultar tasks ativas na inicialização | runner_id=%s erro=%s",
            runner.id,
            exc,
        )
        return

    items = response.get("items", [])
    total = response.get("total", 0)

    if not isinstance(items, list):
        logger.warning(
            "Resposta inesperada ao consultar tasks ativas na inicialização | runner_id=%s response=%s",
            runner.id,
            response,
        )
        return

    if total <= 0 and not items:
        return

    logger.warning(
        "Tasks ativas encontradas no startup | runner_id=%s total=%s",
        runner.id,
        total if isinstance(total, int) else len(items),
    )

    for item in items:
        logger.warning(
            "Task ativa identificada | task_id=%s automation_id=%s status=%s",
            item.get("id"),
            item.get("automation_id"),
            item.get("status"),
        )

    try:
        recovery = task_api.release_startup_locks()
    except Exception as exc:
        logger.exception(
            "Falha ao executar recuperação inicial do worker | runner_id=%s erro=%s",
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


def main() -> None:
    logger = setup_logging()

    print("=== INICIANDO RUNTIME DO WORKER ===")

    auth = load_model(AUTH_FILE, AuthData)
    runner = load_model(RUNNER_FILE, RunnerData)

    if not auth or not runner:
        print("Worker não configurado. auth.json ou runner.json não encontrados.")
        logger.error("Worker não configurado. auth.json ou runner.json não encontrados.")
        return

    access_token = unprotect_text(auth.encrypted_access_token)

    client = HttpClient(base_url=auth.base_url)
    client.set_token(access_token)

    task_api = TaskApiClient(
        client=client,
        runner_uuid=runner.uuid,
        runner_token=runner.runner_token,
    )

    manager = TaskExecutionManager(
        auth=auth,
        access_token=access_token,
        runner=runner,
        logger=logger,
    )

    try:
        recover_runner_startup_tasks(task_api, runner, logger)
    except Exception as exc:
        logger.exception("Erro na recuperação inicial do worker: %s", exc)

    while True:
        try:
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
                next_task = task_api.next_task()

                if not next_task.get("found"):
                    break

                task_id = next_task["task_id"]

                try:
                    task_api.claim_task(task_id)
                except Exception as exc:
                    logger.warning(
                        "Falha ao dar claim na task %s: %s",
                        task_id,
                        exc,
                    )
                    break

                started = manager.start_task(next_task)
                if not started:
                    logger.warning("Task %s já está em execução local.", task_id)
                    break

        except Exception as exc:
            print(f"[WORKER] erro no ciclo: {exc}")
            logger.exception("Erro no ciclo do worker: %s", exc)

        time.sleep(runner.config.polling_interval)


if __name__ == "__main__":
    main()
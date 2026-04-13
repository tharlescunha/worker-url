from __future__ import annotations

from app.runtime.task_client import TaskApiClient


def recover_running_tasks(task_api: TaskApiClient, logger):
    """
    Reconcilia tasks que ficaram como RUNNING no backend
    mas não estão sendo executadas localmente.

    Observação:
    - este fluxo continua conservador
    - se a task ficou RUNNING e o worker reiniciou, ela será marcada como erro
    - no futuro, se o backend passar a devolver mais contexto de foreground,
      este método pode distinguir melhor tarefas interativas órfãs
    """

    try:
        response = task_api.client.get(
            "/api/v1/tasks/",
            params={"status": "RUNNING"},
        )
        tasks = response.get("items", [])

    except Exception as exc:
        logger.warning("Falha ao buscar tasks em execução: %s", exc)
        return

    recovered = 0

    for task in tasks:
        task_id = task.get("id")
        execution_mode = str(
            task.get("execution_mode")
            or task.get("bot_execution_mode")
            or "background"
        ).strip().lower()

        try:
            logger.warning(
                "Task %s estava RUNNING mas não existe execução local. "
                "Marcando como erro. execution_mode=%s",
                task_id,
                execution_mode,
            )

            task_api.send_error(
                task_id=task_id,
                error_type="orphan_task",
                message=(
                    "Task ficou órfã após reinício do worker."
                    if execution_mode != "foreground"
                    else "Task foreground ficou órfã após reinício do worker/agente."
                ),
                stacktrace=f"Worker restart recovery | execution_mode={execution_mode}",
                code="WORKER_RECOVERY",
                is_retryable=True,
            )

            task_api.finish_task(
                task_id=task_id,
                status="error",
                final_message=(
                    "Task recuperada automaticamente após falha do worker."
                    if execution_mode != "foreground"
                    else "Task foreground recuperada automaticamente após falha do worker/agente."
                ),
                items_processed=0,
                items_failed=1,
            )

            recovered += 1

        except Exception as exc:
            logger.exception("Falha ao recuperar task %s: %s", task_id, exc)

    if recovered:
        logger.warning("Recuperação concluída | tasks_corrigidas=%s", recovered)
        
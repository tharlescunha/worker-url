from app.runtime.task_client import TaskApiClient


def recover_running_tasks(task_api: TaskApiClient, logger):
    """
    Reconcilia tasks que ficaram como RUNNING no backend
    mas não estão sendo executadas localmente.
    """

    try:
        response = task_api.client.get("/api/v1/tasks/", params={
            "status": "RUNNING"
        })

        tasks = response.get("items", [])

    except Exception as exc:
        logger.warning("Falha ao buscar tasks em execução: %s", exc)
        return

    recovered = 0

    for task in tasks:
        task_id = task.get("id")

        try:
            logger.warning(
                "Task %s estava RUNNING mas não existe execução local. Marcando como erro.",
                task_id,
            )

            task_api.send_error(
                task_id=task_id,
                error_type="orphan_task",
                message="Task ficou órfã após reinício do worker.",
                stacktrace="Worker restart recovery",
                code="WORKER_RECOVERY",
                is_retryable=True,
            )

            task_api.finish_task(
                task_id=task_id,
                status="error",
                final_message="Task recuperada automaticamente após falha do worker.",
                items_processed=0,
                items_failed=1,
            )

            recovered += 1

        except Exception as exc:
            logger.exception("Falha ao recuperar task %s: %s", task_id, exc)

    if recovered:
        logger.warning("Recuperação concluída | tasks_corrigidas=%s", recovered)
        
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from app.core.config_models import AuthData, RunnerData
from app.core.constants import AUTH_FILE, RUNNER_FILE
from app.core.exceptions import AuthenticationError
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.core.logging_config import setup_logging
from app.core.security import protect_text, unprotect_text
from app.runtime.task_client import TaskApiClient
from app.runtime.task_executor import get_execution_mode
from app.runtime.task_manager import TaskExecutionManager
from app.sync.bot_sync import sync_bots


MAX_WORKER_LOG_BUFFER = 200
BOT_SYNC_INTERVAL_SECONDS = 300

REFRESH_PATH = "/api/v1/auth/refresh"
# Quantos ciclos consecutivos com AuthenticationError antes de tentar refresh
_AUTH_ERROR_THRESHOLD = 1
# Quantas tentativas de refresh antes de pausar por mais tempo
_MAX_REFRESH_FAILURES = 3
_REFRESH_PAUSE_SECONDS = 60


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
    for execution_mode in ("foreground", "background"):
        response = task_api.next_task(execution_mode)

        if response.get("found"):
            return response

    return {"found": False}


def send_worker_heartbeat(
    task_api: TaskApiClient,
    runner: RunnerData,
    logger,
    active_count: int,
) -> None:
    try:
        task_api.heartbeat(
            ip=runner.ip,
            running_tasks=active_count,
        )
    except Exception as exc:
        logger.warning("Falha ao enviar heartbeat: %s", exc)


def queue_worker_log(
    worker_log_buffer: list[tuple[str, str]],
    logger,
    message: str,
    level: str = "info",
) -> None:
    if level == "error":
        logger.error(message)
    elif level == "warning":
        logger.warning(message)
    else:
        logger.info(message)

    worker_log_buffer.append((level, message))
    if len(worker_log_buffer) > MAX_WORKER_LOG_BUFFER:
        del worker_log_buffer[:-MAX_WORKER_LOG_BUFFER]


def flush_worker_logs_to_task(
    task_api: TaskApiClient,
    logger,
    task_id: int,
    worker_log_buffer: list[tuple[str, str]],
) -> None:
    if not worker_log_buffer:
        return

    pending_logs = list(worker_log_buffer)
    worker_log_buffer.clear()

    for level, message in pending_logs:
        try:
            task_api.send_log(
                task_id=task_id,
                level=level,
                message=f"[worker] {message}",
                event_code="worker_log",
            )
        except Exception as exc:
            logger.warning(
                "Falha ao enviar log do worker para a task | task_id=%s erro=%s",
                task_id,
                exc,
            )


def try_refresh_token(
    auth: AuthData,
    client: HttpClient,
    logger,
) -> bool:
    """
    Tenta renovar o access_token usando o refresh_token armazenado.
    Atualiza o client e o auth.json em caso de sucesso.
    Retorna True se conseguiu, False caso contrário.
    """
    if not auth.encrypted_refresh_token:
        logger.error("Token expirado e refresh_token não disponível — reinicie o worker manualmente.")
        return False

    try:
        refresh_token = unprotect_text(auth.encrypted_refresh_token)
    except Exception as exc:
        logger.error("Falha ao descriptografar refresh_token: %s", exc)
        return False

    try:
        response = client.post(REFRESH_PATH, {"refresh_token": refresh_token})
    except Exception as exc:
        logger.error("Falha ao chamar endpoint de refresh: %s", exc)
        return False

    new_access = response.get("access_token")
    new_refresh = response.get("refresh_token")

    if not new_access:
        logger.error("Endpoint de refresh não retornou access_token.")
        return False

    client.set_token(new_access)

    try:
        auth.encrypted_access_token = protect_text(new_access)
        if new_refresh:
            auth.encrypted_refresh_token = protect_text(new_refresh)
        auth.saved_at = datetime.now(timezone.utc)
        save_model(AUTH_FILE, auth)
        logger.info("Token renovado com sucesso e salvo em auth.json.")
    except Exception as exc:
        # Token atualizado em memória mas não persistido — ainda funciona até próximo restart
        logger.warning("Token renovado mas falha ao salvar auth.json: %s", exc)

    return True


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
    worker_log_buffer: list[tuple[str, str]] = []
    last_bot_sync_at = 0.0

    consecutive_auth_errors = 0
    consecutive_refresh_failures = 0

    while True:
        try:
            manager.cleanup_finished()
            active_count = manager.active_count()

            send_worker_heartbeat(
                task_api=task_api,
                runner=runner,
                logger=logger,
                active_count=active_count,
            )

            now_monotonic = time.monotonic()
            should_sync_bots = (
                runner.config.auto_update_bots
                and active_count == 0
                and (
                    last_bot_sync_at == 0.0
                    or now_monotonic - last_bot_sync_at >= BOT_SYNC_INTERVAL_SECONDS
                )
            )

            if should_sync_bots:
                sync_started_at = time.monotonic()
                try:
                    sync_bots(
                        client,
                        runner,
                        progress_callback=lambda message: queue_worker_log(
                            worker_log_buffer,
                            logger,
                            message,
                        ),
                    )
                    save_model(RUNNER_FILE, runner)
                    logger.info(
                        "Sync de bots concluido | duracao_segundos=%.1f",
                        time.monotonic() - sync_started_at,
                    )
                except Exception:
                    logger.exception(
                        "Falha no sync de bots | proxima_tentativa_segundos=%s",
                        BOT_SYNC_INTERVAL_SECONDS,
                    )
                finally:
                    last_bot_sync_at = time.monotonic()

                send_worker_heartbeat(
                    task_api=task_api,
                    runner=runner,
                    logger=logger,
                    active_count=manager.active_count(),
                )

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

                flush_worker_logs_to_task(
                    task_api=task_api,
                    logger=logger,
                    task_id=task_id,
                    worker_log_buffer=worker_log_buffer,
                )

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

            # Ciclo concluído sem erros — zera contadores de erro
            consecutive_auth_errors = 0
            consecutive_refresh_failures = 0

        except AuthenticationError as exc:
            consecutive_auth_errors += 1
            logger.warning(
                "Erro de autenticação no ciclo do worker (tentativa %s): %s",
                consecutive_auth_errors,
                exc,
            )

            if consecutive_auth_errors >= _AUTH_ERROR_THRESHOLD:
                logger.warning("Token possivelmente expirado — tentando renovar...")
                refreshed = try_refresh_token(auth, client, logger)

                if refreshed:
                    consecutive_auth_errors = 0
                    consecutive_refresh_failures = 0
                    # Atualiza task_api com o novo token já configurado no client
                    task_api = TaskApiClient(
                        client=client,
                        runner_uuid=runner.uuid,
                        runner_token=runner.runner_token,
                    )
                    manager.access_token = unprotect_text(auth.encrypted_access_token)
                    print("[WORKER] Token renovado com sucesso — continuando.")
                else:
                    consecutive_refresh_failures += 1
                    logger.error(
                        "Falha ao renovar token (tentativa %s/%s).",
                        consecutive_refresh_failures,
                        _MAX_REFRESH_FAILURES,
                    )

                    if consecutive_refresh_failures >= _MAX_REFRESH_FAILURES:
                        logger.critical(
                            "Não foi possível renovar o token após %s tentativas. "
                            "Pausando por %ss. Reregistre o worker se o problema persistir.",
                            _MAX_REFRESH_FAILURES,
                            _REFRESH_PAUSE_SECONDS,
                        )
                        print(
                            f"[WORKER] CRÍTICO: token inválido após {_MAX_REFRESH_FAILURES} "
                            f"tentativas de refresh. Pausando {_REFRESH_PAUSE_SECONDS}s."
                        )
                        time.sleep(_REFRESH_PAUSE_SECONDS)
                        consecutive_refresh_failures = 0

        except Exception as exc:
            print(f"[WORKER] erro no ciclo: {exc}")
            logger.exception("Erro no ciclo do worker: %s", exc)

        time.sleep(max(1, int(runner.config.polling_interval or 10)))


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import sys
import threading
import time

from app.core.config_models import AuthData, RunnerData
from app.core.constants import (
    AUTH_FILE,
    RUNNER_FILE,
    RUNTIME_EVENT_TYPE_AUTH_FAILED,
    RUNTIME_EVENT_TYPE_BOT_INSTALL_FAILED,
    RUNTIME_EVENT_TYPE_FORBIDDEN,
    RUNTIME_EVENT_TYPE_SYNC_FAILED,
    RUNTIME_EVENT_TYPE_WORKER_STARTED,
    WORKER_VERSION,
)
from app.core.exceptions import AuthenticationError, ForbiddenError
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.core.logging_config import setup_logging
from app.core.security import unprotect_text
from app.runtime.auth_refresh import TokenRefresher
from app.runtime.events import EventReporter
from app.runtime.services import (
    HeartbeatThread,
    LoopProgress,
    PulseThread,
    describe_session,
)
from app.runtime.task_client import TaskApiClient
from app.runtime.task_executor import get_execution_mode
from app.runtime.task_manager import TaskExecutionManager
from app.sync.bot_sync import sync_bots


MAX_WORKER_LOG_BUFFER = 200
BOT_SYNC_INTERVAL_SECONDS = 300

# Códigos de saída lidos pelo supervisor / .bat
EXIT_NOT_CONFIGURED = 2
EXIT_CANNOT_READ_TOKEN = 3

_AUTH_FAILURE_PAUSE_SECONDS = 60


class WorkerLogBuffer:
    """Mensagens do worker (ex.: sync) que são anexadas ao log da próxima task."""

    def __init__(self, logger) -> None:
        self.logger = logger
        self._lock = threading.Lock()
        self._items: list[tuple[str, str]] = []

    def add(self, message: str, level: str = "info") -> None:
        if level == "error":
            self.logger.error(message)
        elif level == "warning":
            self.logger.warning(message)
        else:
            self.logger.info(message)

        with self._lock:
            self._items.append((level, message))
            if len(self._items) > MAX_WORKER_LOG_BUFFER:
                del self._items[:-MAX_WORKER_LOG_BUFFER]

    def drain(self) -> list[tuple[str, str]]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items


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


def fetch_next_task(task_api: TaskApiClient, execution_modes: list[str]) -> dict:
    for execution_mode in execution_modes:
        response = task_api.next_task(execution_mode)

        if response.get("found"):
            return response

    return {"found": False}


def flush_worker_logs_to_task(
    task_api: TaskApiClient,
    logger,
    task_id: int,
    log_buffer: WorkerLogBuffer,
) -> None:
    for level, message in log_buffer.drain():
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


class BotSyncThread(threading.Thread):
    """
    Sincroniza e prepara bots fora do loop principal, para que instalação
    (git/pip) nunca atrase polling de tasks nem heartbeat.
    """

    def __init__(
        self,
        client: HttpClient,
        runner: RunnerData,
        log_buffer: WorkerLogBuffer,
        events: EventReporter,
        logger,
    ) -> None:
        super().__init__(daemon=True, name="bot-sync")
        self.client = client
        self.runner = runner
        self.log_buffer = log_buffer
        self.events = events
        self.logger = logger
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            if self.runner.config.auto_update_bots:
                self._sync_once()

            self._stop_event.wait(BOT_SYNC_INTERVAL_SECONDS)

    def _sync_once(self) -> None:
        started_at = time.monotonic()

        try:
            sync_bots(
                self.client,
                self.runner,
                progress_callback=lambda message: self.log_buffer.add(message),
                failure_callback=self._on_bot_failure,
                install_missing=False,
            )
            save_model(RUNNER_FILE, self.runner)
            self.logger.info(
                "Sync de bots concluido | duracao_segundos=%.1f",
                time.monotonic() - started_at,
            )
        except Exception as exc:
            self.logger.exception(
                "Falha no sync de bots | proxima_tentativa_segundos=%s",
                BOT_SYNC_INTERVAL_SECONDS,
            )
            self.events.report(
                RUNTIME_EVENT_TYPE_SYNC_FAILED,
                f"Falha no sync de bots: {exc}",
                reason="sync_failed",
            )

    def _on_bot_failure(self, bot, error: str) -> None:
        self.events.report(
            RUNTIME_EVENT_TYPE_BOT_INSTALL_FAILED,
            f"Falha ao preparar bot {bot.name or bot.bot_id}: {error}",
            reason="install_failed",
            bot_id=bot.bot_id,
        )


def main() -> int:
    os.environ["ORKAFLOW_WORKER_ROLE"] = "local"

    logger = setup_logging()

    print("=== ORKAFLOW WORKER ===")
    print(f"Versão {WORKER_VERSION} | {describe_session()}")
    print("Deixe este terminal aberto para executar as atividades.")
    print()

    try:
        auth = load_model(AUTH_FILE, AuthData)
        runner = load_model(RUNNER_FILE, RunnerData)
    except Exception as exc:
        logger.exception("Arquivos de configuração do worker inválidos: %s", exc)
        print(f"Configuração do worker inválida: {exc}")
        return EXIT_NOT_CONFIGURED

    if not auth or not runner:
        print("Worker não configurado. auth.json ou runner.json não encontrados.")
        logger.error("Worker não configurado. auth.json ou runner.json não encontrados.")
        return EXIT_NOT_CONFIGURED

    try:
        access_token = unprotect_text(auth.encrypted_access_token)
    except Exception as exc:
        logger.exception("Não foi possível ler o token salvo (DPAPI): %s", exc)
        print("Não foi possível ler o token salvo. Reregistre o worker nesta máquina.")
        return EXIT_CANNOT_READ_TOKEN

    # Um único client para todas as threads: o token renovado vale para todo mundo.
    client = HttpClient(base_url=auth.base_url)
    client.set_token(access_token)
    client.on_unauthorized = TokenRefresher(auth, client, logger)

    task_api = TaskApiClient(
        client=client,
        runner_uuid=runner.uuid,
        runner_token=runner.runner_token,
    )
    events = EventReporter(task_api, logger)
    log_buffer = WorkerLogBuffer(logger)
    progress = LoopProgress()

    manager = TaskExecutionManager(
        client=client,
        runner=runner,
        logger=logger,
        events=events,
    )

    heartbeat = HeartbeatThread(task_api, runner, manager, logger)
    pulse = PulseThread(progress, logger)
    bot_sync = BotSyncThread(client, runner, log_buffer, events, logger)

    pulse.start()
    heartbeat.start()

    logger.warning(
        "Worker iniciado | versao=%s runner_id=%s %s",
        WORKER_VERSION,
        runner.id,
        describe_session(),
    )

    recover_runner_startup_tasks(task_api, runner, logger)

    # Antes de pegar qualquer task, carrega a lista de bots vinculados (rápido, não instala).
    # Assim uma máquina recém-configurada já sabe quais bots existem; cada task prepara só o seu.
    try:
        summary = sync_bots(client, runner, install=False)
        save_model(RUNNER_FILE, runner)
        logger.warning(
            "Lista de bots carregada | total=%s vinculados=%s",
            summary.get("total"),
            summary.get("linked"),
        )
    except Exception as exc:
        logger.exception("Falha ao carregar a lista de bots na partida: %s", exc)
        events.report(
            RUNTIME_EVENT_TYPE_SYNC_FAILED,
            f"Falha ao carregar a lista de bots na partida: {exc}",
            reason="initial_sync_failed",
        )

    events.report(
        RUNTIME_EVENT_TYPE_WORKER_STARTED,
        f"Worker iniciado (versão {WORKER_VERSION}) | {describe_session()}",
        reason="startup",
        dedupe=False,
    )

    bot_sync.start()

    consecutive_auth_errors = 0

    try:
        while True:
            progress.tick()

            try:
                manager.cleanup_finished()
                _start_available_tasks(task_api, manager, runner, log_buffer, logger)
                consecutive_auth_errors = 0

            except AuthenticationError as exc:
                # O client já tentou renovar o token; se chegou aqui, não conseguiu.
                consecutive_auth_errors += 1
                logger.error(
                    "Falha de autenticação (%s seguidas) e o token não pôde ser renovado: %s",
                    consecutive_auth_errors,
                    exc,
                )
                events.report(
                    RUNTIME_EVENT_TYPE_AUTH_FAILED,
                    f"Token inválido e não renovável: {exc}. Pode ser preciso reregistrar o worker.",
                    reason="token_refresh_failed",
                )
                print(
                    "[WORKER] Token inválido e não foi possível renovar. "
                    "Se persistir, reregistre o worker."
                )
                time.sleep(_AUTH_FAILURE_PAUSE_SECONDS)

            except ForbiddenError as exc:
                # 403 = o backend recusou (runner desativado, não vinculado...).
                # Não é token: segue tentando no ritmo normal, sem refresh.
                logger.warning("Backend recusou a operação do worker: %s", exc)
                events.report(
                    RUNTIME_EVENT_TYPE_FORBIDDEN,
                    f"O backend recusou a requisição do worker: {exc}",
                    reason="forbidden",
                )

            except Exception as exc:
                print(f"[WORKER] erro no ciclo: {exc}")
                logger.exception("Erro no ciclo do worker: %s", exc)

            progress.tick()
            time.sleep(max(1, int(runner.config.polling_interval or 10)))

    except KeyboardInterrupt:
        logger.warning("Worker interrompido pelo usuário (Ctrl+C).")
        return 130
    finally:
        heartbeat.stop()
        pulse.stop()
        bot_sync.stop()


def _start_available_tasks(
    task_api: TaskApiClient,
    manager: TaskExecutionManager,
    runner: RunnerData,
    log_buffer: WorkerLogBuffer,
    logger,
) -> None:
    while True:
        modes = manager.available_modes()

        if not modes:
            return

        next_task = fetch_next_task(task_api, modes)

        if not next_task.get("found"):
            return

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
            return

        try:
            task_api.claim_task(task_id)
        except ForbiddenError as exc:
            logger.warning(
                "Backend recusou o claim da task | task_id=%s motivo=%s",
                task_id,
                exc,
            )
            return
        except AuthenticationError:
            raise
        except Exception as exc:
            logger.warning(
                "Falha ao dar claim na task | task_id=%s erro=%s",
                task_id,
                exc,
            )
            return

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

            return

        print(f"Task iniciada | task_id={task_id} | modo={execution_mode}")
        logger.info(
            "Task enviada para execução local | task_id=%s execution_mode=%s",
            task_id,
            execution_mode,
        )


if __name__ == "__main__":
    sys.exit(main())

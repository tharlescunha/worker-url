from __future__ import annotations

import ctypes
import os
import threading
import time
from datetime import datetime, timezone

from app.core.constants import (
    HEARTBEAT_INTERVAL_SECONDS,
    MAIN_LOOP_STALL_LIMIT_SECONDS,
    PULSE_FILE,
    PULSE_WRITE_INTERVAL_SECONDS,
)
from app.runtime import self_update
from app.runtime.task_client import TaskApiClient

_DESKTOP_SWITCHDESKTOP = 0x0100


def is_user_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_input_desktop_available() -> bool:
    """
    False quando a sessão está bloqueada ou desconectada (o desktop de entrada
    não pode ser aberto). Automação com foco (SAP GUI etc.) não funciona assim.
    """
    try:
        user32 = ctypes.windll.user32
        handle = user32.OpenInputDesktop(0, False, _DESKTOP_SWITCHDESKTOP)
        if not handle:
            return False
        user32.CloseDesktop(handle)
        return True
    except Exception:
        # Não conseguiu checar: não acusa problema.
        return True


def current_os_user() -> str | None:
    """DOMINIO\\usuario do Windows em que o worker está rodando (vai no heartbeat)."""
    user = os.environ.get("USERNAME") or ""
    if not user:
        try:
            import getpass

            user = getpass.getuser()
        except Exception:
            return None
    domain = os.environ.get("USERDOMAIN") or ""
    return f"{domain}\\{user}" if domain else user


def describe_session() -> str:
    return (
        f"usuario={os.environ.get('USERNAME', '?')} "
        f"sessao={os.environ.get('SESSIONNAME', '?')} "
        f"admin={is_user_admin()}"
    )


class LoopProgress:
    """Marca o último ciclo concluído do loop principal."""

    def __init__(self) -> None:
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def tick(self) -> None:
        with self._lock:
            self._last = time.monotonic()

    def seconds_since_last_tick(self) -> float:
        with self._lock:
            return time.monotonic() - self._last


class HeartbeatThread(threading.Thread):
    """
    Heartbeat independente do loop principal: sync de bots, instalação ou
    claim lento não fazem o backend achar que o worker caiu.
    """

    def __init__(self, api: TaskApiClient, runner, manager, logger) -> None:
        super().__init__(daemon=True, name="worker-heartbeat")
        self.api = api
        self.runner = runner
        self.manager = manager
        self.logger = logger
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        self._os_user = current_os_user()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                response = self.api.heartbeat(
                    ip=self.runner.ip,
                    running_tasks=self.manager.active_count(),
                    os_user=self._os_user,
                    worker_commit=self_update.current_commit(),
                    worker_version=self_update.display_version(),
                    update_ack=self_update.handled_id(),
                )
                self_update.on_heartbeat_response(response)
                if self._consecutive_failures:
                    self.logger.info("Heartbeat restabelecido.")
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                # Loga a primeira falha e depois a cada 10, para não encher o log.
                if self._consecutive_failures == 1 or self._consecutive_failures % 10 == 0:
                    self.logger.warning(
                        "Falha ao enviar heartbeat (%s seguidas): %s",
                        self._consecutive_failures,
                        exc,
                    )

            self._stop_event.wait(HEARTBEAT_INTERVAL_SECONDS)


class PulseThread(threading.Thread):
    """
    Grava o arquivo de pulso lido pelo supervisor. O pulso só é atualizado
    enquanto o loop principal estiver progredindo; se o loop travar, o pulso
    para e o supervisor reinicia o worker.
    """

    def __init__(self, progress: LoopProgress, logger) -> None:
        super().__init__(daemon=True, name="worker-pulse")
        self.progress = progress
        self.logger = logger
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            stalled_for = self.progress.seconds_since_last_tick()

            if stalled_for <= MAIN_LOOP_STALL_LIMIT_SECONDS:
                self._write_pulse()
            else:
                self.logger.error(
                    "Loop principal parado há %.0fs; pulso suspenso (o supervisor vai reiniciar o worker).",
                    stalled_for,
                )

            self._stop_event.wait(PULSE_WRITE_INTERVAL_SECONDS)

    def _write_pulse(self) -> None:
        try:
            PULSE_FILE.parent.mkdir(parents=True, exist_ok=True)
            PULSE_FILE.write_text(
                datetime.now(timezone.utc).isoformat(),
                encoding="utf-8",
            )
        except Exception:
            pass

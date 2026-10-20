"""
Supervisor do worker.

Roda `python -m app.runtime.main` como processo filho e o encerra quando:
- o filho termina (qualquer motivo): devolve o controle ao .bat, que atualiza
  o código (git forçado) e sobe tudo de novo;
- o arquivo de pulso para de ser atualizado (worker travado): mata a árvore
  de processos do filho e devolve o controle ao .bat.

Não é serviço do Windows: roda no mesmo terminal/sessão do usuário, o que é
necessário para automações com foco de tela (SAP GUI etc.).

Códigos de saída: 0 = filho terminou, 10 = reiniciado por travamento,
130 = interrompido pelo usuário (Ctrl+C; o .bat não reinicia).
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime

from app.core.constants import (
    PULSE_FILE,
    SUPERVISOR_STALL_LIMIT_SECONDS,
    SUPERVISOR_STARTUP_GRACE_SECONDS,
)

EXIT_STALLED = 10
EXIT_INTERRUPTED = 130
CHECK_INTERVAL_SECONDS = 5
CRASH_LOOP_SECONDS = 30
CRASH_LOOP_EXTRA_WAIT_SECONDS = 50


def _log(message: str) -> None:
    print(f"[SUPERVISOR {datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def _kill_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )


def _pulse_age_seconds(started_at: float) -> float:
    """Idade do último pulso; antes do primeiro pulso conta desde a partida do filho."""
    try:
        mtime = PULSE_FILE.stat().st_mtime
        if mtime >= started_at - 1:
            return max(0.0, time.time() - mtime)
    except OSError:
        pass

    return max(0.0, time.time() - started_at)


def run() -> int:
    try:
        PULSE_FILE.unlink(missing_ok=True)
    except OSError:
        pass

    started_at = time.time()
    _log("Iniciando worker...")

    child = subprocess.Popen(
        [sys.executable, "-m", "app.runtime.main"],
        shell=False,
    )

    try:
        while True:
            code = child.poll()

            if code is not None:
                _log(f"Worker encerrou com código {code}.")

                if code == EXIT_INTERRUPTED:
                    return EXIT_INTERRUPTED

                if time.time() - started_at < CRASH_LOOP_SECONDS:
                    # Caiu logo ao subir: espera mais para não ficar em loop apertado.
                    _log(f"Queda imediata; aguardando {CRASH_LOOP_EXTRA_WAIT_SECONDS}s extras.")
                    time.sleep(CRASH_LOOP_EXTRA_WAIT_SECONDS)

                return 0

            running_for = time.time() - started_at
            limit = (
                SUPERVISOR_STARTUP_GRACE_SECONDS
                if running_for < SUPERVISOR_STARTUP_GRACE_SECONDS
                else SUPERVISOR_STALL_LIMIT_SECONDS
            )

            if _pulse_age_seconds(started_at) > limit and running_for > SUPERVISOR_STARTUP_GRACE_SECONDS:
                _log(
                    f"Worker sem sinal de vida há mais de {limit}s — reiniciando "
                    "(encerrando processo e filhos)."
                )
                _kill_tree(child.pid)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
                return EXIT_STALLED

            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        _log("Interrompido pelo usuário (Ctrl+C). Encerrando worker...")
        try:
            child.wait(timeout=10)
        except Exception:
            _kill_tree(child.pid)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(run())

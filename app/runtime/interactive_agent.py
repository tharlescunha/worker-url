from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime

from app.core.constants import (
    INTERACTIVE_AGENT_LOG_FILE,
    INTERACTIVE_AGENT_STATE_FILE,
)
from app.core.logging_config import setup_logging


def main() -> None:
    os.environ["ORKAFLOW_WORKER_ROLE"] = "interactive_agent_legacy"

    logger = setup_logging()

    print("=== INTERACTIVE AGENT (LEGACY MODE) ===")

    logger.warning(
        "Interactive Agent iniciado em modo LEGADO. "
        "Ele NÃO executa mais tasks foreground. "
        "Use o interactive_worker.py para execução de bots com UI."
    )

    while True:
        try:
            state_payload = {
                "is_active": False,
                "pid": os.getpid(),
                "updated_at": datetime.now(UTC).isoformat(),
                "message": "Interactive Agent desativado (modo legado).",
            }

            INTERACTIVE_AGENT_STATE_FILE.write_text(
                str(state_payload),
                encoding="utf-8",
            )

        except Exception as exc:
            logger.warning(
                "Erro ao atualizar estado do interactive agent legado: %s",
                exc,
            )

        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
        
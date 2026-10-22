import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.constants import (
    LOG_MAX_FILE_BYTES,
    LOG_RETENTION_DAYS,
    LOGS_DIR,
    TMP_DIR,
    TMP_RETENTION_DAYS,
)
from app.core.log_masking import UrlCredentialsFilter


def cleanup_old_files() -> None:
    """
    Limpa logs e temporários antigos. Roda na inicialização do worker.

    - logs rotacionados (worker.log.*) e demais .log/.jsonl com mais de
      LOG_RETENTION_DAYS dias são apagados;
    - arquivo de log atual acima de LOG_MAX_FILE_BYTES é descartado
      (resolve o worker.log gigante herdado de versões sem rotação);
    - temporários de task em TMP_DIR com mais de TMP_RETENTION_DAYS dias.
    """
    now = time.time()

    _delete_older_than(LOGS_DIR, ("*.log", "*.log.*", "*.jsonl"), LOG_RETENTION_DAYS, now)
    _delete_older_than(TMP_DIR, ("task_*",), TMP_RETENTION_DAYS, now)

    if LOGS_DIR.exists():
        for path in LOGS_DIR.glob("*"):
            try:
                if path.is_file() and path.stat().st_size > LOG_MAX_FILE_BYTES:
                    path.unlink()
            except Exception:
                pass


def _delete_older_than(folder: Path, patterns: tuple[str, ...], days: int, now: float) -> None:
    if not folder.exists():
        return

    limit = now - days * 86400

    for pattern in patterns:
        for path in folder.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < limit:
                    path.unlink()
            except Exception:
                pass


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("orkaflow_worker")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        cleanup_old_files()
    except Exception:
        pass

    # Um arquivo por dia, guardando só os últimos LOG_RETENTION_DAYS dias.
    file_handler = TimedRotatingFileHandler(
        LOGS_DIR / "worker.log",
        when="midnight",
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Nada de usuário/token de URL (ex.: clone do Azure DevOps) no arquivo, na tela ou no painel.
    masking = UrlCredentialsFilter()
    console_handler.addFilter(masking)
    file_handler.addFilter(masking)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger

from pathlib import Path

from app.core.constants import (
    APP_DIR,
    BASE_DIR,
    BOTS_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    RUNTIME_DIR,
    TMP_DIR,
    TOOLS_DIR,
    VENVS_DIR,
)


REQUIRED_DIRS = [
    BASE_DIR,
    APP_DIR,
    BOTS_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    RUNTIME_DIR,
    TMP_DIR,
    TOOLS_DIR,
    VENVS_DIR,
]


def create_worker_structure() -> list[Path]:
    created: list[Path] = []

    for path in REQUIRED_DIRS:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
        else:
            path.mkdir(parents=True, exist_ok=True)

    return created


def ensure_base_structure() -> list[Path]:
    return create_worker_structure()


def ensure_tmp_dir() -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def ensure_logs_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def ensure_bots_dir() -> Path:
    BOTS_DIR.mkdir(parents=True, exist_ok=True)
    return BOTS_DIR


def ensure_venvs_dir() -> Path:
    VENVS_DIR.mkdir(parents=True, exist_ok=True)
    return VENVS_DIR

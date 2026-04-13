# app\core\paths.py

"""
Gerenciamento da estrutura de pastas.
As pastas só são criadas depois do cadastro concluído.
"""

from pathlib import Path

from app.core.constants import (
    APP_DIR,
    BASE_DIR,
    BOTS_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    RUNTIME_DIR,
    SERVICES_DIR,
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
    SERVICES_DIR,
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

    return created

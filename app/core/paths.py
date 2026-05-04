from pathlib import Path

from app.core.constants import (
    BASE_DIR,
    APP_DIR,
    BOTS_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    RUNTIME_DIR,
    TMP_DIR,
    TOOLS_DIR,
    VENVS_DIR,
)


def create_worker_structure() -> dict[str, str]:
    """
    Cria toda a estrutura necessária do worker local.

    Isso evita erro de:
    - pasta não encontrada
    - falha ao salvar JSON
    - erro ao criar payload temporário
    """

    paths = [
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

    created: dict[str, str] = {}

    for path in paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
            created[str(path)] = "ok"
        except Exception as exc:
            created[str(path)] = f"erro: {exc}"

    return created


def ensure_tmp_dir() -> Path:
    """
    Garante que a pasta de temporários existe.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def ensure_logs_dir() -> Path:
    """
    Garante que a pasta de logs existe.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def ensure_bots_dir() -> Path:
    """
    Garante que a pasta de bots existe.
    """
    BOTS_DIR.mkdir(parents=True, exist_ok=True)
    return BOTS_DIR


def ensure_venvs_dir() -> Path:
    """
    Garante que a pasta de venvs existe.
    """
    VENVS_DIR.mkdir(parents=True, exist_ok=True)
    return VENVS_DIR

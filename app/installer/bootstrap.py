# app\installer\bootstrap.py

from app.core.logging_config import setup_logging
from app.core.paths import ensure_base_structure
from app.diagnostics.prereq_checks import (
    check_base_dir_write_access,
    check_git_installed,
    check_odbc_driver_hint,
)


def bootstrap_environment() -> dict:
    logger = setup_logging()
    created_paths = ensure_base_structure()

    checks = {
        "git": check_git_installed(),
        "base_dir": check_base_dir_write_access(),
        "odbc": check_odbc_driver_hint(),
    }

    logger.info("Bootstrap do instalador iniciado.")
    logger.info("Pastas criadas/garantidas: %s", [str(path) for path in created_paths])
    logger.info("Pré-checks: %s", checks)

    return {
        "created_paths": [str(path) for path in created_paths],
        "checks": checks,
    }

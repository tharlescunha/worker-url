# app\installer\bootstrap.py

from __future__ import annotations

import shutil
import subprocess

from app.core.logging_config import setup_logging
from app.core.paths import ensure_base_structure
from app.diagnostics.prereq_checks import (
    check_base_dir_write_access,
    check_git_installed,
    check_odbc_driver_hint,
)


def _run_version_check(command: list[str]) -> dict:
    try:
        result = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )

        output = ((result.stdout or "") + " " + (result.stderr or "")).strip()

        return {
            "ok": result.returncode == 0,
            "command": " ".join(command),
            "output": output,
        }
    except Exception as exc:
        return {
            "ok": False,
            "command": " ".join(command),
            "output": str(exc),
        }


def check_python_installed() -> dict:
    candidates: list[list[str]] = []

    if shutil.which("python"):
        candidates.append(["python"])

    if shutil.which("py"):
        candidates.append(["py", "-3.12"])
        candidates.append(["py", "-3"])
        candidates.append(["py"])

    tested: list[dict] = []
    seen: set[tuple[str, ...]] = set()

    for candidate in candidates:
        key = tuple(candidate)
        if key in seen:
            continue
        seen.add(key)

        result = _run_version_check(candidate)
        tested.append(result)

        if result["ok"]:
            return {
                "ok": True,
                "selected_command": result["command"],
                "output": result["output"],
                "tested": tested,
                "message": f"Python disponível via {result['command']}.",
            }

    return {
        "ok": False,
        "selected_command": None,
        "output": None,
        "tested": tested,
        "message": (
            "Python não encontrado. Instale o Python 3.12+ e garanta que "
            "'python' ou 'py' funcione no terminal."
        ),
    }


def bootstrap_environment() -> dict:
    logger = setup_logging()
    created_paths = ensure_base_structure()

    checks = {
        "git": check_git_installed(),
        "python": check_python_installed(),
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

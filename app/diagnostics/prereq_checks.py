# app\diagnostics\prereq_checks.py

"""
Verificações de pré-requisito do assistente.

Agora inclui:
- Python
- Git
- ambiente ODBC
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def check_python_installed() -> tuple[bool, str]:
    """
    Valida se o Python atual existe e é executável.
    """
    python_exe = sys.executable

    if python_exe and Path(python_exe).exists():
        version = sys.version.split()[0]
        return True, f"Python encontrado: {python_exe} | versão {version}"

    return False, "Python não encontrado."


def check_git_installed() -> tuple[bool, str]:
    git_path = shutil.which("git")
    if git_path:
        return True, f"Git encontrado em: {git_path}"
    return False, "Git não encontrado no PATH."


def check_odbc_environment() -> tuple[bool, str]:
    candidates = [
        Path(r"C:\Windows\System32\odbcad32.exe"),
        Path(r"C:\Windows\SysWOW64\odbcad32.exe"),
    ]

    if any(path.exists() for path in candidates):
        return True, "Ambiente ODBC detectado no Windows."

    return False, "Ambiente ODBC não encontrado."


def run_prerequisite_checks() -> dict[str, tuple[bool, str]]:
    return {
        "python": check_python_installed(),
        "git": check_git_installed(),
        "driver": check_odbc_environment(),
    }


def check_nssm():
    nssm_path = Path("C:/OrkaFlow/tools/nssm.exe")

    if nssm_path.exists():
        return True, str(nssm_path)
    else:
        return False, "NSSM não encontrado em C:/OrkaFlow/tools/nssm.exe"
    
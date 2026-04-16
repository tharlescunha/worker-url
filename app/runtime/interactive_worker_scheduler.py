from __future__ import annotations

import getpass
import subprocess

from app.core.constants import (
    DIAGNOSTIC_INTERACTIVE_WORKER_BAT,
    INSTALL_INTERACTIVE_WORKER_BAT,
    INTERACTIVE_AGENT_TASK_NAME,
    INTERACTIVE_WORKER_SCRIPT,
    INTERACTIVE_WORKER_TASK_NAME,
    INTERACTIVE_WORKER_VBS,
    REMOVE_INTERACTIVE_WORKER_BAT,
    RUN_INTERACTIVE_WORKER_BAT,
)
from app.installer.runtime_setup import get_worker_runtime_dir, get_worker_runtime_venv_dir


def generate_interactive_worker_files() -> dict[str, str]:
    runtime_dir = get_worker_runtime_dir()
    runtime_venv_dir = get_worker_runtime_venv_dir()

    python_executable = runtime_venv_dir / "Scripts" / "python.exe"

    if not python_executable.exists():
        raise RuntimeError(
            f"Python do worker-runtime não encontrado em {python_executable}"
        )

    INTERACTIVE_WORKER_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_INTERACTIVE_WORKER_BAT.parent.mkdir(parents=True, exist_ok=True)

    script_content = """from app.runtime.interactive_worker import main

if __name__ == "__main__":
    main()
"""

    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{str(runtime_dir)}"
WshShell.Run chr(34) & "{str(python_executable)}" & chr(34) & " " & chr(34) & "{str(INTERACTIVE_WORKER_SCRIPT)}" & chr(34), 0, False
'''

    run_bat_content = f'''@echo off
setlocal

set PYTHON_EXE={str(python_executable)}
set WORKER_SCRIPT={str(INTERACTIVE_WORKER_SCRIPT)}
set WORK_DIR={str(runtime_dir)}

if not exist "%PYTHON_EXE%" (
    echo Python nao encontrado em %PYTHON_EXE%
    exit /b 1
)

if not exist "%WORKER_SCRIPT%" (
    echo Script do interactive worker nao encontrado em %WORKER_SCRIPT%
    exit /b 1
)

if not exist "%WORK_DIR%" (
    echo Pasta do runtime nao encontrada em %WORK_DIR%
    exit /b 1
)

cd /d "%WORK_DIR%"
"%PYTHON_EXE%" "%WORKER_SCRIPT%"
'''

    current_user = getpass.getuser()

    install_bat_content = f'''@echo off
setlocal

set TASK_NAME={INTERACTIVE_WORKER_TASK_NAME}
set VBS_PATH={str(INTERACTIVE_WORKER_VBS)}
set CURRENT_USER={current_user}

if not exist "%VBS_PATH%" (
    echo Arquivo VBS do interactive worker nao encontrado em %VBS_PATH%
    exit /b 1
)

schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if %errorlevel%==0 (
    schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1
)

schtasks /Create ^
 /SC ONLOGON ^
 /TN "%TASK_NAME%" ^
 /TR "wscript.exe ""%VBS_PATH%""" ^
 /RL HIGHEST ^
 /IT ^
 /F ^
 /RU "%CURRENT_USER%"

if errorlevel 1 exit /b 1

exit /b 0
'''

    remove_bat_content = f'''@echo off
setlocal

set TASK_NAME={INTERACTIVE_WORKER_TASK_NAME}
schtasks /Delete /TN "%TASK_NAME%" /F
'''

    diagnostic_bat_content = f'''@echo off
echo === TASK ===
schtasks /Query /TN "{INTERACTIVE_WORKER_TASK_NAME}" /V /FO LIST
echo.
echo === QUERY SESSION ===
query session
echo.
echo === PYTHON ===
"{str(python_executable)}" --version
echo.
echo === WORKER SCRIPT ===
echo {str(INTERACTIVE_WORKER_SCRIPT)}
echo.
echo === VBS ===
echo {str(INTERACTIVE_WORKER_VBS)}
echo.
echo === LEGACY TASK (SE EXISTIR) ===
schtasks /Query /TN "{INTERACTIVE_AGENT_TASK_NAME}" /V /FO LIST
'''

    INTERACTIVE_WORKER_SCRIPT.write_text(script_content, encoding="utf-8")
    INTERACTIVE_WORKER_VBS.write_text(vbs_content, encoding="utf-8")
    RUN_INTERACTIVE_WORKER_BAT.write_text(run_bat_content, encoding="utf-8")
    INSTALL_INTERACTIVE_WORKER_BAT.write_text(install_bat_content, encoding="utf-8")
    REMOVE_INTERACTIVE_WORKER_BAT.write_text(remove_bat_content, encoding="utf-8")
    DIAGNOSTIC_INTERACTIVE_WORKER_BAT.write_text(diagnostic_bat_content, encoding="utf-8")

    return {
        "interactive_worker_script": str(INTERACTIVE_WORKER_SCRIPT),
        "interactive_worker_vbs": str(INTERACTIVE_WORKER_VBS),
        "run_interactive_worker_bat": str(RUN_INTERACTIVE_WORKER_BAT),
        "install_interactive_worker_bat": str(INSTALL_INTERACTIVE_WORKER_BAT),
        "remove_interactive_worker_bat": str(REMOVE_INTERACTIVE_WORKER_BAT),
        "diagnostic_interactive_worker_bat": str(DIAGNOSTIC_INTERACTIVE_WORKER_BAT),
        "interactive_worker_task_name": INTERACTIVE_WORKER_TASK_NAME,
        "legacy_interactive_agent_task_name": INTERACTIVE_AGENT_TASK_NAME,
    }


def install_interactive_worker_task() -> tuple[bool, str]:
    if not INSTALL_INTERACTIVE_WORKER_BAT.exists():
        return False, f"Arquivo não encontrado: {INSTALL_INTERACTIVE_WORKER_BAT}"

    result = subprocess.run(
        ["cmd", "/c", str(INSTALL_INTERACTIVE_WORKER_BAT)],
        capture_output=True,
        text=True,
        shell=False,
    )

    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0, output


def remove_interactive_worker_task() -> tuple[bool, str]:
    if not REMOVE_INTERACTIVE_WORKER_BAT.exists():
        return False, f"Arquivo não encontrado: {REMOVE_INTERACTIVE_WORKER_BAT}"

    result = subprocess.run(
        ["cmd", "/c", str(REMOVE_INTERACTIVE_WORKER_BAT)],
        capture_output=True,
        text=True,
        shell=False,
    )

    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0, output


def run_interactive_worker_now() -> tuple[bool, str]:
    if not RUN_INTERACTIVE_WORKER_BAT.exists():
        return False, f"Arquivo não encontrado: {RUN_INTERACTIVE_WORKER_BAT}"

    result = subprocess.run(
        ["cmd", "/c", str(RUN_INTERACTIVE_WORKER_BAT)],
        capture_output=True,
        text=True,
        shell=False,
    )

    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0, output


def get_interactive_worker_task_status() -> tuple[bool, str]:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", INTERACTIVE_WORKER_TASK_NAME, "/V", "/FO", "LIST"],
        capture_output=True,
        text=True,
        shell=False,
    )

    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0, output

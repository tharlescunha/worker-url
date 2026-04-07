# app\service\service_files.py

"""
Geração dos arquivos do serviço Windows.

Agora também gera:
- install_worker_service.bat
- start_worker.bat
- stop_worker.bat
- restart_worker.bat
- diagnostic_worker.bat

Ajuste importante:
- o instalador tenta usar o Python atual
- se existir PYTHON_HOME, usa esse valor
- configura o serviço para iniciar automaticamente
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.config_models import WorkerServiceConfig
from app.core.constants import (
    AUTH_FILE,
    DIAGNOSTIC_SERVICE_BAT,
    INSTALL_SERVICE_BAT,
    LOGS_DIR,
    RESTART_SERVICE_BAT,
    RUNNER_FILE,
    SERVICE_CONFIG_FILE,
    SERVICE_DESCRIPTION,
    SERVICE_DISPLAY_NAME,
    SERVICE_NAME,
    START_SERVICE_BAT,
    STOP_SERVICE_BAT,
)
from app.core.json_store import save_model


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_python_executable() -> str:
    env_python = os.environ.get("PYTHON_HOME", "").strip()
    if env_python:
        return env_python
    return sys.executable


def generate_service_files() -> dict[str, str]:
    project_root = _project_root()
    python_executable = _resolve_python_executable()
    working_directory = str(project_root)
    runtime_module = "app.runtime.main"

    service_config = WorkerServiceConfig(
        service_name=SERVICE_NAME,
        display_name=SERVICE_DISPLAY_NAME,
        description=SERVICE_DESCRIPTION,
        project_root=str(project_root),
        working_directory=working_directory,
        python_executable=python_executable,
        runtime_module=runtime_module,
        auth_file=str(AUTH_FILE),
        runner_file=str(RUNNER_FILE),
        logs_dir=str(LOGS_DIR),
        command=python_executable,
        command_args=f"-m {runtime_module}",
        install_hint="Necessário NSSM em C:\\OrkaFlow\\tools\\nssm.exe",
        created_at=datetime.now(timezone.utc),
    )

    save_model(SERVICE_CONFIG_FILE, service_config)

    install_content = f"""@echo off
setlocal

set NSSM_EXE=C:\\OrkaFlow\\tools\\nssm.exe
set SERVICE_NAME={SERVICE_NAME}
set PYTHON_EXE={python_executable}
set WORK_DIR={working_directory}
set APP_ARGS=-m {runtime_module}

if not exist "%NSSM_EXE%" (
    echo NSSM nao encontrado em %NSSM_EXE%
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo Python nao encontrado em %PYTHON_EXE%
    exit /b 1
)

sc query "%SERVICE_NAME%" >nul 2>&1
if %errorlevel%==0 (
    echo Servico ja existe. Removendo configuracao anterior...
    "%NSSM_EXE%" stop "%SERVICE_NAME%" >nul 2>&1
    "%NSSM_EXE%" remove "%SERVICE_NAME%" confirm >nul 2>&1
)

"%NSSM_EXE%" install "%SERVICE_NAME%" "%PYTHON_EXE%" %APP_ARGS%
if errorlevel 1 exit /b 1

"%NSSM_EXE%" set "%SERVICE_NAME%" AppDirectory "%WORK_DIR%"
"%NSSM_EXE%" set "%SERVICE_NAME%" DisplayName "{SERVICE_DISPLAY_NAME}"
"%NSSM_EXE%" set "%SERVICE_NAME%" Description "{SERVICE_DESCRIPTION}"
"%NSSM_EXE%" set "%SERVICE_NAME%" Start SERVICE_AUTO_START
"%NSSM_EXE%" set "%SERVICE_NAME%" AppStdout "C:\\OrkaFlow\\logs\\service_stdout.log"
"%NSSM_EXE%" set "%SERVICE_NAME%" AppStderr "C:\\OrkaFlow\\logs\\service_stderr.log"
"%NSSM_EXE%" set "%SERVICE_NAME%" AppRotateFiles 1
"%NSSM_EXE%" set "%SERVICE_NAME%" AppRotateOnline 1
"%NSSM_EXE%" set "%SERVICE_NAME%" AppRotateBytes 1048576

exit /b 0
"""

    start_content = f"""@echo off
sc start "{SERVICE_NAME}"
"""

    stop_content = f"""@echo off
sc stop "{SERVICE_NAME}"
"""

    restart_content = f"""@echo off
sc stop "{SERVICE_NAME}"
timeout /t 2 >nul
sc start "{SERVICE_NAME}"
"""

    diagnostic_content = f"""@echo off
echo === SERVICE QUERY ===
sc query "{SERVICE_NAME}"
echo.
echo === PYTHON ===
"{python_executable}" --version
echo.
echo === RUNTIME TEST ===
"{python_executable}" -m {runtime_module}
"""

    INSTALL_SERVICE_BAT.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_SERVICE_BAT.write_text(install_content, encoding="utf-8")
    START_SERVICE_BAT.write_text(start_content, encoding="utf-8")
    STOP_SERVICE_BAT.write_text(stop_content, encoding="utf-8")
    RESTART_SERVICE_BAT.write_text(restart_content, encoding="utf-8")
    DIAGNOSTIC_SERVICE_BAT.write_text(diagnostic_content, encoding="utf-8")

    return {
        "service_config_file": str(SERVICE_CONFIG_FILE),
        "install_service_bat": str(INSTALL_SERVICE_BAT),
        "start_service_bat": str(START_SERVICE_BAT),
        "stop_service_bat": str(STOP_SERVICE_BAT),
        "restart_service_bat": str(RESTART_SERVICE_BAT),
        "diagnostic_service_bat": str(DIAGNOSTIC_SERVICE_BAT),
    }

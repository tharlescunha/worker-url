from __future__ import annotations

from datetime import datetime, timezone

from app.core.config_models import WorkerServiceConfig
from app.core.constants import (
    AUTH_FILE,
    DIAGNOSTIC_INTERACTIVE_WORKER_BAT,
    DIAGNOSTIC_SERVICE_BAT,
    INSTALL_INTERACTIVE_WORKER_BAT,
    INSTALL_SERVICE_BAT,
    LOGS_DIR,
    REMOVE_INTERACTIVE_WORKER_BAT,
    RESTART_SERVICE_BAT,
    RUNNER_FILE,
    RUN_INTERACTIVE_WORKER_BAT,
    SERVICE_CONFIG_FILE,
    SERVICE_DESCRIPTION,
    SERVICE_DISPLAY_NAME,
    SERVICE_NAME,
    START_SERVICE_BAT,
    STOP_SERVICE_BAT,
)
from app.core.json_store import save_model
from app.installer.runtime_setup import (
    WORKER_RUNTIME_BRANCH,
    get_worker_runtime_dir,
    get_worker_runtime_venv_dir,
)
from app.runtime.interactive_worker_scheduler import generate_interactive_worker_files


def generate_service_files() -> dict[str, str]:
    runtime_dir = get_worker_runtime_dir()
    runtime_venv_dir = get_worker_runtime_venv_dir()

    python_executable = str(runtime_venv_dir / "Scripts" / "python.exe")
    working_directory = str(runtime_dir)
    runtime_module = "app.runtime.main"
    runtime_branch = WORKER_RUNTIME_BRANCH.strip() or "main"

    service_config = WorkerServiceConfig(
        service_name=SERVICE_NAME,
        display_name=SERVICE_DISPLAY_NAME,
        description=SERVICE_DESCRIPTION,
        project_root=working_directory,
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
    echo Python do runtime nao encontrado em %PYTHON_EXE%
    exit /b 1
)

if not exist "%WORK_DIR%" (
    echo Pasta do runtime nao encontrada em %WORK_DIR%
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
setlocal

set WORK_DIR={working_directory}
set PYTHON_EXE={python_executable}
set SERVICE_NAME={SERVICE_NAME}
set RUNTIME_BRANCH={runtime_branch}

if not exist "%WORK_DIR%" (
    echo Pasta do runtime nao encontrada em %WORK_DIR%
    exit /b 1
)

cd /d "%WORK_DIR%"

git fetch --all --tags --prune
if errorlevel 1 exit /b 1

git checkout --force "%RUNTIME_BRANCH%"
if errorlevel 1 exit /b 1

git reset --hard origin/%RUNTIME_BRANCH%
if errorlevel 1 exit /b 1

git clean -fd
if errorlevel 1 exit /b 1

if not exist "%PYTHON_EXE%" (
    echo Python da venv do runtime nao encontrado em %PYTHON_EXE%
    exit /b 1
)

"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

if exist "requirements.txt" (
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 exit /b 1
)

sc start "%SERVICE_NAME%"
"""

    stop_content = f"""@echo off
sc stop "{SERVICE_NAME}"
"""

    restart_content = f"""@echo off
setlocal

sc stop "{SERVICE_NAME}"
timeout /t 2 >nul

set WORK_DIR={working_directory}
set PYTHON_EXE={python_executable}
set SERVICE_NAME={SERVICE_NAME}
set RUNTIME_BRANCH={runtime_branch}

if not exist "%WORK_DIR%" (
    echo Pasta do runtime nao encontrada em %WORK_DIR%
    exit /b 1
)

cd /d "%WORK_DIR%"

git fetch --all --tags --prune
if errorlevel 1 exit /b 1

git checkout --force "%RUNTIME_BRANCH%"
if errorlevel 1 exit /b 1

git reset --hard origin/%RUNTIME_BRANCH%
if errorlevel 1 exit /b 1

git clean -fd
if errorlevel 1 exit /b 1

if not exist "%PYTHON_EXE%" (
    echo Python da venv do runtime nao encontrado em %PYTHON_EXE%
    exit /b 1
)

"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

if exist "requirements.txt" (
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 exit /b 1
)

sc start "%SERVICE_NAME%"
"""

    diagnostic_content = f"""@echo off
echo === SERVICE QUERY ===
sc query "{SERVICE_NAME}"
echo.
echo === WORK DIR ===
echo {working_directory}
echo.
echo === PYTHON ===
"{python_executable}" --version
echo.
echo === RUNTIME TEST ===
cd /d "{working_directory}"
"{python_executable}" -m {runtime_module}
"""

    INSTALL_SERVICE_BAT.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_SERVICE_BAT.write_text(install_content, encoding="utf-8")
    START_SERVICE_BAT.write_text(start_content, encoding="utf-8")
    STOP_SERVICE_BAT.write_text(stop_content, encoding="utf-8")
    RESTART_SERVICE_BAT.write_text(restart_content, encoding="utf-8")
    DIAGNOSTIC_SERVICE_BAT.write_text(diagnostic_content, encoding="utf-8")

    interactive_files = generate_interactive_worker_files()

    install_interactive_worker_content = f"""@echo off
setlocal

echo === Instalando tarefa agendada do interactive worker ===
cmd /c "{interactive_files['install_interactive_worker_bat']}"
exit /b %errorlevel%
"""

    remove_interactive_worker_content = f"""@echo off
setlocal

echo === Removendo tarefa agendada do interactive worker ===
cmd /c "{interactive_files['remove_interactive_worker_bat']}"
exit /b %errorlevel%
"""

    run_interactive_worker_content = f"""@echo off
setlocal

echo === Executando interactive worker manualmente ===
cmd /c "{interactive_files['run_interactive_worker_bat']}"
exit /b %errorlevel%
"""

    diagnostic_interactive_worker_content = f"""@echo off
setlocal

echo === DIAGNOSTICO DO INTERACTIVE WORKER ===
cmd /c "{interactive_files['diagnostic_interactive_worker_bat']}"
echo.
echo === ARQUIVOS GERADOS ===
echo script: {interactive_files.get('interactive_worker_script', '-')}
echo vbs: {interactive_files.get('interactive_worker_vbs', '-')}
echo run_bat: {interactive_files.get('run_interactive_worker_bat', '-')}
echo install_bat: {interactive_files.get('install_interactive_worker_bat', '-')}
echo remove_bat: {interactive_files.get('remove_interactive_worker_bat', '-')}
"""

    INSTALL_INTERACTIVE_WORKER_BAT.write_text(
        install_interactive_worker_content,
        encoding="utf-8",
    )
    REMOVE_INTERACTIVE_WORKER_BAT.write_text(
        remove_interactive_worker_content,
        encoding="utf-8",
    )
    RUN_INTERACTIVE_WORKER_BAT.write_text(
        run_interactive_worker_content,
        encoding="utf-8",
    )
    DIAGNOSTIC_INTERACTIVE_WORKER_BAT.write_text(
        diagnostic_interactive_worker_content,
        encoding="utf-8",
    )

    return {
        "service_config_file": str(SERVICE_CONFIG_FILE),
        "install_service_bat": str(INSTALL_SERVICE_BAT),
        "start_service_bat": str(START_SERVICE_BAT),
        "stop_service_bat": str(STOP_SERVICE_BAT),
        "restart_service_bat": str(RESTART_SERVICE_BAT),
        "diagnostic_service_bat": str(DIAGNOSTIC_SERVICE_BAT),
        "interactive_worker_script": interactive_files.get("interactive_worker_script", ""),
        "interactive_worker_vbs": interactive_files.get("interactive_worker_vbs", ""),
        "install_interactive_worker_bat": str(INSTALL_INTERACTIVE_WORKER_BAT),
        "remove_interactive_worker_bat": str(REMOVE_INTERACTIVE_WORKER_BAT),
        "run_interactive_worker_bat": str(RUN_INTERACTIVE_WORKER_BAT),
        "diagnostic_interactive_worker_bat": str(DIAGNOSTIC_INTERACTIVE_WORKER_BAT),
    }

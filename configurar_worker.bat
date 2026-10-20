@echo off
setlocal EnableExtensions
title OrkaFlow - Configurar Worker

rem =====================================================================
rem  Configura uma maquina para rodar o OrkaFlow Worker.
rem
rem  Como usar:
rem    1) Baixe este repositorio em C:\OrkaFlow\bots\worker-runtime
rem       git clone https://github.com/tharlescunha/worker-url.git C:\OrkaFlow\bots\worker-runtime
rem    2) De dois cliques neste arquivo (NAO precisa ser administrador).
rem    3) Preencha a telinha e clique em Configurar.
rem
rem  Requisitos: Python 3.12 ou superior (com tcl/tk) e Git no PATH.
rem =====================================================================

cd /d "%~dp0"

set "BASE=C:\OrkaFlow"
set "VENV=%BASE%\venvs\worker-runtime"
set "PY=%VENV%\Scripts\python.exe"
set "PYEXE="

where py >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"

if not defined PYEXE (
    where python >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    echo [ERRO] Python nao encontrado. Instale o Python 3.12 ou superior e marque "Add to PATH".
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Git nao encontrado. Instale o Git for Windows.
    pause
    exit /b 1
)

echo ========================================
echo   ORKAFLOW - CONFIGURAR WORKER
echo ========================================
echo.

if not exist "%BASE%" mkdir "%BASE%"
if not exist "%BASE%\venvs" mkdir "%BASE%\venvs"
if not exist "%BASE%\logs" mkdir "%BASE%\logs"

if not exist "%PY%" (
    echo [1/3] Criando ambiente Python em %VENV% ...
    %PYEXE% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERRO] Nao foi possivel criar o ambiente Python.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Ambiente Python ja existe.
)

echo [2/3] Instalando dependencias ^(pode demorar na primeira vez^) ...
"%PY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias. Verifique a internet e tente de novo.
    pause
    exit /b 1
)

"%PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [ERRO] O Python instalado nao tem o tkinter ^(tcl/tk^). Reinstale o Python marcando "tcl/tk and IDLE".
    pause
    exit /b 1
)

echo [3/3] Abrindo a tela de configuracao...
"%PY%" -m app.installer.setup_window
if errorlevel 1 (
    echo.
    echo [ERRO] A tela de configuracao terminou com erro. Veja %BASE%\logs\setup.log
    pause
    exit /b 1
)

endlocal

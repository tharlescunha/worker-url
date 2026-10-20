@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem =====================================================================
rem  OrkaFlow Worker - inicializador
rem
rem  - Antes de cada (re)inicio faz git fetch + reset --hard + clean:
rem    o worker SEMPRE sobe na versao do repositorio, mesmo com arquivos
rem    locais alterados.
rem  - Se o worker cair, travar ou der qualquer erro, reinicia sozinho.
rem  - Nao e servico do Windows: roda na sessao do usuario (necessario
rem    para automacoes com foco de tela, como SAP GUI).
rem  - Para parar de vez: Ctrl+C (e responda S) ou feche esta janela.
rem =====================================================================

rem O git reset pode reescrever este arquivo enquanto o loop roda, e o cmd
rem le o .bat durante a execucao. Por isso roda uma copia temporaria.
if /i not "%~1"=="--child" (
    set "ORKA_COPY=%TEMP%\orkaflow_launcher_%RANDOM%%RANDOM%.bat"
    copy /y "%~f0" "!ORKA_COPY!" >nul
    call "!ORKA_COPY!" --child
    set "ORKA_EXIT=!errorlevel!"
    del "!ORKA_COPY!" >nul 2>&1
    exit /b !ORKA_EXIT!
)

title OrkaFlow Worker

set "BASE=C:\OrkaFlow"
set "RUNTIME_REPO=%BASE%\bots\worker-runtime"
set "PY=%BASE%\venvs\worker-runtime\Scripts\python.exe"
set "PIP=%BASE%\venvs\worker-runtime\Scripts\pip.exe"
set "LOGDIR=%BASE%\logs"
set "LAUNCHER_LOG=%LOGDIR%\launcher.log"
set "REQ_STAMP=%BASE%\runtime\requirements.installed"
set "BRANCH=main"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "%BASE%\runtime" mkdir "%BASE%\runtime"

rem limpa copias deste launcher com mais de 1 dia (sobras de quedas); nunca a copia em uso
forfiles /p "%TEMP%" /m "orkaflow_launcher_*.bat" /d -1 /c "cmd /c del @path" >nul 2>&1

echo ========================================
echo          ORKAFLOW WORKER
echo ========================================
echo.
echo Deixe esta janela aberta.
echo Reinicia sozinho em caso de erro. Ctrl+C para parar.
echo.

:loop
call :rotate_log
call :log "=== Iniciando ciclo do worker ==="

if not exist "%RUNTIME_REPO%\.git" (
    call :log "ERRO: repositorio do worker nao encontrado em %RUNTIME_REPO%. Nova tentativa em 30s."
    ping -n 31 127.0.0.1 >nul
    goto loop
)

if not exist "%PY%" (
    call :log "ERRO: python da venv nao encontrado em %PY%. Nova tentativa em 30s."
    ping -n 31 127.0.0.1 >nul
    goto loop
)

cd /d "%RUNTIME_REPO%"

call :update_code
call :install_requirements

"%PY%" -m app.runtime.supervisor
set "EXITCODE=!errorlevel!"

call :log "Worker encerrou com codigo !EXITCODE!."

if "!EXITCODE!"=="130" (
    call :log "Interrompido pelo usuario. Encerrando."
    exit /b 130
)

echo.
echo [LAUNCHER] Reiniciando em 10 segundos. Ctrl+C para parar.
ping -n 11 127.0.0.1 >nul
goto loop


:update_code
git fetch --all --prune >>"%LAUNCHER_LOG%" 2>&1
if errorlevel 1 (
    call :log "AVISO: git fetch falhou - possivel falha de rede. Mantendo a versao local."
    exit /b 0
)

git checkout --force %BRANCH% >>"%LAUNCHER_LOG%" 2>&1
git reset --hard origin/%BRANCH% >>"%LAUNCHER_LOG%" 2>&1
if errorlevel 1 (
    call :log "ERRO: git reset --hard falhou. Mantendo a versao local."
    exit /b 0
)

git clean -fd >>"%LAUNCHER_LOG%" 2>&1

for /f %%i in ('git rev-parse --short HEAD') do call :log "Codigo atualizado. Versao %%i"
exit /b 0


:install_requirements
if not exist requirements.txt exit /b 0

fc /b requirements.txt "%REQ_STAMP%" >nul 2>&1
if not errorlevel 1 exit /b 0

call :log "requirements.txt mudou. Instalando dependencias..."
"%PIP%" install -r requirements.txt --quiet >>"%LAUNCHER_LOG%" 2>&1
if errorlevel 1 (
    call :log "AVISO: pip install falhou. Seguindo com o ambiente atual."
) else (
    copy /y requirements.txt "%REQ_STAMP%" >nul
)
exit /b 0


:rotate_log
if not exist "%LAUNCHER_LOG%" exit /b 0
for %%F in ("%LAUNCHER_LOG%") do (
    if %%~zF gtr 1048576 move /y "%LAUNCHER_LOG%" "%LAUNCHER_LOG%.old" >nul
)
exit /b 0


:log
echo [LAUNCHER %DATE% %TIME%] %~1
>>"%LAUNCHER_LOG%" echo [%DATE% %TIME%] %~1
exit /b 0

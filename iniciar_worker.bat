@echo off
title OrkaFlow Worker
cd /d "C:\OrkaFlow\bots\worker-runtime"

echo ========================================
echo          ORKAFLOW WORKER
echo ========================================
echo.

echo [1/3] Verificando atualizacoes do worker...
git pull origin main
if %errorlevel% neq 0 (
    echo.
    echo AVISO: git pull falhou. Continuando com a versao atual.
    echo.
)

echo.
echo [2/3] Atualizando dependencias...
"C:\OrkaFlow\venvs\worker-runtime\Scripts\pip.exe" install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo AVISO: pip install falhou. Continuando assim mesmo.
)

echo.
echo [3/3] Iniciando worker...
echo Deixe esta janela aberta.
echo Pressione CTRL+C para parar.
echo.

"C:\OrkaFlow\venvs\worker-runtime\Scripts\python.exe" -m app.runtime.main

echo.
echo Worker finalizado.
pause

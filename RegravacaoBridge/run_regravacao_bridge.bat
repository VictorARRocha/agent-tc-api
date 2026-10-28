@echo off
setlocal
cd /d "%~dp0"
set "PY_CMD=python"

if not exist logs mkdir logs

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

REM Execucao manual (para testes). Em producao use instalar_tarefa_regravacao.ps1.
REM O Bridge grava seu proprio log em logs\regravacao_bridge.log.
%PY_CMD% regravacao_bridge.py >> "logs\regravacao_bridge_console.log" 2>&1

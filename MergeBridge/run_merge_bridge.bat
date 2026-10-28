@echo off
setlocal
cd /d "%~dp0"
set "PY_CMD=python"

if not exist logs mkdir logs

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

REM Execucao manual (para testes). Em producao use instalar_tarefa_merge.ps1.
REM O Bridge grava seu proprio log em logs\merge_bridge.log.
%PY_CMD% merge_bridge.py >> "logs\merge_bridge_console.log" 2>&1

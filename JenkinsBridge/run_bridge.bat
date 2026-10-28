@echo off
setlocal
cd /d "%~dp0"
set "PY_CMD=python"

if not exist logs mkdir logs

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

REM O Bridge grava seu proprio log rotativo em logs\jenkins_bridge.log.
REM Aqui ficam apenas saidas inesperadas do Python (ex.: traceback ao iniciar).
%PY_CMD% jenkins_bridge.py >> "logs\jenkins_bridge_console.log" 2>&1

@echo off
setlocal
cd /d "%~dp0"
set "PY_CMD=python"

if not exist logs mkdir logs

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

%PY_CMD% jenkins_bridge.py >> "logs\jenkins_bridge.log" 2>&1

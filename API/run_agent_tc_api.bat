@echo off
setlocal
chcp 65001 > nul

set "BASE_DIR=%~dp0"
set "BASE_DIR=%BASE_DIR:~0,-1%"
for %%I in ("%BASE_DIR%\..") do set "PROJECT_ROOT=%%~fI"
set "RUNTIME_DIR=%PROJECT_ROOT%\PythonRodagem"
set "LOG_DIR=%BASE_DIR%\logs"
set "ENV_FILE=%BASE_DIR%\.env"
if not exist "%ENV_FILE%" set "ENV_FILE=%PROJECT_ROOT%\.env"
if not exist "%ENV_FILE%" set "ENV_FILE=%RUNTIME_DIR%\.env"

if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="AGENT_TC_API_HOST" if "%AGENT_TC_API_HOST%"=="" set "AGENT_TC_API_HOST=%%B"
        if /I "%%A"=="AGENT_TC_API_PORT" if "%AGENT_TC_API_PORT%"=="" set "AGENT_TC_API_PORT=%%B"
        if /I "%%A"=="AGENT_TC_BACKEND" if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=%%B"
    )
)

if "%AGENT_TC_API_HOST%"=="" set "AGENT_TC_API_HOST=0.0.0.0"
if "%AGENT_TC_API_PORT%"=="" set "AGENT_TC_API_PORT=8000"
if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=postgres"
set "PY_CMD=python"

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if not exist "%RUNTIME_DIR%\cli\agent_tc_api.py" (
    echo ERRO: PythonRodagem\cli\agent_tc_api.py nao encontrado em %PROJECT_ROOT%
    exit /b 1
)

if not exist "%ENV_FILE%" (
    echo ERRO: .env nao encontrado em API, raiz ou PythonRodagem.
    exit /b 1
)

cd /d "%RUNTIME_DIR%"

%PY_CMD% "%RUNTIME_DIR%\cli\agent_tc_api.py" ^
  --backend "%AGENT_TC_BACKEND%" ^
  --env "%ENV_FILE%" ^
  --host "%AGENT_TC_API_HOST%" ^
  --port "%AGENT_TC_API_PORT%" >> "%LOG_DIR%\agent_tc_api.log" 2>&1

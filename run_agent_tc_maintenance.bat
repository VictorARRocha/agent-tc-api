@echo off
setlocal

set "BASE_DIR=%~dp0"
set "BASE_DIR=%BASE_DIR:~0,-1%"
set "ENV_FILE=%BASE_DIR%\.env"
set "LOG_DIR=S:\Teste automatico\Arquivos\AgenteTC\logs"
set "PY_CMD=python"
set "RETENTION_DAYS=30"

if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="AGENT_TC_LOG_DIR" if "%AGENT_TC_LOG_DIR%"=="" set "AGENT_TC_LOG_DIR=%%B"
        if /I "%%A"=="AGENT_TC_BACKEND" if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=%%B"
        if /I "%%A"=="AGENT_TC_RETENTION_DAYS" set "RETENTION_DAYS=%%B"
    )
)

if not "%AGENT_TC_LOG_DIR%"=="" set "LOG_DIR=%AGENT_TC_LOG_DIR%"
if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=postgres"
set "BACKEND=%AGENT_TC_BACKEND%"

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul

for /f "tokens=1-4 delims=/ " %%a in ("%date%") do set "DATA_LOG=%%d%%b%%c"
for /f "tokens=1-3 delims=:, " %%a in ("%time%") do set "HORA_LOG=%%a%%b%%c"
set "HORA_LOG=%HORA_LOG: =0%"
set "LOG_FILE=%LOG_DIR%\agent_tc_maintenance_%DATA_LOG%_%HORA_LOG%.log"

echo ======================================== > "%LOG_FILE%"
echo Agent TC - manutencao diaria >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"
echo Base Agent TC: %BASE_DIR% >> "%LOG_FILE%"
echo Backend: %BACKEND% >> "%LOG_FILE%"
echo Retencao: %RETENTION_DAYS% dias >> "%LOG_FILE%"
echo Env: %ENV_FILE% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

%PY_CMD% "%BASE_DIR%\cli\agent_tc_maintenance.py" purge-inactive-versions --backend "%BACKEND%" --env "%ENV_FILE%" --retention-days %RETENTION_DAYS% --apply >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo. >> "%LOG_FILE%"
echo Agent TC manutencao finalizada com codigo %EXIT_CODE% >> "%LOG_FILE%"
echo Log: %LOG_FILE%

exit /b %EXIT_CODE%

@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul

echo ========================================
echo Agent TC - Python pos-rodagem
echo ========================================

REM Este BAT deve ficar na raiz do AgentTC dentro do Util Compartilhado.
set "BASE_DIR=%~dp0"
set "BASE_DIR=%BASE_DIR:~0,-1%"
for %%I in ("%BASE_DIR%\..") do set "PROJECT_ROOT=%%~fI"

set "ENV_FILE=%BASE_DIR%\.env"
if not exist "%ENV_FILE%" set "ENV_FILE=%PROJECT_ROOT%\.env"

if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="AGENT_TC_LOG_DIR" if "%AGENT_TC_LOG_DIR%"=="" set "AGENT_TC_LOG_DIR=%%B"
        if /I "%%A"=="AGENT_TC_PRACTICE_MDS_PATHS" if "%AGENT_TC_PRACTICE_MDS_PATHS%"=="" set "AGENT_TC_PRACTICE_MDS_PATHS=%%B"
        if /I "%%A"=="AGENT_TC_PRACTICE_PROJECT_SUITE" if "%AGENT_TC_PRACTICE_PROJECT_SUITE%"=="" set "AGENT_TC_PRACTICE_PROJECT_SUITE=%%B"
        if /I "%%A"=="AGENT_TC_SUPREMA_MDS_PATH" if "%AGENT_TC_SUPREMA_MDS_PATH%"=="" set "AGENT_TC_SUPREMA_MDS_PATH=%%B"
        if /I "%%A"=="AGENT_TC_MDS_PATH" if "%AGENT_TC_MDS_PATH%"=="" set "AGENT_TC_MDS_PATH=%%B"
        if /I "%%A"=="AGENT_TC_PROJECT_SUITE_PATH" if "%AGENT_TC_PROJECT_SUITE_PATH%"=="" set "AGENT_TC_PROJECT_SUITE_PATH=%%B"
        if /I "%%A"=="AGENT_TC_LOGS_BASE" if "%AGENT_TC_LOGS_BASE%"=="" set "AGENT_TC_LOGS_BASE=%%B"
        if /I "%%A"=="AGENT_TC_TIMES_FOLDER" if "%AGENT_TC_TIMES_FOLDER%"=="" set "AGENT_TC_TIMES_FOLDER=%%B"
        if /I "%%A"=="AGENT_TC_BACKEND" if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=%%B"
        if /I "%%A"=="AGENT_TC_SYSTEM" if "%AGENT_TC_SYSTEM%"=="" set "AGENT_TC_SYSTEM=%%B"
    )
)

if "%AGENT_TC_LOG_DIR%"=="" set "AGENT_TC_LOG_DIR=S:\Teste automatico\Arquivos\AgenteTC\logs"
if "%AGENT_TC_PRACTICE_MDS_PATHS%"=="" set "AGENT_TC_PRACTICE_MDS_PATHS=C:\TC\TC12 - Simplificado\Cadastros\Practice Base Unificada.mds;C:\TC\TC12 - Simplificado\Practice Antigo\Practice Bases Individuais.mds"
if "%AGENT_TC_PRACTICE_PROJECT_SUITE%"=="" set "AGENT_TC_PRACTICE_PROJECT_SUITE=C:\TC\TC12 - Simplificado\TestesVisualPractice.pjs"
if "%AGENT_TC_SUPREMA_MDS_PATH%"=="" set "AGENT_TC_SUPREMA_MDS_PATH=C:\TC\tc12\PROJETO-TC12\Integracoes\Integracoes.mds"
set "MDS_EXPLICIT=1"
if "%AGENT_TC_MDS_PATH%"=="" (
    set "MDS_EXPLICIT=0"
    set "AGENT_TC_MDS_PATH=C:\TC\Unico\Unico.mds"
)
set "PJS_EXPLICIT=1"
if "%AGENT_TC_PROJECT_SUITE_PATH%"=="" (
    set "PJS_EXPLICIT=0"
    set "AGENT_TC_PROJECT_SUITE_PATH="
)
if "%AGENT_TC_LOGS_BASE%"=="" set "AGENT_TC_LOGS_BASE=S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados"
if "%AGENT_TC_TIMES_FOLDER%"=="" if exist "C:\Tempos TC" set "AGENT_TC_TIMES_FOLDER=C:\Tempos TC"
if "%AGENT_TC_TIMES_FOLDER%"=="" if exist "C:\TC\Tempos TC" set "AGENT_TC_TIMES_FOLDER=C:\TC\Tempos TC"
set "LOG_DIR=%AGENT_TC_LOG_DIR%"
set "MDS_PATH=%AGENT_TC_MDS_PATH%"
set "PROJECT_SUITE_PATH=%AGENT_TC_PROJECT_SUITE_PATH%"
set "LOGS_BASE=%AGENT_TC_LOGS_BASE%"
set "TIMES_FOLDER=%AGENT_TC_TIMES_FOLDER%"
if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=api"
set "PY_CMD=python"

if /I "%AGENT_TC_SYSTEM%"=="practice" (
    set "MDS_PATH=%AGENT_TC_PRACTICE_MDS_PATHS%"
    if "%PJS_EXPLICIT%"=="0" set "PROJECT_SUITE_PATH=%AGENT_TC_PRACTICE_PROJECT_SUITE%"
)
if /I "%AGENT_TC_SYSTEM%"=="suprema" (
    set "MDS_PATH=%AGENT_TC_SUPREMA_MDS_PATH%"
)

py -3 --version > nul 2>&1
if "%ERRORLEVEL%"=="0" set "PY_CMD=py -3"

if "%~1"=="" (
    set "VM_NAME=%COMPUTERNAME%"
) else (
    set "VM_NAME=%~1"
)

if "%~2"=="" (
    set "RUN_FOLDER="
) else (
    set "RUN_FOLDER=%~2"
)

if "%~3"=="" (
    set "VERSION_HINT="
) else (
    set "VERSION_HINT=%~3"
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Base Agent TC: %BASE_DIR%
echo VM: %VM_NAME%
echo Env: %ENV_FILE%
echo MDS: %MDS_PATH%
if not "%PROJECT_SUITE_PATH%"=="" echo ProjectSuite: %PROJECT_SUITE_PATH%
echo Logs Agent TC: %LOG_DIR%
echo Logs base: %LOGS_BASE%
echo Backend: %AGENT_TC_BACKEND%
if not "%TIMES_FOLDER%"=="" echo Tempos TC: %TIMES_FOLDER%
echo Python: %PY_CMD%
if not "%VERSION_HINT%"=="" echo Filtro de versao: %VERSION_HINT%
echo.

if not exist "%BASE_DIR%\cli\agent_tc_ingest.py" (
    echo ERRO: cli\agent_tc_ingest.py nao encontrado em %BASE_DIR%
    goto ERRO_FINAL
)

if not exist "%ENV_FILE%" (
    echo ERRO: .env nao encontrado em %ENV_FILE%
    goto ERRO_FINAL
)

if "%RUN_FOLDER%"=="" (
    set "LOGS_ROOT=%LOGS_BASE%\%VM_NAME%"
    if not exist "!LOGS_ROOT!" (
        echo ERRO: pasta de logs da VM nao encontrada.
        echo Caminho: !LOGS_ROOT!
        goto ERRO_FINAL
    )

    if "!VERSION_HINT!"=="" (
        for /f "delims=" %%F in ('powershell -NoProfile -Command "Get-ChildItem -LiteralPath '!LOGS_ROOT!' -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName"') do (
            set "RUN_FOLDER=%%F"
        )
    ) else (
        for /f "delims=" %%F in ('powershell -NoProfile -Command "$hint = '!VERSION_HINT!'; Get-ChildItem -LiteralPath '!LOGS_ROOT!' -Directory | Where-Object { $_.Name -like ($hint + '*') } | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName"') do (
            set "RUN_FOLDER=%%F"
        )
    )

    if "!RUN_FOLDER!"=="" (
        echo ERRO: nenhuma pasta de rodagem encontrada para a VM/filtro.
        echo Caminho VM: !LOGS_ROOT!
        if not "!VERSION_HINT!"=="" echo Filtro: !VERSION_HINT!
        goto ERRO_FINAL
    )
)

if "!RUN_FOLDER!"=="" (
    echo ERRO: nenhuma pasta de rodagem encontrada.
    goto ERRO_FINAL
)

if not exist "!RUN_FOLDER!" (
    echo ERRO: pasta de rodagem nao encontrada.
    echo Caminho: !RUN_FOLDER!
    goto ERRO_FINAL
)

set "AUTO_SYSTEM_TEXT=!RUN_FOLDER! !VERSION_HINT!"
if /I "%AGENT_TC_SYSTEM%"=="practice" (
    set "MDS_PATH=%AGENT_TC_PRACTICE_MDS_PATHS%"
    if "%PJS_EXPLICIT%"=="0" set "PROJECT_SUITE_PATH=%AGENT_TC_PRACTICE_PROJECT_SUITE%"
) else if /I "%AGENT_TC_SYSTEM%"=="suprema" (
    set "MDS_PATH=%AGENT_TC_SUPREMA_MDS_PATH%"
) else (
    if "%MDS_EXPLICIT%"=="0" (
        powershell -NoProfile -Command "$text = '!AUTO_SYSTEM_TEXT!'.ToLowerInvariant(); if ($text.Contains('practice')) { exit 0 } exit 1" > nul 2>&1
        if "!ERRORLEVEL!"=="0" (
            set "MDS_PATH=%AGENT_TC_PRACTICE_MDS_PATHS%"
            if "%PJS_EXPLICIT%"=="0" set "PROJECT_SUITE_PATH=%AGENT_TC_PRACTICE_PROJECT_SUITE%"
        )
        powershell -NoProfile -Command "$text = '!AUTO_SYSTEM_TEXT!'.ToLowerInvariant(); if ($text.Contains('suprema') -or $text.Contains('integracoes')) { exit 0 } exit 1" > nul 2>&1
        if "!ERRORLEVEL!"=="0" set "MDS_PATH=%AGENT_TC_SUPREMA_MDS_PATH%"
    )
)

echo MDS selecionado: !MDS_PATH!
if not "!PROJECT_SUITE_PATH!"=="" echo ProjectSuite selecionado: !PROJECT_SUITE_PATH!
powershell -NoProfile -Command "$missing = @(); '!MDS_PATH!'.Split(';') | ForEach-Object { $p = $_.Trim().Trim([char]34); if ($p -and -not (Test-Path -LiteralPath $p)) { $missing += $p } }; if ($missing.Count -gt 0) { $missing | ForEach-Object { Write-Host ('MDS_MISSING=' + $_) }; exit 1 }"
if not "!ERRORLEVEL!"=="0" (
    echo ERRO: um ou mais arquivos .mds nao foram encontrados.
    goto ERRO_FINAL
)
if not "!PROJECT_SUITE_PATH!"=="" (
    if not exist "!PROJECT_SUITE_PATH!" (
        echo ERRO: ProjectSuite .pjs nao encontrado em !PROJECT_SUITE_PATH!
        goto ERRO_FINAL
    )
)

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%T"

set "RUN_LOG=%LOG_DIR%\agent_tc_python_%VM_NAME%_%TS%.log"
set "DONE_FILE=%LOG_DIR%\agent_tc_python_finalizado_%VM_NAME%_%TS%.txt"

echo Pasta da rodagem:
echo !RUN_FOLDER!
echo.
echo Log:
echo %RUN_LOG%
echo.

pushd "%BASE_DIR%"

%PY_CMD% "%BASE_DIR%\cli\agent_tc_ingest.py" ^
  --backend "%AGENT_TC_BACKEND%" ^
  --env "%ENV_FILE%" ^
  --run-folder "!RUN_FOLDER!" ^
  --mds "%MDS_PATH%" ^
  --output-root "%LOG_DIR%" ^
  --vm "%VM_NAME%" ^
  --times-folder "%TIMES_FOLDER%" ^
  --project-suite "!PROJECT_SUITE_PATH!" > "%RUN_LOG%" 2>&1

set "EXITCODE=%ERRORLEVEL%"

popd

echo.
echo ========================================
echo Agent TC Python finalizado com codigo %EXITCODE%
echo Log: %RUN_LOG%
echo ========================================

echo.
echo Ultimas linhas do log:
echo ----------------------------------------
powershell -NoProfile -Command "if (Test-Path -LiteralPath '%RUN_LOG%') { Get-Content -LiteralPath '%RUN_LOG%' -Tail 80 }"
echo ----------------------------------------
echo.

if "%EXITCODE%"=="0" (
    echo Agent TC Python finalizado com sucesso. > "%DONE_FILE%"
    echo VM: %VM_NAME% >> "%DONE_FILE%"
    echo Pasta: %RUN_FOLDER% >> "%DONE_FILE%"
    echo Data/Hora: %DATE% %TIME% >> "%DONE_FILE%"
    echo Log: %RUN_LOG% >> "%DONE_FILE%"
    echo ANALISE FINALIZADA COM SUCESSO.
) else (
    echo Agent TC Python finalizado com erro. > "%DONE_FILE%"
    echo VM: %VM_NAME% >> "%DONE_FILE%"
    echo Pasta: %RUN_FOLDER% >> "%DONE_FILE%"
    echo Data/Hora: %DATE% %TIME% >> "%DONE_FILE%"
    echo Codigo: %EXITCODE% >> "%DONE_FILE%"
    echo Log: %RUN_LOG% >> "%DONE_FILE%"
    echo ANALISE FINALIZADA COM ERRO.
)

echo Marcador: %DONE_FILE%
exit /b %EXITCODE%

:ERRO_FINAL
set "EXITCODE=1"
echo.
echo ========================================
echo Agent TC Python interrompido antes da analise.
echo ========================================
exit /b %EXITCODE%

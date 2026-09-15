@echo off
setlocal

set "BASE_DIR=%~dp0"
set "BASE_DIR=%BASE_DIR:~0,-1%"
call "%BASE_DIR%\PythonRodagem\run_agent_tc_python.bat" %*
exit /b %ERRORLEVEL%

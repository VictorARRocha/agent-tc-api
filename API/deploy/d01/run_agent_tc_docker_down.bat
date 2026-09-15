@echo off
setlocal

set "DEPLOY_DIR=%~dp0"
set "DEPLOY_DIR=%DEPLOY_DIR:~0,-1%"
set "ENV_FILE=%DEPLOY_DIR%\docker.env"
set "COMPOSE_FILE=%DEPLOY_DIR%\docker-compose.d01.yml"

cd /d "%DEPLOY_DIR%"

if not exist "%ENV_FILE%" (
    echo ERRO: docker.env nao encontrado.
    echo Copie docker.env.example para docker.env e preencha os valores da D01.
    exit /b 1
)

docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" down
exit /b %ERRORLEVEL%

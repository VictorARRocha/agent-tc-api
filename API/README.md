# API

Esta pasta contem a imagem e o deploy da API central. Na D01, use
`deploy/d01/docker-compose.d01.yml` e `deploy/d01/docker.env`; o guia atual e
[DEPLOY_D01_DOCKER.md](deploy/d01/DEPLOY_D01_DOCKER.md). O servidor Python,
repositorios, autenticacao e migrations ficam em `../PythonRodagem` e entram
na imagem por `Dockerfile`.

`run_agent_tc_api.bat` e `.ps1` permitem executar a API fora do Docker em
ambiente controlado; nao substituem o Compose da D01. Nessa execucao local,
os scripts procuram `.env` nesta ordem:

1. `API\.env`
2. `..\.env` legado da raiz
3. `..\PythonRodagem\.env`

No Docker da D01, a configuracao vem de `deploy/d01/docker.env`.

# API

Esta pasta contem tudo que sobe a API consumida pelo dashboard.

- `run_agent_tc_api.bat` / `run_agent_tc_api.ps1`: sobem a API fora do Docker.
- `start_agent_tc_api_hidden.vbs`: inicia a API escondida.
- `Dockerfile`: imagem da API.
- `deploy/d01/`: compose, `docker.env.example` e atalhos Docker da D01.

O codigo Python da API fica em `..\PythonRodagem` para evitar duplicacao de `agent_tc_core`.
Os scripts procuram `.env` nesta ordem:

1. `API\.env`
2. `..\.env` legado da raiz
3. `..\PythonRodagem\.env`

No Docker da D01, use `API\deploy\d01\docker.env`.

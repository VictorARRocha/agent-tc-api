# AgenteTC

Projeto do Agent TC para substituir o pos-processamento manual do Codex CLI por Python.

## Estrutura

- `API/`: scripts, Dockerfile e deploy da API consumida pelo dashboard.
- `JenkinsBridge/`: bridge que le pedidos da API e aciona Jenkins.
- `PythonRodagem/`: pos-rodagem em Python, parser de compactados, leitura de MDS/PJS, persistencia e migrations.
  - `agent_tc_core/`: pacote principal Python.
  - `cli/`: comandos Python de operacao.
  - `database/`: scripts SQL do modelo canonico.
  - `tests/`: testes de regressao e comportamento do pipeline.
- `docs/`: documentacao de setup, banco, API, deploy e historico do projeto.

## Entradas principais

- `PythonRodagem/run_agent_tc_python.bat`: deve ser chamado no pos-rodagem para processar a pasta mais recente da VM ou uma pasta informada.
- `API/run_agent_tc_api.bat`: sobe a API em `0.0.0.0:8000` fora do Docker.
- `API/start_agent_tc_api_hidden.vbs`: inicia a API escondida na VM que hospeda o Bridge.
- Wrappers com os nomes antigos continuam na raiz para compatibilidade.

## Modo recomendado na SCI

Para manter o PostgreSQL fechado na rede, as VMs devem usar `AGENT_TC_BACKEND=api`.
Nesse modo, a VM analisa a rodagem localmente, envia o payload e as evidencias para `AGENT_TC_API_URL`, e somente a API central grava no PostgreSQL/storage.
Use `AGENT_TC_BACKEND=postgres` apenas em teste controlado ou quando a infra decidir expor o banco conscientemente.

## Auth local

A API oferece Auth local no PostgreSQL:

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /auth/users`
- `PATCH /auth/users/{id}`

O primeiro cadastro vira admin aprovado automaticamente para bootstrap. As senhas sao salvas somente como hash PBKDF2 no PostgreSQL.

## Documentacao

- `API/deploy/d01/DEPLOY_D01_DOCKER.md`: guia operacional atual para API + PostgreSQL + storage local na D01.
- `docs/RESUMO_MIGRACAO_5_ETAPAS.md`: resumo atual da migracao para API, PostgreSQL, storage local, Bridge e Auth local.
- `docs/CANONICAL_DATA_MODEL.md`: modelo canonico do banco.

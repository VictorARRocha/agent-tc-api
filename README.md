# AgenteTC

Projeto do Agent TC para substituir o pos-processamento manual do Codex CLI por Python.

## Estrutura

- `agent_tc_core/`: pacote principal Python. Faz parsing da rodagem, extracao, evidencias, MDS, API e persistencia.
- `cli/`: comandos Python de operacao.
  - `agent_tc_ingest.py`: analisa uma pasta de rodagem e envia ao backend.
  - `agent_tc_api.py`: sobe a API consumida pelo dashboard.
  - `agent_tc_db.py`: utilitarios de banco, resumo e importacao.
- `database/`: scripts SQL do modelo canonico.
- `docs/`: documentacao de setup, banco, API, deploy e historico do projeto.
- `JenkinsBridge/`: bridge atual do Jenkins.
- `logs/`: logs de runtime da API local.
- `tests/`: testes de regressao e comportamento do pipeline.

## Entradas principais

- `run_agent_tc_python.bat`: deve ser chamado no pos-rodagem para processar a pasta mais recente da VM ou uma pasta informada.
- `run_agent_tc_api.bat`: sobe a API em `0.0.0.0:8000`.
- `start_agent_tc_api_hidden.vbs`: inicia a API escondida na VM que hospeda o Bridge.

## Modo recomendado na SCI

Para manter o PostgreSQL fechado na rede, as VMs devem usar `AGENT_TC_BACKEND=api`.
Nesse modo, a VM analisa a rodagem localmente, envia o payload e as evidencias para `AGENT_TC_API_URL`, e somente a API central grava no PostgreSQL/storage.
Use `AGENT_TC_BACKEND=postgres` apenas em teste controlado ou quando a infra decidir expor o banco conscientemente.

## Auth local

A API oferece Auth local para a migracao fora do Supabase Auth:

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /auth/users`
- `PATCH /auth/users/{id}`

O primeiro cadastro vira admin aprovado automaticamente para bootstrap. As senhas sao salvas somente como hash PBKDF2 no PostgreSQL.

## Documentacao

- `deploy/d01/DEPLOY_D01_DOCKER.md`: guia operacional atual para API + PostgreSQL + storage local na D01.
- `docs/RESUMO_MIGRACAO_5_ETAPAS.md`: resumo atual da migracao Supabase/Postgres/storage/Bridge/Auth.
- `docs/CANONICAL_DATA_MODEL.md`: modelo canonico do banco.
- `docs/legacy/`: analises e documentos antigos preservados apenas como historico.

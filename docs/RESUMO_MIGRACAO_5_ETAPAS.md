# Resumo da migracao em 5 etapas

Atualizado em: 2026-09-15

Objetivo: deixar o Agent TC operando com API + PostgreSQL + storage local da empresa, mantendo Supabase apenas como adapter legado/rollback.

## Estado geral

- Etapa 1: limpeza automatica - implementada.
- Etapa 2: StorageAdapter - implementada.
- Etapa 3: PostgresRepository local - implementada e validada.
- Etapa 4: JenkinsBridge via API - implementada e validada.
- Etapa 5: Auth/login sair do Supabase - implementada com Auth local no PostgreSQL.

## Arquitetura atual recomendada

```text
VMs de rodagem -> AgenteTC backend=api -> API D01 -> PostgreSQL D01
                                             |
                                             -> volume Docker de evidencias

Dashboard -> API D01
JenkinsBridge D01 -> API D01 -> Jenkins
```

O dashboard nao fala direto com PostgreSQL, storage local, Jenkins ou Supabase.

## Configuracao principal

VMs de rodagem:

```env
AGENT_TC_BACKEND=api
AGENT_TC_API_URL=http://IP_DA_D01:8000
AGENT_TC_API_TOKEN=mesmo_token_interno
```

D01/API Docker:

```env
POSTGRES_PASSWORD=senha_do_postgres
AGENT_TC_PUBLIC_BASE_URL=http://IP_DA_D01:8000/files
AGENT_TC_BRIDGE_TOKEN=mesmo_token_interno
AGENT_TC_AUTH_BACKEND=local
AGENT_TC_AUTH_SESSION_HOURS=24
AI_PROVIDER=openai
OPENAI_API_KEY=...
```

JenkinsBridge:

```env
JENKINS_BRIDGE_BACKEND=api
AGENT_TC_API_URL=http://IP_DA_D01:8000
AGENT_TC_BRIDGE_TOKEN=mesmo_token_interno
```

Dashboard:

```env
VITE_DATA_PROVIDER=api
VITE_AGENT_TC_API_URL=http://IP_DA_D01:8000
```

## O que ainda existe de Supabase

- `agent_tc_core/supabase_repository.py`: adapter legado para rollback.
- `agent_tc_core/storage.py`: `SupabaseStorageAdapter` legado para rollback.
- `JenkinsBridge/jenkins_bridge.py`: modo `JENKINS_BRIDGE_BACKEND=supabase` legado.
- `cli/agent_tc_db.py`: comandos antigos de utilidade Supabase.
- `docs/legacy/`: documentos historicos.

Esses pontos nao sao o caminho padrao atual. Para voltar temporariamente ao Supabase, a troca precisa ser explicita por `.env`/argumento.

## Onde procurar se quebrar

- Importacao da rodagem: `run_agent_tc_python.ps1`, `cli/agent_tc_ingest.py`, `agent_tc_core/api_repository.py`.
- API/dados no dashboard: `cli/agent_tc_api.py`, `agent_tc_core/api_server.py`, `agent_tc_core/postgres_repository.py`.
- Storage/evidencias: `agent_tc_core/storage.py`, `AGENT_TC_STORAGE_ROOT`, endpoint `/files/...`.
- Jenkins/reexecucao/cancelamento: `JenkinsBridge/jenkins_bridge.py`, endpoints `/bridge/rerun-requests/...`.
- Auth/login/admin: `agent_tc_core/local_auth.py`, tabelas `agent_tc_auth_*`, tela admin do dashboard.
- Agrupamento IA: `agent_tc_core/ai_grouping.py`, `AGENT_TC_AUTH_BACKEND=local`, token retornado por `/auth/login`.

## Validacoes ja realizadas

- API + PostgreSQL Docker na D01 no ar.
- Rodagem enviada por VM via API e exibida no dashboard.
- Evidencias servidas pela API por `/files/...`.
- JenkinsBridge consumindo pedidos via API.
- Agrupamento por IA funcionando.
- Login/Auth local funcionando com admin e usuario comum.

## Proximo handoff

Para infra/web, usar principalmente:

- `deploy/d01/DEPLOY_D01_DOCKER.md`
- `deploy/d01/docker-compose.d01.yml`
- `deploy/d01/docker.env.example`
- `.env.example`
- `JenkinsBridge/.env.example`

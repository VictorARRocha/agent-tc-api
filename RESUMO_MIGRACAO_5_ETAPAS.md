# Resumo da migracao em 5 etapas

Data: 2026-09-04

Objetivo: reduzir dependencia do Supabase e deixar o Agent TC preparado para banco/storage internos da empresa.

## Estado geral

Etapas:

- Etapa 1: limpeza automatica - implementada.
- Etapa 2: StorageAdapter - implementada.
- Etapa 3: PostgresRepository local - implementada e validada em teste local.
- Etapa 4: JenkinsBridge via API - implementada e validada em teste local.
- Etapa 5: Auth/login sair do Supabase - pendente.

## Etapa 1 - Limpeza automatica

Arquivos principais:

- `cli\agent_tc_maintenance.py`
- `run_agent_tc_maintenance.bat`
- `agent_tc_core\sqlite_repository.py`
- `agent_tc_core\supabase_repository.py`
- `agent_tc_core\postgres_repository.py`

O que faz:

- verifica versoes sem rodagem recente;
- remove rodagens antigas daquela versao;
- no Supabase, apaga objetos do bucket antes de apagar linhas;
- no Postgres/SQLite, usa o mesmo contrato de `purge_inactive_versions`.

Comando:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_maintenance.py" purge-inactive-versions --backend supabase --env "C:\TC\Util Compartilhado\AgenteTC\.env" --retention-days 30
```

Adicionar `--apply` para executar de fato.

## Etapa 2 - StorageAdapter

Arquivos principais:

- `agent_tc_core\storage.py`
- `agent_tc_core\supabase_repository.py`
- `agent_tc_core\api_server.py`

Backends existentes:

- `AGENT_TC_STORAGE=supabase`
- `AGENT_TC_STORAGE=local`

Variaveis para storage local:

```text
AGENT_TC_STORAGE=local
AGENT_TC_STORAGE_ROOT=\\servidor\pasta\AgenteTC\evidencias
AGENT_TC_PUBLIC_BASE_URL=http://IP_DA_API:8000/files
```

Observacoes:

- o banco guarda metadados e URL/caminho;
- o arquivo fisico fica no storage;
- a API serve storage local por `GET /files/<storage_path>`.

## Etapa 3 - PostgresRepository local

Arquivos principais:

- `database\postgres\001_initial.sql`
- `agent_tc_core\postgres_repository.py`
- `requirements-postgres.txt`
- `cli\agent_tc_db.py`
- `cli\agent_tc_ingest.py`
- `cli\agent_tc_api.py`
- `run_agent_tc_python.bat`
- `run_agent_tc_api.bat`
- `run_agent_tc_python.ps1`
- `run_agent_tc_api.ps1`

SQL canonico:

- `database\postgres\001_initial.sql` e o schema atual para PostgreSQL comum;
- contem somente estrutura (`CREATE SCHEMA`, `CREATE TABLE`, `CREATE INDEX`);
- nao contem dados de rodagem, `INSERT INTO` ou `COPY`;
- arquivos em `database\backups` sao historicos e nao devem ser usados como schema atual.

Variaveis:

```text
AGENT_TC_BACKEND=postgres
POSTGRES_DSN=postgresql://usuario:senha@host:5432/agent_tc
POSTGRES_SCHEMA=public
POSTGRES_TABLE_PREFIX=agent_tc_
```

Dependencia:

```powershell
py -3 -m pip install -r "C:\TC\Util Compartilhado\AgenteTC\requirements-postgres.txt"
```

Inicializar:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_db.py" init-postgres --env "C:\TC\Util Compartilhado\AgenteTC\.env"
```

Validar:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_db.py" summary-postgres --env "C:\TC\Util Compartilhado\AgenteTC\.env"
```

Teste realizado:

- Postgres local no notebook;
- uma rodagem importada;
- API local retornou `/runs` com dados do Postgres.

Correcoes feitas durante teste:

- API passou a serializar `datetime/date` vindos do Postgres;
- scripts `.ps1` criados porque alguns `.bat` foram bloqueados por politica do Windows no notebook;
- `.bat` principais alinhados para lerem as variaveis operacionais do `.env`, mantendo compatibilidade com as VMs.

## Etapa 4 - JenkinsBridge via API

Arquivos principais:

- `JenkinsBridge\jenkins_bridge.py`
- `JenkinsBridge\requirements.txt`
- `JenkinsBridge\run_bridge.ps1`
- `agent_tc_core\api_server.py`
- `agent_tc_core\supabase_repository.py`
- `agent_tc_core\postgres_repository.py`

Objetivo:

- tirar o Bridge do acesso direto a `SUPABASE_URL/rest/v1/agent_tc_rerun_requests`;
- fazer o Bridge falar com a API;
- deixar a API decidir se grava em Supabase, Postgres ou SQLite.

Modo hibrido:

- padrao antigo continua sendo Supabase;
- novo modo usa API.

Variaveis no `JenkinsBridge\.env` para usar API:

```text
JENKINS_BRIDGE_BACKEND=api
AGENT_TC_API_URL=http://127.0.0.1:8000
AGENT_TC_BRIDGE_TOKEN=
```

Se `AGENT_TC_BRIDGE_TOKEN` for definido na API e no Bridge, os endpoints internos exigem:

```text
Authorization: Bearer <token>
```

Endpoints internos adicionados:

```text
GET  /bridge/rerun-requests/requested
GET  /bridge/rerun-requests/active
GET  /bridge/rerun-requests/cancel-requested
POST /bridge/rerun-requests/{id}/claim
POST /bridge/rerun-requests/{id}/update
```

Validacao feita:

- API com Postgres local respondeu os tres GETs do Bridge com `[]`;
- codigo compilou em `api_server.py`, `supabase_repository.py`, `postgres_repository.py` e `jenkins_bridge.py`.
- dashboard criou solicitacao de rerun;
- Bridge consumiu a solicitacao pela API;
- Jenkins iniciou build;
- cancelamento pelo dashboard foi processado pelo Bridge;
- Jenkins retornou `ABORTED`;
- API/Postgres gravou `status=cancelado`, `execution_status=cancelado`, `execution_result=ABORTED`.

Como testar o Bridge via API:

1. Subir a API apontando para o backend desejado.
2. Configurar `JenkinsBridge\.env` com `JENKINS_BRIDGE_BACKEND=api`.
3. Rodar:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\TC\Util Compartilhado\AgenteTC\JenkinsBridge\run_bridge.ps1"
```

## Etapa 5 - Auth/login fora do Supabase

Pendente.

Hoje a API ainda usa:

- `agent_tc_core\auth.py`
- Supabase Auth para validar token no agrupamento por IA;
- dashboard Loveable ainda pode depender de Supabase Auth para login/sessao.

Esta etapa nao e so trocar tabela. Precisa decidir:

- login proprio na API;
- JWT assinado pela API;
- tabela local de usuarios/permissoes;
- protecao de rotas no dashboard sem Supabase Auth;
- migracao dos usuarios existentes, se necessario.

## Onde procurar se quebrar

Importacao da rodagem:

- `run_agent_tc_python.ps1`
- `cli\agent_tc_ingest.py`
- `agent_tc_core\pipeline.py`
- `agent_tc_core\postgres_repository.py`
- `agent_tc_core\supabase_repository.py`

API/dados no dashboard:

- `run_agent_tc_api.ps1`
- `cli\agent_tc_api.py`
- `agent_tc_core\api_server.py`
- repository ativo conforme `AGENT_TC_BACKEND`

Storage/evidencias:

- `agent_tc_core\storage.py`
- variaveis `AGENT_TC_STORAGE_*`
- endpoint `/files/...`

Performance:

- `agent_tc_core\performance.py`
- aceita linhas `MAIS LENTO` e `MAIS RAPIDO`;
- grava `status=mais_lento` ou `status=mais_rapido` em `run_delays`.

Manutencao/limpeza:

- `cli\agent_tc_maintenance.py`
- metodo `purge_inactive_versions` do repository ativo

Jenkins/reexecucao/cancelamento:

- `JenkinsBridge\jenkins_bridge.py`
- `JenkinsBridge\.env`
- endpoints `/bridge/rerun-requests/...`
- tabela `agent_tc_rerun_requests`

Auth/login:

- `agent_tc_core\auth.py`
- dashboard Loveable
- tabelas de usuarios/permissoes, se ainda existirem no Supabase

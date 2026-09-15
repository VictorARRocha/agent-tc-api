# Analise dos repositorios GitHub do Agent TC

> Documento historico. Use `docs/DOCUMENTACAO_COMPLETA_AGENT_TC.md` e `docs/RESUMO_MIGRACAO_5_ETAPAS.md` como estado atual do projeto. Algumas conclusoes abaixo ficaram obsoletas depois da implementacao de PostgresRepository, StorageAdapter e JenkinsBridge via API.

Data da analise: 2026-08-17

Repositorios analisados:

- API/backend: `https://github.com/VictorARRocha/agent-tc-api`
- Dashboard: `https://github.com/VictorARRocha/tczinho`

Clones locais usados apenas para leitura:

- `C:\Users\prog30\Documents\Phytozinar IA\repo-analysis\agent-tc-api`
- `C:\Users\prog30\Documents\Phytozinar IA\repo-analysis\tczinho`

## Estado dos repos

### `agent-tc-api`

Branch principal:

```text
main -> 8ad619b Fetch full testcase hierarchy from Supabase
```

Esse commit e importante porque corrige a leitura completa da hierarquia do Supabase usando paginacao.

### `tczinho`

Branches encontradas:

```text
main -> d98b6bd Corrigiu erro de cadastro no DB
lovable-fallback -> 5c5ec4
```

A analise foi feita sobre `main`.

## Resultado de validacao

### API/backend

Comando executado:

```bat
py -3 -m unittest discover -s tests -v
```

Resultado:

```text
Ran 27 tests
OK
```

Observacao: apareceram `ResourceWarning` de conexoes SQLite nao fechadas nos testes, mas sem falha funcional.

### Dashboard

Nao foi possivel rodar build/test local porque:

- `npm` nao existe no PATH do terminal;
- `pnpm.cmd` empacotado foi bloqueado por politica de grupo.

Mensagem:

```text
Este programa esta bloqueado por uma politica de grupo.
```

Portanto, a analise do dashboard foi estatica, por leitura dos arquivos.

## Comparacao GitHub API vs copia do Util Compartilhado

A copia operacional em `C:\TC\Util Compartilhado\AgenteTC` nao e um repositorio Git. Ela contem BATs, JenkinsBridge e `.env` operacionais, mas nao possui `.git`.

O repositorio GitHub `agent-tc-api` esta mais completo/atual para:

- testes automatizados;
- Docker/Render;
- docs tecnicas;
- schema Postgres;
- IA com Gemini/OpenAI;
- paginacao de hierarquia do Supabase.

A copia do Util Compartilhado contem itens operacionais que nao estao no repo da API:

- `run_agent_tc_python.bat`
- `run_agent_tc_api.bat`
- `start_agent_tc_api_hidden.vbs`
- `JenkinsBridge\`
- backups SQL antigos
- logs operacionais
- `docs/DOCUMENTACAO_COMPLETA_AGENT_TC.md`

## Divergencias importantes encontradas

### 1. Hierarquia Supabase

No GitHub, `agent_tc_core\supabase_repository.py` usa:

```python
rows = self._select_all("testcase_hierarchy", params)
```

e ordena numericamente os nodes.

Na copia do Util Compartilhado analisada anteriormente, o mesmo metodo usava `_select(...)` simples. Isso pode cortar a hierarquia em 1000 linhas quando a API local usa Supabase.

Impacto:

- API publica/Render deve estar correta se estiver no commit `8ad619b`;
- API local do Util pode estar atrasada;
- se o dashboard mostrar `[2.7]`, `[2.8]`, `[2.9]` sem nomes, essa e uma causa provavel.

### 2. IA/Gemini

No GitHub, `ai_grouping.py` possui:

- `GeminiChatCompletionsClient`
- `ai_client_from_env`
- `group_failures_in_batches`
- `AiGroupingInvalidJsonError`
- suporte a `AI_PROVIDER=openai|gemini`
- variaveis `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_OUTPUT_TOKENS`

Na copia do Util Compartilhado analisada antes, esses nomes nao existiam, embora `api_server.py` importasse alguns deles.

Impacto:

- a versao do GitHub esta coerente e testada;
- a copia do Util pode quebrar agrupamento por IA local se nao for sincronizada.

### 3. Performance

No GitHub existem testes `tests\test_performance.py`.

Na copia do Util, `performance.py` registrava apenas `MAIS LENTO`; nao registrava `MAIS RAPIDO`.

Recomendacao:

- se a feature de mais rapidos for necessaria, usar o GitHub como fonte de verdade e validar o arquivo atual antes de copiar para producao.

### 4. BATs operacionais ainda forcam Supabase

Na copia do Util, os BATs usam:

```bat
--backend supabase
```

O CLI ja aceita `--backend sqlite`, mas os BATs nao usam uma variavel como `AGENT_TC_BACKEND`.

Impacto para migracao:

- mesmo existindo SQLiteRepository, a rotina real ainda esta amarrada ao Supabase por BAT;
- primeiro ajuste recomendado: tornar backend configuravel por `.env`/variavel de ambiente.

### 5. JenkinsBridge ainda fala direto com Supabase

O Bridge em `C:\TC\Util Compartilhado\AgenteTC\JenkinsBridge` usa:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- tabela `agent_tc_rerun_requests`

Ele consulta e atualiza Supabase diretamente via REST.

Impacto para migracao:

- migrar apenas a API nao basta;
- JenkinsBridge tambem precisa virar adapter ou passar a consumir a API.

### 6. Dashboard ainda usa Supabase para Auth/Admin

O dashboard ja usa API para dados de QA, mas ainda usa Supabase diretamente para:

- Auth/sessao;
- `agent_tc_app_users`;
- `agent_tc_admin_audit_log`;
- realtime de usuarios/admin;
- Storage bruto em alguns fluxos.

Impacto:

- migrar banco de QA e uma etapa;
- migrar autenticacao/permissoes e outra etapa separada.

## API/backend GitHub

Arquivos principais:

- `agent_tc_core\api_server.py`: servidor HTTP e rotas.
- `agent_tc_core\supabase_repository.py`: adapter Supabase.
- `agent_tc_core\sqlite_repository.py`: adapter SQLite/local.
- `agent_tc_core\pipeline.py`: pos-processamento deterministico.
- `agent_tc_core\payload.py`: montagem do payload canonico.
- `agent_tc_core\mds.py`: leitura de MDS.
- `agent_tc_core\project_suite.py`: leitura de PJS do Practice.
- `agent_tc_core\parser.py`: analise de evidencias e comparacoes.
- `agent_tc_core\performance.py`: tempos.
- `agent_tc_core\ai_grouping.py`: agrupamento por IA.
- `cli\agent_tc_ingest.py`: CLI de ingestao.
- `cli\agent_tc_api.py`: CLI da API.
- `cli\agent_tc_db.py`: utilitarios de banco.

Docs relevantes:

- `docs\CANONICAL_DATA_MODEL.md`
- `docs\PUBLIC_API_DEPLOY.md`
- `docs\backups\agent_tc_schema_backup_current.sql`

Schemas:

- `database\postgres\001_initial.sql`
- `database\postgres\002_total_executed.sql`
- `database\postgres\003_testcase_description.sql`
- `database\sqlite\001_initial.sql`

## Dashboard GitHub

Arquivos principais:

- `src\services\data\types.ts`: contrato `QaDataSource`.
- `src\services\data\config.ts`: escolhe provider e URL da API.
- `src\services\data\apiSource.ts`: chamadas REST para API.
- `src\services\data\supabaseSource.ts`: hoje e wrapper do `ApiQaDataSource`, mantendo Storage Supabase.
- `src\services\data\index.ts`: ponto unico de importacao da camada de dados.
- `src\services\aiGrouping.ts`: chama endpoints de agrupamento e envia Bearer token.
- `src\contexts\AuthContext.tsx`: Auth/perfil via Supabase.
- `src\pages\AdminUsuarios.tsx`: usuarios/admin via Supabase.
- `src\pages\ModulePage.tsx`: tela principal do modulo.
- `src\components\JenkinsHistory.tsx`: historico e cancelamento Jenkins.

Configuracao atual:

```ts
VITE_DATA_PROVIDER=supabase | api
VITE_AGENT_TC_API_URL=https://agent-tc-api.onrender.com
```

Observacao: mesmo se `VITE_DATA_PROVIDER=supabase`, `SupabaseQaDataSource` delega dados de QA para `ApiQaDataSource`.

## Contrato de API consumido pelo dashboard

Endpoints usados:

- `GET /modules`
- `GET /modules/:slug/runs`
- `GET /runs`
- `GET /runs/:id`
- `GET /runs/:id/failures`
- `GET /runs/:id/evidences`
- `GET /failures/:id/evidences`
- `GET /runs/:id/groups`
- `GET /runs/:id/group-links`
- `GET /runs/:id/next-steps`
- `GET /runs/:id/performance`
- `GET /testcase-hierarchy?module=...`
- `GET /runs/:id/reexecutable-cases`
- `GET /rerun-requests`
- `GET /rerun-requests?slug=...`
- `POST /rerun-requests`
- `POST /rerun-requests/:id/cancel`
- `GET /runs/:id/ai-group-status`
- `POST /runs/:id/ai-group`

Para migrar banco sem quebrar dashboard, preservar esse contrato.

## Conclusao para migracao de banco

O melhor caminho nao e mexer no dashboard primeiro.

Ordem recomendada:

1. Tomar o GitHub `agent-tc-api` como fonte tecnica da API atual.
2. Sincronizar a copia operacional do Util com o GitHub, preservando BATs e JenkinsBridge.
3. Criar um adapter novo no backend para o banco local da empresa, copiando o contrato de `SQLiteRepository`.
4. Tornar `run_agent_tc_python.bat` e `run_agent_tc_api.bat` configuraveis por backend.
5. Decidir storage local para evidencias.
6. Migrar JenkinsBridge para API ou adapter local.
7. Manter dashboard consumindo a mesma API.
8. Deixar Supabase Auth/Admin para uma fase separada, se a empresa quiser abandonar Supabase por completo.

## Proximo arquivo recomendado

Para a proxima etapa, criar:

```text
PLANO_MIGRACAO_BANCO_LOCAL.md
```

com:

- banco escolhido;
- DDL canonico;
- adapter Python;
- estrategia de storage;
- plano para JenkinsBridge;
- plano para Auth/Admin;
- testes de equivalencia Supabase x banco local.

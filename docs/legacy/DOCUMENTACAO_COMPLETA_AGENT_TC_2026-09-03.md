# Legado: documentacao completa da fase de migracao

Este documento foi preservado como historico tecnico. Ele descreve partes do
estado antigo, incluindo Supabase como padrao, e nao deve ser usado como guia de
deploy atual. O deploy atual da D01 esta em `deploy/d01/DEPLOY_D01_DOCKER.md`.

# Documentacao completa do Agent TC

Data desta revisao: 2026-09-03

Este arquivo documenta a copia operacional encontrada em:

```text
C:\TC\Util Compartilhado\AgenteTC
```

Objetivo: servir como handoff tecnico para entender o fluxo inteiro do Agent TC e como preparar a migracao do Supabase para um banco local da empresa.

## Resumo executivo

O Agent TC substitui o uso do Codex CLI no pos-rodagem do TestComplete.

Antes, a IA fazia leitura de arquivos, extracao, classificacao de falhas, envio de evidencias, inserts no banco e agrupamento. Agora a regra correta e:

- Python faz o trabalho deterministico.
- Banco/Storage guardam os dados e evidencias.
- API entrega os dados ao dashboard.
- IA e chamada somente sob demanda, pelo botao de agrupamento no dashboard.

O backend atual continua usando Supabase por padrao, mas os BATs principais ja aceitam troca por variavel (`AGENT_TC_BACKEND`). O codigo possui adapters SQLite, Supabase e Postgres local para a base canonica de rodagens. Auth/login do dashboard ainda e uma etapa separada.

## Mapa da pasta atual

Arquivos e pastas encontrados:

```text
C:\TC\Util Compartilhado\AgenteTC
|-- .env
|-- run_agent_tc_api.bat
|-- run_agent_tc_python.bat
|-- start_agent_tc_api_hidden.vbs
|-- agent_tc_core\
|-- cli\
|-- database\
|-- JenkinsBridge\
|-- logs\
```

Nao foi encontrado `.git` nessa pasta. Ela parece ser a copia operacional do Util Compartilhado, nao o checkout Git principal.

Repositorio historico do backend/API:

```text
VictorARRocha/agent-tc-api
```

Repositorio historico do dashboard:

```text
VictorARRocha/tczinho
```

## Fluxo completo

```mermaid
flowchart TD
  A["Dashboard Lovable"] --> B["API Agent TC"]
  B --> C["agent_tc_rerun_requests"]
  C --> D["JenkinsBridge"]
  D --> E["Jenkins"]
  E --> F["TestComplete na VM"]
  F --> G["Pasta de logs compactados"]
  G --> H["run_agent_tc_python.bat"]
  H --> I["cli/agent_tc_ingest.py"]
  I --> J["agent_tc_core.pipeline"]
  J --> K["MDS/PJS + RAR/ZIP + Tempos TC"]
  K --> L["Payload shadow"]
  L --> M["Repository Supabase, Postgres ou SQLite"]
  M --> N["Banco + Storage"]
  N --> B
  A --> O["Botao Agrupar falhas"]
  O --> P["POST /runs/{id}/ai-group"]
  P --> Q["OpenAI/Gemini conforme implementacao"]
  Q --> N
```

## Sistemas suportados

### Unico

MDS padrao:

```text
C:\TC\Unico\Unico.mds
```

Variaveis globais lidas no MDS:

- `wpSomaCasosExecutados`
- `wpSomaErros`
- `wpSomaDiferencas`
- `wpRodagemJenkins`

Mapeamento por prefixo do caso:

- `0`: Geral
- `1`: Folha
- `2`: Fiscal
- `3`: Contabil
- `4`: Contabil
- `5`: Financeiro
- `6`: Geral
- `7`: Contabil
- `9`: Gestao

Exemplo de pasta esperada:

```text
PROXIMA1.26.7.0 03_07_2026 20_37_58
```

O parser aceita:

```text
VERSAO dd_MM_yyyy HH_mm_ss
dd_MM_yyyy HH_mm_ss
```

### Practice

Sistema legado. Usa dois MDS e um PJS.

MDS:

```text
C:\TC\TC12 - Simplificado\Cadastros\Practice Base Unificada.mds
C:\TC\TC12 - Simplificado\Practice Antigo\Practice Bases Individuais.mds
```

ProjectSuite:

```text
C:\TC\TC12 - Simplificado\TestesVisualPractice.pjs
```

Regra:

- nomes/hierarquia dos casos vem dos MDS;
- variaveis globais vem do PJS;
- prefixo `19`;
- arquivo de erro tambem pode ser `logErro.txt`;
- se a pasta nao tiver versao clara, o sistema usa `PRACTICE`;
- se encontrar versao estilo `8.30a`, captura como versao.

### Suprema

Sistema legado.

MDS:

```text
C:\TC\tc12\PROJETO-TC12\Integracoes\Integracoes.mds
```

Regra:

- usa MDS para hierarquia e variaveis;
- prefixo `16`;
- se a pasta nao tiver versao clara, usa `SUPREMA`.

Observacao: a regra antiga de excecao para VMs `testevsup` e `a03` nao aparece no codigo Python atual. O sistema usa prefixo/modulo e caminho de MDS.

## Arquivos da raiz

### `.env`

Arquivo de configuracao da API/ingestao. Chaves encontradas, sem valores:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_BUCKET
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_TIMEOUT_SECONDS
OPENAI_MAX_OUTPUT_TOKENS
```

Nao logar valores desse arquivo. O backend padrao segue sendo Supabase:

```text
AGENT_TC_BACKEND=supabase
AGENT_TC_STORAGE=supabase
```

Para teste com PostgreSQL local:

```text
AGENT_TC_BACKEND=postgres
POSTGRES_DSN=postgresql://usuario:senha@servidor:5432/agent_tc
POSTGRES_SCHEMA=public
POSTGRES_TABLE_PREFIX=agent_tc_
AGENT_TC_STORAGE=supabase
```

Enquanto o storage continuar no Supabase, manter `AGENT_TC_STORAGE=supabase`. Quando o storage local/S3 for adotado, esta variavel muda sem trocar o repository do banco.

### `run_agent_tc_python.bat`

Entrada principal do pos-rodagem.

Funcoes:

- define `BASE_DIR` pela pasta do proprio BAT;
- localiza `.env`;
- le do `.env`, quando existirem, as variaveis operacionais `AGENT_TC_BACKEND`, caminhos de logs, caminhos Practice/Suprema, MDS/PJS, pasta de tempos e `AGENT_TC_SYSTEM`;
- define caminhos padrao de logs, MDS, PJS e Tempos TC;
- recebe VM no argumento 1;
- recebe pasta da rodagem no argumento 2, se existir;
- recebe filtro de versao no argumento 3, se existir;
- se nao receber pasta, procura a pasta mais recente em:

```text
S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados\<VM>
```

- detecta Practice/Suprema por `AGENT_TC_SYSTEM` ou pelo texto da pasta/versao;
- valida se MDS/PJS existem;
- chama `cli\agent_tc_ingest.py`;
- grava log em:

```text
S:\Teste automatico\Arquivos\AgenteTC\logs
```

Comando interno atual:

```bat
%PY_CMD% "%BASE_DIR%\cli\agent_tc_ingest.py" ^
  --backend "%AGENT_TC_BACKEND%" ^
  --env "%ENV_FILE%" ^
  --run-folder "!RUN_FOLDER!" ^
  --mds "%MDS_PATH%" ^
  --output-root "%LOG_DIR%" ^
  --vm "%VM_NAME%" ^
  --times-folder "%TIMES_FOLDER%" ^
  --project-suite "!PROJECT_SUITE_PATH!"
```

Padrao seguro: se `AGENT_TC_BACKEND` nao estiver definido nem no ambiente nem no `.env`, o BAT usa `supabase`.

### `run_agent_tc_api.bat`

Sobe a API.

Comportamento:

- host padrao: `0.0.0.0`
- porta padrao: `8000`
- log: `logs\agent_tc_api.log`
- chama `cli\agent_tc_api.py`
- le `AGENT_TC_API_HOST`, `AGENT_TC_API_PORT` e `AGENT_TC_BACKEND` do `.env` antes de aplicar os padroes

Comando interno atual:

```bat
--backend "%AGENT_TC_BACKEND%"
```

Padrao seguro: se `AGENT_TC_BACKEND` nao estiver definido nem no ambiente nem no `.env`, a API sobe em `supabase`.

### `start_agent_tc_api_hidden.vbs`

Inicia `run_agent_tc_api.bat` em janela escondida.

Uso: manter API rodando numa VM sem terminal aberto.

## Pasta `cli`

### `cli\agent_tc_ingest.py`

CLI de ingestao.

Argumentos principais:

- `--run-folder`
- `--mds`
- `--output-root`
- `--vm`
- `--times-folder`
- `--project-suite`
- `--backend supabase|sqlite|postgres`
- `--env`
- `--sqlite-db`
- `--postgres-dsn`
- `--dry-run`

Fluxo:

1. chama `run_shadow_pipeline`;
2. cria `SQLiteRepository`, `SupabaseRepository` ou `PostgresRepository`;
3. chama `repository.import_payload`;
4. imprime JSON com resumo: rodagem, falhas, evidencias, diferencas, hierarquia, atrasos e resultado do import.

### `cli\agent_tc_api.py`

CLI da API HTTP.

Argumentos:

- `--host`
- `--port`
- `--logs-root`
- `--backend local-json|sqlite|supabase|postgres`
- `--sqlite-db`
- `--env`
- `--postgres-dsn`
- `--postgres-schema`
- `--postgres-table-prefix`
- `--supabase-schema`
- `--supabase-table-prefix`
- `--read-only`

Fluxo:

1. cria repository conforme backend;
2. inicializa SQLite/Supabase/Postgres quando aplicavel;
3. chama `make_server`;
4. sobe servidor HTTP.

### `cli\agent_tc_db.py`

Utilitarios de banco.

Comandos:

- `init-sqlite`: cria/atualiza banco SQLite local;
- `init-supabase`: valida Supabase, cria bucket e semeia modulos;
- `init-postgres`: cria/atualiza PostgreSQL local e semeia modulos;
- `import-payload`: importa `shadow_payload.json`;
- `summary`: contagens no SQLite;
- `summary-supabase`: contagens no Supabase;
- `summary-postgres`: contagens no PostgreSQL local.

Esse script e muito importante para migracao porque ja prova que o projeto foi desenhado com mais de um backend.

## Pacote `agent_tc_core`

### `constants.py`

Centraliza:

- prefixo -> modulo/sistema;
- modulo -> prefixos aceitos;
- status de classificacao;
- extensoes de compactados, imagens e textos;
- marcadores de Base/Atual;
- nomes de arquivo de erro.

Nomes de erro aceitos hoje:

```python
informacaoerro.txt
logerro.txt
erro.txt
callstack.txt
```

### `config.py`

Define `RunContext` e parsing da pasta de rodagem.

Campos do contexto:

- `run_folder`
- `mds_path`
- `output_root`
- `vm_name`
- `versao`
- `versao_safe`
- `data_inicio`
- `stamp`
- `id_rodagem`

ID de rodagem:

```text
rod_<vm>_<versao_safe>_<yyyyMMdd_HHmmss>
```

Funcoes importantes:

- `infer_vm_from_path`
- `infer_version_from_mds_path`
- `is_legacy_system_context`
- `normalize_run_version`
- `parse_run_context`

### `extractor.py`

Cuida dos compactados.

Funcoes:

- `find_extractor`: procura 7-Zip/WinRAR/UnRAR;
- `extract_case_id`: pega o ID antes do primeiro hifen;
- `inventory_archives`: lista RAR/ZIP da pasta;
- `extract_archive`: extrai ZIP nativamente ou RAR via ferramenta externa.

Classe:

- `ArchiveInfo`

Regra de ID:

```text
3.1.8.1-03_07_2026-20_00_00.RAR -> 3.1.8.1
```

### `mds.py`

Le MDS e monta indice de hierarquia.

Classes:

- `CaseInfo`
- `MdsIndex`
- `MdsCollection`

Funcoes:

- `module_for_node_id`
- `split_mds_paths`
- `infer_system_from_mds_paths`
- `infer_system_from_mds_path`

Comportamento:

- parseia XML do MDS;
- identifica nodes pelo padrao `[1.2.3] Nome`;
- monta `full_path_ids`, `full_path_names` e `full_path_label`;
- extrai `script_name` e `procedure_name` do `testMoniker`;
- le variaveis persistentes em `Variable/DefValue`;
- se nao achar caso, retorna `Nao encontrado no .mds`.

Regra critica: `descricao` vem do atributo `description` do MDS. Ela nao deve ser substituida por mensagem de erro.

### `project_suite.py`

Le variaveis globais no PJS, usado principalmente pelo Practice.

Classe:

- `ProjectSuiteVariables`

Metodo principal:

- `project_variable_int(name)`

### `parser.py`

Analisa conteudo extraido de cada compactado.

Classes:

- `EvidenceCandidate`
- `Comparison`
- `FailureAnalysis`

Funcoes:

- `find_comparisons`
- `summarize_comparison`
- `analyze_failure`
- `mime_for`

Classificacao:

- TXT de erro e comparacao: `Quebra com diferenca`;
- somente TXT de erro: `Quebra de testes`;
- somente comparacao Base/Atual: `Diferenca entre arquivos de comparacao`;
- nenhum sinal: `Sem classificacao objetiva`.

Resumo de diferenca:

- hash base/atual;
- quantidade de linhas;
- estimativa de linhas alteradas;
- amostra de diff limitada.

### `performance.py`

Le arquivos `*.txt` da pasta de tempos.

Padrao atual reconhecido:

```text
<caso> - <delay> MAIS LENTO --- Planilha: <hh:mm:ss> | Atual: <hh:mm:ss>
<caso> - <delay> MAIS RAPIDO --- Planilha: <hh:mm:ss> | Atual: <hh:mm:ss>
```

O parser normaliza acentos antes de comparar, entao aceita `MAIS RAPIDO` e `MAIS RÁPIDO`. Casos mais lentos entram com `status=mais_lento`; casos mais rapidos entram com `status=mais_rapido`. `delay_segundos` guarda a diferenca absoluta em segundos.

Saida vira `atrasos_rodagem` no payload e `run_delays` no banco.

### `pipeline.py`

Orquestrador do pos-rodagem.

Funcoes auxiliares:

- `read_total_tests_from_run_folder`
- `read_occurrence_totals_from_run_folder`
- `read_occurrence_totals_from_project_suite`
- `build_archive_integrity`
- `read_named_int`
- `read_text_file`

Funcao principal:

- `run_shadow_pipeline`

Fluxo:

1. separa caminhos MDS;
2. parseia contexto da rodagem;
3. valida pasta, MDS e PJS;
4. carrega PJS se existir;
5. inventaria RAR/ZIP;
6. le `TotalOcorrenciasTC.txt` ou variaveis do PJS;
7. calcula integridade de compactacao;
8. extrai arquivos;
9. analisa cada falha;
10. le `TotalTestesRodados.txt`;
11. se necessario, usa fallback `wpSomaCasosExecutados` no PJS/MDS;
12. le performance;
13. monta payload;
14. escreve relatorios locais.

Arquivos auxiliares esperados:

- `TotalTestesRodados.txt`
- `TotalOcorrenciasTC.txt`

### `payload.py`

Monta o `shadow_payload.json`.

Funcoes:

- `module_from_failures`
- `hierarchy_rows_for_module`
- `failure_id`
- `cluster_id`
- `build_shadow_payload`

O payload contem:

- `rodagem`
- `modulo`
- `agrupamentos_shadow`
- `falhas`
- `evidencias`
- `diferencas_relatorio`
- `atrasos_rodagem`
- `testcase_hierarchy`
- `erros_processamento`
- `upsert_plan`
- `ia_input`

Importante: `agrupamentos_shadow` e agrupamento temporario por status, nao agrupamento real por IA.

### `reporter.py`

Gera:

- `shadow_payload.json`
- `ia_input.json`
- `shadow_summary.md`

Esses arquivos sao gravados na pasta de output/logs da analise.

### `utils.py`

Funcoes utilitarias:

- `safe_token`
- `ascii_lower`
- `read_text_fallback`
- `short_text`

### `api_repository.py`

Repository baseado em arquivos locais `shadow_payload.json`. Usado no modo `local-json`.

Classe:

- `LocalPayloadRepository`

Metodos principais:

- `modules`
- `runs`
- `run`
- `payload`
- `failures`
- `evidences`
- `groups`
- `next_steps`
- `performance`
- `testcase_hierarchy`
- `record_rerun_request`
- `rerun_requests`

Uso: bom para debug local sem banco.

### `api_server.py`

Servidor HTTP simples usando `BaseHTTPRequestHandler`.

Endpoints GET:

- `/health`
- `/modules`
- `/modules/{slug}/runs`
- `/runs`
- `/runs/{id}`
- `/runs/{id}/payload`
- `/runs/{id}/failures`
- `/runs/{id}/evidences`
- `/runs/{id}/groups`
- `/runs/{id}/group-links`
- `/runs/{id}/next-steps`
- `/runs/{id}/performance`
- `/runs/{id}/reexecutable-cases`
- `/runs/{id}/ai-group-status`
- `/runs/{id}/ai-group-debug`
- `/failures/{id}/evidences`
- `/testcase-hierarchy?module=...`
- `/rerun-requests`

Endpoints POST:

- `/analyze`
- `/runs/{id}/ai-group`
- `/rerun-requests`
- `/rerun-requests/{id}/cancel`

Regra de migracao: manter estes endpoints e seus JSONs estaveis. O dashboard nao deve precisar saber qual banco esta por tras.

### `auth.py`

Valida Bearer token usando Supabase Auth:

- chama `/auth/v1/user`;
- usa service role/secret key do `.env`;
- retorna erro se sessao ausente, invalida ou expirada.

Para migrar completamente para banco local, este ponto tambem precisa ser redesenhado se o Supabase Auth for abandonado.

### `ai_grouping.py`

Implementa contrato de agrupamento por IA.

Estado encontrado nesta copia:

- existe `OpenAIResponsesClient`;
- usa endpoint `https://api.openai.com/v1/responses`;
- modelo default no arquivo: `gpt-5.4-mini`;
- le `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_OUTPUT_TOKENS`;
- monta prompt de sistema restrito;
- monta input com falhas/evidencias/diferencas;
- valida que toda falha aparece exatamente uma vez;
- materializa grupos, links e acoes.

Ponto critico encontrado: `api_server.py` importa `ai_client_from_env`, `group_failures_in_batches` e `AiGroupingInvalidJsonError`, mas esses nomes nao existem neste `ai_grouping.py`. Se a API de IA esta funcionando no Render, a versao publicada provavelmente difere desta copia do Util Compartilhado. Antes de mexer em IA, sincronizar/validar esse ponto.

### `sqlite_repository.py`

Adapter SQLite.

Metodos publicos equivalentes ao Supabase:

- `initialize`
- `import_payload_file`
- `import_payload`
- `modules`
- `runs`
- `run`
- `failures`
- `evidences`
- `evidences_by_failure`
- `report_differences`
- `ai_grouping_status`
- `ai_grouping_debug`
- `save_ai_job`
- `persist_ai_grouping`
- `groups`
- `group_links`
- `reexecutable_cases`
- `next_steps`
- `performance`
- `testcase_hierarchy`
- `payload`
- `rerun_requests`
- `record_rerun_request`
- `cancel_rerun_request`

Esse arquivo e a melhor base existente para migrar para banco local, mesmo que a empresa escolha outro banco depois.

### `supabase_repository.py`

Adapter Supabase/PostgREST/Storage.

Metodos publicos principais:

- `initialize`
- `seed_modules`
- `import_payload_file`
- `import_payload`
- `modules`
- `runs`
- `run`
- `failures`
- `evidences`
- `report_differences`
- `ai_grouping_status`
- `save_ai_job`
- `persist_ai_grouping`
- `groups`
- `group_links`
- `reexecutable_cases`
- `next_steps`
- `performance`
- `testcase_hierarchy`
- `payload`
- `rerun_requests`
- `record_rerun_request`
- `cancel_rerun_request`
- `ensure_bucket`
- `upload_file`

Importacao:

1. garante bucket;
2. semeia modulos;
3. grava `runs` com status `processing`;
4. grava `ingestion_batches`;
5. importa hierarquia;
6. importa ocorrencias;
7. importa grupos shadow;
8. importa links;
9. faz upload de evidencias;
10. importa diferencas;
11. importa performance;
12. verifica contagens;
13. marca run como `analyzed`;
14. se falhar, marca `import_failed`.

Ponto critico ja tratado: leituras de alta cardinalidade no `SupabaseRepository` usam `_select_all(...)` com paginacao. Isso cobre hierarquia, rodagens, falhas, evidencias, diferencas, agrupamentos, links, proximos passos e performance, evitando corte silencioso no limite de 1000 linhas do PostgREST.

### `postgres_repository.py`

Adapter PostgreSQL local.

Objetivo: permitir que a base canonica de rodagens saia do Supabase Database e rode em PostgreSQL comum, mantendo o mesmo contrato da API.

Comportamento:

- reutiliza a logica de importacao do `SupabaseRepository`;
- troca chamadas PostgREST por SQL direto via `psycopg`;
- usa tabelas com prefixo default `agent_tc_`;
- le `POSTGRES_DSN` ou `DATABASE_URL`;
- inicializa `database\postgres\001_initial.sql`;
- respeita o mesmo `StorageAdapter`, entao banco PostgreSQL local pode continuar apontando evidencias para Supabase Storage, storage local ou outro adapter futuro.

Dependencia opcional:

```powershell
py -3 -m pip install -r "C:\TC\Util Compartilhado\AgenteTC\requirements-postgres.txt"
```

Variaveis:

```text
AGENT_TC_BACKEND=postgres
POSTGRES_DSN=postgresql://usuario:senha@servidor:5432/agent_tc
POSTGRES_SCHEMA=public
POSTGRES_TABLE_PREFIX=agent_tc_
```

Comandos uteis:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_db.py" init-postgres --env "C:\TC\Util Compartilhado\AgenteTC\.env"
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_db.py" summary-postgres --env "C:\TC\Util Compartilhado\AgenteTC\.env"
```

## Banco atual e schema local

Existe schema SQLite em:

```text
database\sqlite\001_initial.sql
```

Tabelas locais:

- `schema_migrations`
- `modules`
- `runs`
- `ingestion_batches`
- `testcase_hierarchy`
- `occurrences`
- `evidence_files`
- `report_differences`
- `ai_analysis_jobs`
- `ai_groups`
- `ai_group_occurrences`
- `recommended_actions`
- `run_delays`
- `rerun_requests`

No Supabase, o adapter usa prefixo default:

```text
agent_tc_
```

Exemplo:

- SQLite: `runs`
- Supabase: `agent_tc_runs`

### `database\sqlite\001_initial.sql`

Schema local inicial. Este arquivo cria o banco SQLite com tabelas sem prefixo `agent_tc_`. Ele e a melhor referencia concreta para desenhar o banco local da empresa, porque expressa o modelo canonico sem depender diretamente do Supabase/PostgREST.

### `database\backups`

Backups SQL encontrados:

- `SQL do dashboard.sql`
- `agent_tc_schema_backup_20260708_115517.sql`
- `agent_tc_schema_backup_20260708_151128.sql`
- `agent_tc_auth_permissions_20260708_155536.sql`

Uso recomendado:

- usar como referencia historica do Supabase;
- nao usar automaticamente como schema final sem comparar com `database\sqlite\001_initial.sql`;
- para migracao, preferir gerar um DDL canonico novo a partir do schema atual validado.

### `logs`

Pasta de logs locais da API quando iniciada por `run_agent_tc_api.bat`.

Arquivo principal esperado:

```text
logs\agent_tc_api.log
```

Os logs de pos-rodagem normalmente sao gravados fora da pasta local, em:

```text
S:\Teste automatico\Arquivos\AgenteTC\logs
```

## JenkinsBridge

Pasta:

```text
C:\TC\Util Compartilhado\AgenteTC\JenkinsBridge
```

Arquivos:

- `.env`
- `jenkins_bridge.py`
- `run_bridge.bat`
- `start_bridge_hidden.vbs`
- `logs\jenkins_bridge.log`

Chaves do `.env` do Bridge:

```text
JENKINS_URL
JENKINS_JOB_PATH
JENKINS_USER
JENKINS_API_TOKEN
JENKINS_BRIDGE_BACKEND
AGENT_TC_API_URL
AGENT_TC_BRIDGE_TOKEN
```

Modos do Bridge:

```text
JENKINS_BRIDGE_BACKEND=supabase
JENKINS_BRIDGE_BACKEND=api
```

No modo `supabase`, o Bridge usa `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` e fala diretamente com `agent_tc_rerun_requests`.

No modo `api`, o Bridge usa `AGENT_TC_API_URL` e chama os endpoints internos `/bridge/rerun-requests/...`. Nesse modo ele nao precisa saber se a API esta usando Supabase ou PostgreSQL local; quem decide o banco e o `.env` da API/AgenteTC.

Fluxo:

1. busca registros `requested` ou `solicitado`;
2. tenta capturar mudando para `processando`;
3. compacta `config_json`;
4. envia POST ao Jenkins com `CONFIG_JSON`;
5. salva fila/build;
6. monitora fila e build;
7. processa cancelamentos;
8. atualiza status final.

Status ativos:

- `enviado_jenkins`
- `na_fila`
- `rodando`
- `processando`
- `erro_monitoramento`
- `cancelando`

Status de cancelamento:

- `cancel_requested`
- `cancelamento_solicitado`

Regra correta de cancelamento: so marcar `cancelado` apos confirmar no Jenkins que item saiu da fila ou build retornou `ABORTED`.

Estado atual: o modo `api` foi implementado e validado em teste local com PostgreSQL. A solicitacao saiu do dashboard, o Bridge consumiu pela API, iniciou build Jenkins e confirmou cancelamento como `ABORTED`.

## Storage/evidencias

O envio de evidencias agora passa por adapter de storage.

Arquivo principal:

- `C:\TC\Util Compartilhado\AgenteTC\agent_tc_core\storage.py`

Adapters existentes:

- `SupabaseStorageAdapter`: comportamento atual, usando Supabase Storage.
- `LocalFileStorageAdapter`: copia os arquivos para uma pasta local/compartilhada.

O `SupabaseRepository` continua cuidando do banco Supabase, mas nao precisa mais conhecer detalhes de bucket. Ele chama `self.storage.upload_file(...)` ao importar evidencias e `self.storage.delete_paths(...)` durante a manutencao/retencao.

O payload prepara:

- arquivo compactado original;
- TXT de erro;
- imagens;
- prints;
- arquivos de comparacao base;
- arquivos de comparacao atual.

Regra importante: evidencia so deve ser persistida no banco depois do upload confirmado.

Configuracao padrao, mantendo o comportamento atual:

```text
AGENT_TC_STORAGE=supabase
SUPABASE_BUCKET=evidencias-rodagens
```

Configuracao para storage local/compartilhado:

```text
AGENT_TC_STORAGE=local
AGENT_TC_STORAGE_ROOT=S:\Teste automatico\Arquivos\AgenteTC\evidencias
AGENT_TC_PUBLIC_BASE_URL=http://IP_DA_API:8000/files
```

Observacao: o `LocalFileStorageAdapter` grava/apaga arquivos em pasta local, e a API ja possui endpoint para servir esses arquivos por `/files/...`.

Endpoint de arquivos locais:

- `GET /files/<storage_path>`

Quando `AGENT_TC_STORAGE=local`, a API le `AGENT_TC_STORAGE_ROOT` e serve apenas arquivos dentro dessa raiz. Caminhos fora da raiz configurada sao bloqueados. Para o dashboard receber links abrindo pela propria API, configurar:

```text
AGENT_TC_PUBLIC_BASE_URL=http://IP_DA_API:8000/files
```

Para banco local/Postgres, recomendacao:

- nao salvar binario pesado dentro do banco;
- usar file server/NAS/MinIO/S3 interno;
- banco guarda metadados e caminho/URL;
- API serve ou assina links para o dashboard.

## Como uma falha vira card no dashboard

1. TestComplete gera RAR/ZIP na pasta da rodagem.
2. `run_agent_tc_python.bat` chama o Python.
3. `extractor.inventory_archives` lista compactados.
4. `extractor.extract_case_id` pega o ID antes do hifen.
5. `extractor.extract_archive` extrai.
6. `mds.MdsCollection.case_info` busca nome e descricao no MDS.
7. `parser.analyze_failure` classifica quebra/diferenca.
8. `payload.build_shadow_payload` monta linhas.
9. `repository.import_payload` grava banco/storage.
10. `api_server` expoe `/runs/{id}/failures`.
11. Dashboard monta arvore usando `/testcase-hierarchy`.

## Como o total de casos rodados e calculado

Prioridade:

1. `TotalTestesRodados.txt` dentro da pasta da rodagem;
2. `wpSomaCasosExecutados` no PJS, se houver PJS;
3. `wpSomaCasosExecutados` no MDS.

Coluna final:

```text
runs.total_executed
agent_tc_runs.total_executed
```

## Como a integridade de compactacao e calculada

Prioridade:

1. `TotalOcorrenciasTC.txt` na pasta da rodagem;
2. variaveis do PJS, se houver.

Valores:

- `wpSomaErros`
- `wpSomaDiferencas`

O Python soma os dois e compara com a quantidade de RAR/ZIP encontrados. Se houver divergencia, adiciona erro de processamento com etapa `integridade_compactacao`.

## Pontos criticos encontrados nesta revisao

1. `run_agent_tc_python.bat`, `run_agent_tc_api.bat` e `run_agent_tc_maintenance.bat` usam `AGENT_TC_BACKEND`, com default `supabase`.

2. A pasta operacional nao e um repositorio Git. Para rastreio de versao, comparar com `VictorARRocha/agent-tc-api`.

3. Auth/login ainda depende de Supabase Auth. Migrar banco nao migra autenticacao automaticamente.

4. JenkinsBridge possui modo hibrido. Para banco local completo, usar `JENKINS_BRIDGE_BACKEND=api`.

5. Para Postgres local, instalar `requirements-postgres.txt` e configurar `POSTGRES_DSN`.

## Roteiro recomendado para migrar para banco local

1. Subir um PostgreSQL local de teste.
2. Instalar `requirements-postgres.txt` no ambiente Python que roda Agent TC.
3. Configurar `.env` com `AGENT_TC_BACKEND=postgres` e `POSTGRES_DSN`.
4. Inicializar schema:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_db.py" init-postgres --env "C:\TC\Util Compartilhado\AgenteTC\.env"
```

5. Rodar uma importacao controlada em uma VM.
6. Subir a API com `AGENT_TC_BACKEND=postgres`.
7. Validar endpoints principais no dashboard ou via navegador:

```text
GET /health
GET /modules
GET /runs
GET /runs/{id}/failures
GET /runs/{id}/evidences
GET /testcase-hierarchy?module=fiscal
```

8. Para testar reexecucao/cancelamento, subir o Bridge com `JENKINS_BRIDGE_BACKEND=api` e validar pelos endpoints `/bridge/rerun-requests/...`.

Configuracao dos BATs:

```bat
if "%AGENT_TC_BACKEND%"=="" set "AGENT_TC_BACKEND=supabase"
...
--backend "%AGENT_TC_BACKEND%"
```

Manter endpoints HTTP iguais para nao quebrar o dashboard.

## Contrato de API que deve permanecer estavel

O dashboard depende destes endpoints:

- `GET /health`
- `GET /modules`
- `GET /modules/{slug}/runs`
- `GET /runs`
- `GET /runs/{id}`
- `GET /runs/{id}/failures`
- `GET /runs/{id}/evidences`
- `GET /failures/{id}/evidences`
- `GET /runs/{id}/groups`
- `GET /runs/{id}/group-links`
- `GET /runs/{id}/next-steps`
- `GET /runs/{id}/performance`
- `GET /runs/{id}/reexecutable-cases`
- `GET /runs/{id}/ai-group-status`
- `GET /runs/{id}/ai-group-debug`
- `GET /testcase-hierarchy?module=...`
- `GET /rerun-requests`
- `POST /rerun-requests`
- `POST /rerun-requests/{id}/cancel`
- `POST /runs/{id}/ai-group`

## Diagnostico rapido

Rodagem nao aparece:

- olhar log em `S:\Teste automatico\Arquivos\AgenteTC\logs`;
- conferir `agent_tc_runs`;
- conferir `/runs`;
- conferir `module_id`;
- conferir URL da API no dashboard.

Falhas contadas, mas aba vazia:

- conferir `runs.status` ou `agent_tc_runs.status`;
- se `import_failed`, olhar `ingestion_batches.summary_json`;
- conferir `occurrences`;
- conferir se coluna de descricao do caso existe e esta preenchida.

Hierarquia sem nome:

- conferir `/testcase-hierarchy?module=fiscal`;
- conferir se retornou mais de 1000 linhas;
- aplicar paginacao no adapter.

Total executado zero:

- conferir `TotalTestesRodados.txt`;
- conferir PJS do Practice;
- conferir MDS do Unico/Suprema;
- conferir `total_executed`.

Performance vazia:

- conferir pasta `C:\Tempos TC` ou `C:\TC\Tempos TC`;
- conferir se as linhas seguem o padrao `MAIS LENTO`;
- nesta copia, `MAIS RAPIDO` nao e parseado.

Agrupamento IA falha:

- conferir se a versao de `ai_grouping.py` bate com `api_server.py`;
- conferir `OPENAI_API_KEY`;
- conferir modelo;
- conferir `/runs/{id}/ai-group-debug`.

## Ordem segura para dar manutencao

1. Ler este documento.
2. Ler `run_agent_tc_python.bat`.
3. Ler `cli\agent_tc_ingest.py`.
4. Ler `agent_tc_core\pipeline.py`.
5. Ler `agent_tc_core\payload.py`.
6. Ler `agent_tc_core\supabase_repository.py` e/ou `sqlite_repository.py`.
7. So depois mexer na API ou dashboard.

Para migracao de banco, mexer primeiro em adapter/backend. Evitar alterar dashboard enquanto o contrato da API puder ser preservado.

## Manutencao diaria e retencao de versoes

Foi adicionada uma rotina de manutencao para liberar armazenamento de versoes que nao rodam ha mais de 30 dias.

Arquivo principal:

- `C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_maintenance.py`

Atalho para agendamento diario:

- `C:\TC\Util Compartilhado\AgenteTC\run_agent_tc_maintenance.bat`

Regra aplicada:

- A rotina agrupa as rodagens por `system + version`.
- Se a ultima rodagem daquela versao for mais antiga que `retention_days`, por padrao 30 dias, todas as rodagens daquela versao entram no plano de limpeza.
- A limpeza remove dados dependentes de rodagem: `report_differences`, `recommended_actions`, `ai_group_occurrences`, `ai_groups`, `ai_analysis_jobs`, `run_delays`, `evidence_files`, `ingestion_batches`, `occurrences` e `runs`.
- A tabela `testcase_hierarchy` nao e apagada, pois representa catalogo/hierarquia de testes, nao uma rodagem especifica.
- No Supabase, os arquivos do bucket sao apagados antes das linhas do banco. Se a exclusao do bucket falhar, a rotina interrompe antes de apagar os registros, reduzindo risco de perder rastreabilidade.
- No SQLite, a rotina limpa as tabelas. Arquivos locais/buckets devem ser tratados por um adapter de storage especifico quando o projeto migrar para armazenamento local, MinIO, S3 ou outro equivalente.

Comando de simulacao:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_maintenance.py" purge-inactive-versions --backend supabase --env "C:\TC\Util Compartilhado\AgenteTC\.env" --retention-days 30
```

Comando real:

```powershell
py -3 "C:\TC\Util Compartilhado\AgenteTC\cli\agent_tc_maintenance.py" purge-inactive-versions --backend supabase --env "C:\TC\Util Compartilhado\AgenteTC\.env" --retention-days 30 --apply
```

O `.bat` ja executa o comando real com `--apply` e grava log em:

```text
S:\Teste automatico\Arquivos\AgenteTC\logs
```

Para rodar todos os dias, apontar o Agendador de Tarefas do Windows, Jenkins ou outro scheduler interno para:

```text
C:\TC\Util Compartilhado\AgenteTC\run_agent_tc_maintenance.bat
```

Portabilidade:

- A regra de retencao foi implementada nos adapters `SupabaseRepository` e `SQLiteRepository`.
- Para Postgres local ou outro banco, criar um novo repository com o mesmo metodo `purge_inactive_versions(retention_days, dry_run, now)`.
- Para storage fora do Supabase, implementar uma funcao equivalente a `delete_storage_paths`, apagando os objetos fisicos antes de remover as linhas do banco.

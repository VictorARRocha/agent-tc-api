# Modelo de dados do Agent TC

Resumo do modelo implementado no PostgreSQL da D01. A definicao exata de
colunas, tipos, chaves e indices esta nas migrations em
`PythonRodagem/database/postgres/`. O dashboard consome a API e nao depende
diretamente dos nomes das tabelas. SQLite continua disponivel para testes e
execucoes locais controladas.

## Principios

- O Python e dono do processo deterministico.
- IA so agrupa/classifica causa a partir de JSON pequeno.
- O dashboard nao deve depender de detalhes internos do banco.
- IDs precisam ser deterministicos para reprocessar sem duplicar.
- Evidencia so deve ser marcada como enviada depois de upload confirmado.
- Storage e banco sao conceitos separados.
- O modelo deve evitar tipos muito especificos de um provedor quando houver
  alternativa simples.

## Entidades Principais

### `modules`

Modulos oficiais do sistema.

Campos:

- `id`: texto, PK. Ex.: `mod_contabil`.
- `slug`: texto unico. Ex.: `contabil`.
- `name`: texto. Ex.: `Contabil`.
- `system`: texto. Ex.: `Unico`.
- `codes_json`: JSON/texto com codigos do modulo. Ex.: `["3","4","7"]`.
- `active`: booleano.
- `sort_order`: inteiro.
- `created_at`: timestamp.
- `updated_at`: timestamp.

### `runs`

Rodagens do TestComplete.

Campos:

- `id`: texto, PK. Ex.: `rod_A08_PROXIMA1.26.7.0_20260703_203758`.
- `system`: texto.
- `version`: texto.
- `vm_name`: texto.
- `module_id`: FK para `modules.id`.
- `started_at`: timestamp.
- `finished_at`: timestamp nullable.
- `logs_path`: texto.
- `status`: texto.
- `total_archives`: inteiro.
- `total_occurrences`: inteiro.
- `total_executed`: inteiro nullable. Total real executado vindo de `TotalTestesRodados.txt`, `wpSomaCasosExecutados` no `.mds` ou `wpSomaCasosExecutados` no `.pjs` do ProjectSuite. No Suprema e no Practice, conta os casos da rodagem (os medidos no `Tempo CT.csv` e os que falharam).
- `total_ai_groups`: inteiro.
- `repository_url`, `repository_root`, `repository_revision`: texto nullable.
  Origem SVN da rodagem (`svn info` do projeto na VM), usada pela regravacao.
  Na importacao pelo dashboard, a URL e a da branch escolhida e a revisao fica
  vazia.
- `created_at`: timestamp.
- `updated_at`: timestamp.

Notas:

- `id` e tecnico.
- Dashboard deve exibir `version`, `vm_name`, `started_at` e modulo.

### `testcase_hierarchy`

Arvore lida do `.mds`.

Campos:

- `id`: texto/uuid deterministico, PK.
- `system`: texto.
- `module_id`: FK para `modules.id`.
- `module_code`: texto. Ex.: `3`.
- `module_name`: texto.
- `node_id`: texto. Ex.: `3.1.3.8`.
- `parent_node_id`: texto nullable.
- `node_name`: texto.
- `node_type`: texto. Valores: `grupo`, `caso`.
- `description`: texto nullable. Descricao do caso/grupo no `.mds`.
- `full_path_ids_json`: JSON/texto.
- `full_path_names_json`: JSON/texto.
- `full_path_label`: texto.
- `script_name`: texto nullable.
- `procedure_name`: texto nullable.
- `mds_path`: texto.
- `created_at`: timestamp.
- `updated_at`: timestamp.

Indice recomendado:

- unico por `system + node_id`.
- indice por `module_id`.
- indice por `parent_node_id`.

### `occurrences`

Ocorrencias encontradas na rodagem. Substitui o conceito mais limitado de
`falhas`.

Campos:

- `id`: texto, PK.
- `run_id`: FK para `runs.id`.
- `module_id`: FK para `modules.id`.
- `testcase_node_id`: texto. Ex.: `3.1.3.8`.
- `testcase_name`: texto.
- `testcase_description`: texto nullable. Descricao real do caso vinda do `.mds`.
- `group_node_id`: texto nullable.
- `group_name`: texto nullable.
- `source_archive_name`: texto.
- `source_archive_size_bytes`: inteiro.
- `occurrence_type`: texto.
- `status`: texto.
- `error_message`: texto nullable.
- `log_summary`: texto nullable.
- `technical_signature`: texto nullable.
- `created_at`: timestamp.
- `updated_at`: timestamp.

Valores de `occurrence_type`:

- `test_break`
- `report_difference`
- `test_break_with_difference`
- `incomplete_evidence`
- `unknown`

### `evidence_files`

Metadados dos arquivos de evidencia.

Campos:

- `id`: texto, PK.
- `run_id`: FK para `runs.id`.
- `occurrence_id`: FK para `occurrences.id`, nullable em casos especiais.
- `module_id`: FK para `modules.id`.
- `file_role`: texto.
- `file_type`: texto.
- `original_name`: texto.
- `local_path`: texto nullable.
- `storage_provider`: texto. Ex.: `local`, `s3`, `external`.
- `storage_bucket`: texto nullable.
- `storage_path`: texto nullable.
- `public_url`: texto nullable.
- `signed_url`: texto nullable.
- `signed_url_expires_at`: timestamp nullable.
- `mime_type`: texto nullable.
- `extension`: texto nullable.
- `size_bytes`: inteiro nullable.
- `sha256`: texto nullable.
- `upload_status`: texto.
- `created_at`: timestamp.
- `updated_at`: timestamp.

Valores de `file_role`:

- `archive_original`
- `error_text`
- `error_image`
- `screen_print`
- `comparison_base`
- `comparison_current`
- `other_text`
- `other`

Valores de `upload_status`:

- `pending`
- `uploaded`
- `failed`
- `skipped`

O storage em uso e `local`. Os campos de URL e provedor permitem representar
outros destinos, mas o adapter `s3` ainda nao esta implementado. Uma URL
registrada em `public_url` nao dispensa a autenticacao exigida por `/files/...`.

### `report_differences`

Pares base/atual detectados pelo Python.

Campos:

- `id`: texto, PK.
- `run_id`: FK para `runs.id`.
- `occurrence_id`: FK para `occurrences.id`.
- `module_id`: FK para `modules.id`.
- `testcase_node_id`: texto.
- `base_evidence_id`: FK para `evidence_files.id`.
- `current_evidence_id`: FK para `evidence_files.id`.
- `base_file_name`: texto.
- `current_file_name`: texto.
- `base_lines`: inteiro nullable.
- `current_lines`: inteiro nullable.
- `changed_lines_estimate`: inteiro nullable.
- `summary_json`: JSON/texto.
- `created_at`: timestamp.

### `ai_groups`

Agrupamentos gerados pela IA.

Campos:

- `id`: texto, PK.
- `run_id`: FK para `runs.id`.
- `module_id`: FK para `modules.id`.
- `title`: texto.
- `technical_signature`: texto.
- `classification`: texto.
- `confidence`: inteiro.
- `justification`: texto.
- `status`: texto.
- `created_at`: timestamp.
- `updated_at`: timestamp.

### `ai_group_occurrences`

Vinculo N:N entre agrupamentos da IA e ocorrencias.

Campos:

- `group_id`: FK para `ai_groups.id`.
- `occurrence_id`: FK para `occurrences.id`.
- `created_at`: timestamp.

PK composta:

- `group_id + occurrence_id`

### `recommended_actions`

Acoes sugeridas pela IA ou por regra deterministica.

Campos:

- `id`: texto, PK.
- `run_id`: FK para `runs.id`.
- `group_id`: FK para `ai_groups.id`, nullable.
- `occurrence_id`: FK para `occurrences.id`, nullable.
- `category`: texto.
- `hypothesis`: texto.
- `action`: texto.
- `confidence`: inteiro nullable.
- `priority`: texto nullable.
- `status`: texto.
- `created_at`: timestamp.
- `updated_at`: timestamp.

### `run_delays`

Performance dos testes.

Campos:

- `id`: texto, PK.
- `run_id`: FK para `runs.id`.
- `module_id`: FK para `modules.id`.
- `testcase_node_id`: texto.
- `testcase_name`: texto nullable.
- `expected_seconds`: inteiro.
- `actual_seconds`: inteiro.
- `delay_seconds`: inteiro.
- `status`: texto.
- `created_at`: timestamp.

Valores de `status`:

- `slower`
- `faster`
- `same`

### `rerun_requests`

Solicitacoes de reexecucao para JenkinsBridge.

Campos:

- `id`: texto/uuid, PK.
- `source_run_id`: FK para `runs.id`, nullable.
- `vm_name`: texto.
- `version`: texto.
- `module_id`: FK para `modules.id`, nullable.
- `test_cases`: texto.
- `parallel`: texto nullable.
- `ct_desmarcar`: texto nullable.
- `branch`: texto nullable.
- `requested_by`: texto nullable.
- `request_type`: texto.
- `configuration_mode`: texto.
- `config_json`: JSON/texto.
- `status`: texto.
- `jenkins_queue_url`: texto nullable.
- `jenkins_build_url`: texto nullable.
- `jenkins_build_number`: texto nullable.
- `execution_status`: texto nullable.
- `execution_result`: texto nullable.
- `error_message`: texto nullable.
- `created_at`: timestamp.
- `updated_at`: timestamp.

## Pedidos dos Bridges e dados do dashboard

- `run_presets`: pre-definicoes de rodagem Jenkins salvas no dashboard
  (compartilhadas entre usuarios).
- `regravacao_requests`: pedidos de regravacao de bases. Guarda a rodagem, a
  URL de destino no SVN, os arquivos (`items_json`), o resultado por arquivo
  (`result_json`, com `conflito` quando a base mudou) e a revisao do commit.
  Executados pelo RegravacaoBridge.
- `import_requests`: pedidos de importacao de rodagem pela pasta do log
  compactado: pasta, VM, `branch_url` escolhida, codigo de saida e fim da saida
  do `.bat`. Executados pelo RegravacaoBridge.
- `merge_requests`: pedidos de merge entre branches (origem, destino, previa,
  conflitos e resultado). Executados pelo MergeBridge.
- `svn_branch_cache`: uma linha (`id = atual`) com a lista de branches do
  projeto do TC, enviada pelo MergeBridge.
- `testcase_disabled`: casos desmarcados pelo `mod_xml.py` em toda rodagem
  (`disabled_by = mod_xml`), sincronizados a cada ingestao do Unico. Marcacoes
  antigas feitas a mao no dashboard ficam no banco, mas nao sao exibidas.
- `testcase_times`: medicoes de tempo de cada caso, uma por caso e inicio de
  rodagem (lidas do `<Sistema> Tempo CT.csv`).
- `testcase_time_bases`: tempo base de cada caso (`origin`: `planilha`,
  `primeira_medicao` ou `substituida`); so quem tem a permissao `regravar`
  troca a base. A carga inicial vem de `cli/agent_tc_importar_tempos.py`.

## Tabelas auxiliares e de autenticacao

- `ingestion_batches`: acompanha a importacao de cada payload.
- `ai_analysis_jobs`: acompanha as solicitacoes de agrupamento por IA.
- `app_users`: perfil, hash de senha, papel e estado de aprovacao, mais
  `permissions` (JSON: `rodagem`, `merge`, `regravar`; vazio = so ver) e
  `modules` (JSON com os slugs dos modulos, ou `["*"]` = todos).
- `auth_sessions`: somente o hash do token de sessao e sua validade.
- `permission_catalog`, `user_permissions`, `user_module_permissions`:
  previstas no schema inicial e nao usadas. As permissoes valem pelas colunas
  `permissions` e `modules` de `app_users`.
- `admin_audit_log`: historico das acoes administrativas gravadas pela API.
- `schema_migrations`: versoes das migrations aplicadas.

Com o prefixo padrao `agent_tc_`, `runs` corresponde a
`public.agent_tc_runs`, `app_users` a `public.agent_tc_app_users` e assim por
diante. O banco guarda metadados das evidencias; o conteudo dos arquivos fica
no volume Docker de evidencias montado em `/data/evidencias` na API.

## Evolucao do schema

As migrations ficam numeradas em `PythonRodagem/database/postgres/`, de
`001_initial.sql` a `014_import_branch.sql` (a mais recente); cada arquivo
comeca com um comentario do que muda. O `PostgresRepository.initialize()`
aplica as migrations ao iniciar a API e inicializa o storage. O SQLite usa
`database/sqlite/001_initial.sql` e acrescenta colunas novas em
`SQLiteRepository.initialize()`.
Para conferir uma alteracao futura, consulte primeiro esses arquivos e os
testes em `PythonRodagem/tests/`.

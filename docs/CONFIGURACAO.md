# Configuracao por componente

Cada processo le apenas seu proprio arquivo de configuracao. Arquivos `.env`
reais ficam no host e nao devem ser enviados ao Git. Os `.env.example` sao
modelos sem segredos. No Docker, `docker.env` e lido pelo Compose e injetado na
API; no dashboard, variaveis `VITE_` entram no JavaScript durante o build.

| Componente | Arquivo real | Modelo |
| --- | --- | --- |
| API e PostgreSQL na D01 | `API/deploy/d01/docker.env` | `API/deploy/d01/docker.env.example` |
| Python nas VMs | `PythonRodagem/.env` | `PythonRodagem/.env.example` |
| JenkinsBridge na D01 | `JenkinsBridge/.env` | `JenkinsBridge/.env.example` |
| RegravacaoBridge na D01 | `RegravacaoBridge/.env` | `RegravacaoBridge/.env.example` |
| MergeBridge na D01 | `MergeBridge/.env` | `MergeBridge/.env.example` |
| Dashboard na D01 | `C:\TC\AgentTC-Dashboard\deploy\d01\web.env` | `web.env.example` no repositorio `tczinho` |

## API e PostgreSQL: `docker.env`

| Variavel | Para que serve | Origem do valor atual |
| --- | --- | --- |
| `POSTGRES_DB` | Nome do banco. | Nome criado no Compose; atualmente `agent_tc`. |
| `POSTGRES_USER` | Usuario da API no banco. | Usuario criado no Compose; atualmente `agent_tc`. |
| `POSTGRES_PASSWORD` | Senha do banco. | Segredo definido na instalacao; manter o valor correspondente ao volume existente. |
| `POSTGRES_SCHEMA` | Schema das tabelas. | Modelo do projeto; `public`. |
| `POSTGRES_TABLE_PREFIX` | Prefixo dos nomes de tabela. | Modelo do projeto; `agent_tc_`. |
| `AGENT_TC_API_PORT` | Porta da API no host D01. | `8001` no arranjo atual; dentro do container ela usa `8000`. |
| `AGENT_TC_PUBLIC_BASE_URL` | Prefixo das URLs de evidencias registradas. | `http://SEU_SUBDOMINIO.duckdns.org:10443/api/files`. A API ainda exige sessao para abrir arquivos. |
| `AGENT_TC_BRIDGE_TOKEN` | Autentica ingestao das VMs e chamadas do Bridge. | Segredo gerado pela equipe do projeto; mesmo valor nos clientes tecnicos. |
| `AGENT_TC_AUTH_SESSION_HOURS` | Validade da sessao de usuario. | Decisao do projeto; atualmente `24`. |
| `AI_PROVIDER` | Provedor para agrupamento. | Atualmente `gemini`; tambem ha suporte a `openai`. |
| `AI_TIMEOUT_SECONDS`, `AI_MAX_OUTPUT_TOKENS` | Limites gerais da IA. | Configuracao do projeto; atualmente `120` e `6000`. |
| `GEMINI_API_KEY` | Autorizacao no Gemini. | Chave da conta do provedor usada pela equipe. |
| `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_OUTPUT_TOKENS` | Modelo e limites Gemini. | Atualmente `gemini-3.5-flash`, `120` e `12000`. |
| `AI_RETRIES`, `AI_RETRY_SECONDS` | Opcional. Novas tentativas no modelo principal quando ele responde ocupado (503, 429, outros 5xx ou sem conexao). | Padrao `2` tentativas a cada `15` segundos. |
| `OPENAI_API_KEY` (com `AI_PROVIDER=gemini`) | Opcional. Se preenchida, a OpenAI (`OPENAI_MODEL`) vira o 2o nivel, usado quando o Gemini principal esgota as tentativas. | Vazia na D01 (nivel pulado). |
| `GEMINI_FALLBACK_MODEL` | Opcional. Ultimo nivel (1 nova tentativa); o job grava qual modelo respondeu, com "(reserva)". Vazio desliga. | Padrao `gemini-3.5-flash-lite`. |
| `AGENT_TC_AI_JOB_STALE_MINUTES` | Opcional. Minutos apos os quais um agrupamento ainda `running` e considerado interrompido e deixa de bloquear um novo pedido. | Padrao `30`. |

Se o provedor escolhido for OpenAI, use `OPENAI_API_KEY`, `OPENAI_MODEL`,
`OPENAI_TIMEOUT_SECONDS` e `OPENAI_MAX_OUTPUT_TOKENS` no lugar da chave e modelo
Gemini. A chave deve vir da conta do provedor. O Compose define
`AGENT_TC_AUTH_BACKEND=local`, `AGENT_TC_STORAGE=local`, o caminho interno
`/data/evidencias` e a conexao `POSTGRES_DSN`; normalmente nao se repetem esses
valores em `docker.env`.

`POSTGRES_PASSWORD` usado por um banco ja inicializado nao e alterado apenas
editando `docker.env`. Uma troca de senha exige coordenar a senha dentro do
PostgreSQL e a configuracao da API.

## PythonRodagem: `PythonRodagem/.env`

Configuracao minima nas VMs:

```env
AGENT_TC_BACKEND=api
AGENT_TC_API_URL=http://192.168.9.201:8000
AGENT_TC_BRIDGE_TOKEN=<mesmo_token_da_api>
```

`AGENT_TC_API_TOKEN` tambem e aceito para a ingestao e tem prioridade sobre
`AGENT_TC_BRIDGE_TOKEN`. Use um dos dois com o mesmo valor configurado na API.
Esse token nao e o token de usuario nem o token do Jenkins.

O launcher aceita ainda as seguintes opcoes quando os caminhos padrao da VM
nao servem:

| Variavel | Funcao |
| --- | --- |
| `AGENT_TC_LOGS_BASE` | Raiz das pastas compactadas, separadas por VM. |
| `AGENT_TC_LOG_DIR` | Onde gravar log e marcador de conclusao do Python. |
| `AGENT_TC_MDS_PATH` | MDS do Unico. |
| `AGENT_TC_PROJECT_SUITE_PATH` | PJS explicito, se necessario. |
| `AGENT_TC_PRACTICE_MDS_PATHS` | MDSs do Practice, separados por `;`. |
| `AGENT_TC_PRACTICE_PROJECT_SUITE` | PJS padrao do Practice. |
| `AGENT_TC_SUPREMA_MDS_PATH` | MDS do Suprema. |
| `AGENT_TC_TIMES_FOLDER` | Pasta opcional de tempos/performance. |
| `AGENT_TC_SYSTEM` | Forca `practice` ou `suprema` quando a deteccao pelo caminho nao basta. |
| `AGENT_TC_BRANCH_URL` | Nao vai no `.env`: o RegravacaoBridge define na importacao de rodagem, com a branch escolhida no dashboard, e ela substitui a origem SVN lida da copia local. |

Esses caminhos vem da instalacao do TestComplete e das pastas de logs da VM.
O script procura primeiro `PythonRodagem/.env` e aceita o `.env` antigo da raiz
como fallback. No modo `api`, a VM nao precisa de senha PostgreSQL, chave de IA
ou caminho do volume de evidencias da D01.

## JenkinsBridge: `JenkinsBridge/.env`

| Variavel | Para que serve | Onde obter |
| --- | --- | --- |
| `JENKINS_BRIDGE_BACKEND` | Modo de integracao. | `api`. |
| `AGENT_TC_API_URL` | API vista pelo Bridge na D01. | Atualmente `http://127.0.0.1:8000`. |
| `AGENT_TC_BRIDGE_TOKEN` | Autentica o Bridge na API. | Mesmo valor do `docker.env`. |
| `JENKINS_URL` | Endereco base do Jenkins. | Endereco da instalacao da empresa. |
| `JENKINS_JOB_PATH` | Caminho do job `buildWithParameters`. | URL do job no Jenkins, sem o dominio. |
| `JENKINS_USER` | Usuario de automacao. | Conta criada/indicada no Jenkins. |
| `JENKINS_API_TOKEN` | Autentica no Jenkins. | Token gerado na conta Jenkins; distinto do token do Agent TC. |

O Bridge le o `.env` da propria pasta ao iniciar. Alteracoes exigem reiniciar
esse processo Python.

## RegravacaoBridge: `RegravacaoBridge/.env`

| Variavel | Para que serve | Valor atual / padrao |
| --- | --- | --- |
| `AGENT_TC_API_URL` | API vista pelo Bridge na D01. | `http://127.0.0.1:8001` (direto na API). |
| `AGENT_TC_BRIDGE_TOKEN` | Autentica o Bridge na API. | Mesmo valor do `docker.env`. |
| `SVN_COMMIT` | `1` grava no SVN; outro valor so simula (confere tudo e nao grava). | Padrao `0`. |
| `SVN_ALLOWED_ROOTS` | Repositorios onde o Bridge pode gravar, separados por virgula. | `https://192.168.10.199:8443/svn/testcomplete`. |
| `SVN_USERNAME`, `SVN_PASSWORD` | Credenciais do SVN. | Recomendado deixar vazio e salvar a senha uma vez no perfil do Windows do usuario da tarefa (`svn info <url> --username <usuario>`). |
| `SVN_TRUST_SERVER_CERT_FAILURES` | Aceita certificado do SVN nao confiavel (ex.: `unknown-ca`). | Vazio. |
| `SVN_BIN_DIR` | Pasta do `svn.exe` e do `svnmucc.exe`. | TortoiseSVN. |
| `AGENT_TC_LOGS_BASE` | Raiz das pastas compactadas aceitas na importacao. | `S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados`. |
| `AGENT_TC_IMPORT_BAT` | `.bat` rodado na importacao de rodagem. | `run_agent_tc_python.bat` da pasta acima do Bridge. |

## MergeBridge: `MergeBridge/.env`

| Variavel | Para que serve | Valor atual / padrao |
| --- | --- | --- |
| `AGENT_TC_API_URL`, `AGENT_TC_BRIDGE_TOKEN` | Iguais aos do RegravacaoBridge. | |
| `MERGE_COMMIT` | `1` faz o commit do merge; outro valor so simula e desfaz. | Padrao `0`. |
| `MERGE_PROJECT_URL` | Projeto do TC; a principal e a pasta `branches/` ficam aqui. | `https://192.168.10.199:8443/svn/testcomplete/unico/ProjetoUnico`. |
| `MERGE_TRUNK_NAME` | Nome da pasta da principal. | `Unico`. |
| `MERGE_WC_DIR` | Copia de trabalho propria do Bridge (~2,5 GB), fora do Util Compartilhado. | `C:\AgenteTC\merge_wc`. |
| `BRANCHES_REFRESH_SECONDS` | Intervalo de envio da lista de branches ao dashboard. | `120`. |
| `SVN_USERNAME`, `SVN_PASSWORD`, `SVN_TRUST_SERVER_CERT_FAILURES`, `SVN_BIN_DIR` | Iguais aos do RegravacaoBridge. | |

A lista de branches do MergeBridge tambem alimenta a escolha de branch na
regravacao e na importacao de rodagem. Com o MergeBridge parado, a lista fica
na ultima versao enviada.

## Dashboard: `web.env` e build

O `web.env` usado pelo Compose na D01 contem apenas:

```env
AGENT_TC_PUBLIC_HTTP_PORT=8000
```

O Dockerfile do `tczinho` compila o dashboard com
`VITE_AGENT_TC_API_URL=/api` e `VITE_DATA_PROVIDER=api`. Assim o navegador usa
o mesmo host/porta do dashboard para chamar a API. Para desenvolvimento local
fora desse Compose, `.env`/`.env.local` do dashboard pode apontar para
`http://192.168.9.201:8000` e precisa de novo build ao mudar variaveis Vite.
Nunca coloque senha ou token em `VITE_`: eles ficam visiveis no navegador.

O DuckDNS e configurado fora da aplicacao para resolver o IP publico usado
pela NAT. Seu token de atualizacao nao pertence a nenhum dos arquivos acima.

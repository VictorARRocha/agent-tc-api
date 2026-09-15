# Guia simples do Agent TC para Infra e Web

Atualizado em: 2026-09-15

Este documento explica, de forma simples, os quatro pontos principais do
projeto Agent TC:

- API / Docker
- Python de rodagem
- JenkinsBridge
- Dashboard

A ideia geral e:

```text
VMs de rodagem -> PythonRodagem -> API Docker da D01 -> PostgreSQL + arquivos
Dashboard -> API Docker da D01
JenkinsBridge -> API Docker da D01 -> Jenkins
```

Ou seja: a API virou o centro do sistema. Todo mundo fala com ela, e ela cuida
do banco, arquivos, login, agrupamento por IA e pedidos de rerodagem.

## 1. API / Docker

A API e o servidor central do Agent TC.

Ela fica rodando na D01 via Docker e recebe os dados das VMs de rodagem. Depois
grava tudo no PostgreSQL, salva os arquivos de evidencia e entrega os dados
para o dashboard.

No Docker sobem dois servicos principais:

- `agent-tc-postgres`: banco PostgreSQL local.
- `agent-tc-api`: API Python que conversa com o banco e serve os dados.

### Onde fica

```text
API\
API\deploy\d01\
```

O arquivo principal de configuracao do Docker e:

```text
API\deploy\d01\docker.env
```

Nunca commitar o `docker.env` real, porque ele contem senha e token.

### Variaveis do docker.env

```env
POSTGRES_DB=agent_tc
```

Nome do banco PostgreSQL.

De onde vem:

- definido pela equipe do projeto ou pela infra.
- pode manter `agent_tc`.

```env
POSTGRES_USER=agent_tc
```

Usuario usado pela API para acessar o banco.

De onde vem:

- definido pela infra ou no proprio Docker.
- pode manter `agent_tc`.

```env
POSTGRES_PASSWORD=senha_forte
```

Senha do usuario do PostgreSQL.

De onde vem:

- definida pela infra.
- precisa ser uma senha forte.
- deve ficar somente no host, nunca no Git.

```env
POSTGRES_SCHEMA=public
```

Schema onde as tabelas ficam dentro do PostgreSQL.

De onde vem:

- configuracao do banco.
- pode manter `public`.

```env
POSTGRES_TABLE_PREFIX=agent_tc_
```

Prefixo usado no nome das tabelas.

Exemplo:

```text
agent_tc_runs
agent_tc_occurrences
agent_tc_evidence_files
```

De onde vem:

- padrao do projeto.
- pode manter `agent_tc_`.

```env
AGENT_TC_API_PORT=8000
```

Porta em que a API fica disponivel.

Exemplo:

```text
http://192.168.9.201:8000
```

De onde vem:

- definida pela infra.
- precisa estar liberada para quem for acessar a API.

```env
AGENT_TC_PUBLIC_BASE_URL=http://IP_DA_D01:8000/files
```

URL usada para abrir imagens e arquivos de evidencia no dashboard.

De onde vem:

- IP ou DNS da D01.
- se a infra criar HTTPS/reverse proxy, usar a URL publica HTTPS.

Exemplos:

```text
http://192.168.9.201:8000/files
https://agent-tc.sci.com.br/files
```

```env
AGENT_TC_BRIDGE_TOKEN=token_interno
```

Token interno compartilhado entre:

- API
- PythonRodagem nas VMs
- JenkinsBridge

Ele serve para a API aceitar chamadas internas.

Importante:

- nao e o token do Jenkins.
- pode ser qualquer string forte gerada pela equipe.
- o mesmo valor precisa estar nos tres lugares.

```env
AGENT_TC_AUTH_BACKEND=local
```

Define que o login dos usuarios e local, usando tabelas no PostgreSQL.

De onde vem:

- padrao atual do projeto.
- manter `local`.

```env
AGENT_TC_AUTH_SESSION_HOURS=24
```

Define por quantas horas uma sessao de login continua valida.

De onde vem:

- decisao do projeto.
- `24` significa que o usuario precisa logar novamente depois de 24 horas.

```env
AI_PROVIDER=openai
```

Define qual provedor de IA sera usado para agrupamento das falhas.

Valores esperados atualmente:

```text
openai
gemini
```

De onde vem:

- decisao do projeto.
- hoje o uso principal e `openai`.

```env
AI_TIMEOUT_SECONDS=120
AI_MAX_OUTPUT_TOKENS=6000
```

Limites gerais para chamadas de IA.

De onde vem:

- configuracao tecnica do projeto.
- pode manter os valores padrao.

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
OPENAI_TIMEOUT_SECONDS=120
OPENAI_MAX_OUTPUT_TOKENS=6000
```

Configuracao da OpenAI para agrupamento por IA.

De onde vem:

- `OPENAI_API_KEY`: chave da conta OpenAI.
- `OPENAI_MODEL`: modelo escolhido pelo projeto.
- demais valores: limites de tempo e resposta.

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
GEMINI_TIMEOUT_SECONDS=120
GEMINI_MAX_OUTPUT_TOKENS=12000
```

Configuracao alternativa para Gemini.

De onde vem:

- conta/chave Gemini, caso a empresa decida usar esse provedor.
- se for usar OpenAI, pode deixar vazio.

### Como a API usa esses dados

Quando o Docker sobe:

1. O PostgreSQL cria ou reutiliza o banco.
2. A API monta a string de conexao com o banco.
3. A API inicia na porta configurada.
4. A API grava dados no PostgreSQL.
5. A API salva evidencias em volume local do Docker.
6. O dashboard consulta a API.

## 2. Python de rodagem

O Python de rodagem e o script que roda nas VMs depois da execucao do
TestComplete.

Ele faz o pos-processamento da rodagem:

- localiza a pasta da rodagem;
- le os compactados;
- extrai logs, prints e arquivos;
- le arquivos `.mds`;
- le `.pjs` quando necessario;
- monta o payload da rodagem;
- envia tudo para a API.

### Onde fica

```text
PythonRodagem\
```

Script principal:

```text
PythonRodagem\run_agent_tc_python.bat
```

Configuracao:

```text
PythonRodagem\.env
```

### Variaveis principais do PythonRodagem

```env
AGENT_TC_BACKEND=api
```

Define para onde o Python envia os dados.

Valor recomendado:

```text
api
```

Significa que a VM envia os dados para a API central, e a API grava no banco.

```env
AGENT_TC_API_URL=http://IP_DA_D01:8000
```

URL da API central.

De onde vem:

- IP/DNS da D01 onde o Docker da API esta rodando.
- se a infra criar HTTPS, usar a URL HTTPS.

Exemplos:

```text
http://192.168.9.201:8000
https://agent-tc.sci.com.br
```

```env
AGENT_TC_API_TOKEN=mesmo_token_da_api
```

Token usado pela VM para enviar dados para a API.

De onde vem:

- mesmo valor do `AGENT_TC_BRIDGE_TOKEN` do `docker.env`.
- precisa bater exatamente.

```env
AGENT_TC_LOG_DIR=S:\Teste automatico\Arquivos\AgenteTC\logs
```

Pasta onde o proprio PythonRodagem salva logs de execucao.

De onde vem:

- caminho de rede/local usado pela equipe de QA.
- precisa existir ou permitir criacao.

```env
AGENT_TC_LOGS_BASE=S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados
```

Pasta raiz onde ficam as rodages compactadas do TestComplete.

Exemplo esperado:

```text
S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados\A08\...
```

De onde vem:

- caminho usado atualmente pelo processo de rodagem.

```env
AGENT_TC_MDS_PATH=C:\TC\Unico\Unico.mds
```

Caminho do `.mds` padrao do sistema Unico.

De onde vem:

- instalacao local do projeto TestComplete na VM.

```env
AGENT_TC_PRACTICE_MDS_PATHS=C:\...\Practice Base Unificada.mds;C:\...\Practice Bases Individuais.mds
```

Caminhos dos `.mds` usados por Practice.

Observacao:

- pode ter mais de um caminho separado por ponto e virgula.

De onde vem:

- instalacao local do projeto Practice na VM.

```env
AGENT_TC_PRACTICE_PROJECT_SUITE=C:\TC\TC12 - Simplificado\TestesVisualPractice.pjs
```

Caminho do ProjectSuite `.pjs` usado para Practice.

De onde vem:

- instalacao local do TestComplete/Practice.

```env
AGENT_TC_SUPREMA_MDS_PATH=C:\TC\tc12\PROJETO-TC12\Integracoes\Integracoes.mds
```

Caminho do `.mds` usado para Suprema.

De onde vem:

- instalacao local do projeto Suprema.

```env
AGENT_TC_TIMES_FOLDER=C:\Tempos TC
```

Pasta opcional com arquivos de tempo/performance dos testes.

De onde vem:

- se a VM gera essa pasta, apontar para ela.
- se nao existir, pode ficar vazio.

```env
AGENT_TC_SYSTEM=practice
```

Opcional. Forca o sistema da rodagem.

Valores comuns:

```text
practice
suprema
```

Na maioria dos casos, o script tenta inferir sozinho pelo nome/caminho da
rodagem.

### Como o PythonRodagem funciona

1. A VM termina uma rodagem.
2. O `.bat` e executado.
3. O script acha a pasta da rodagem.
4. Ele identifica sistema, versao e modulo.
5. Ele cruza dados dos compactados com os `.mds`.
6. Ele gera um payload.
7. Ele envia o payload e as evidencias para a API.

### Manutencao mensal

Tambem existe:

```text
PythonRodagem\run_agent_tc_maintenance.bat
```

Ele executa a limpeza de versoes antigas.

Variaveis usadas:

```env
AGENT_TC_BACKEND=postgres
AGENT_TC_RETENTION_DAYS=30
AGENT_TC_LOG_DIR=S:\Teste automatico\Arquivos\AgenteTC\logs
```

Uso:

- deve ser agendado na D01 ou em uma maquina que consiga acessar o banco.
- remove versoes que nao rodam ha mais de X dias.

## 3. JenkinsBridge

O JenkinsBridge e a ponte entre o dashboard e o Jenkins.

Ele existe porque o dashboard nao chama o Jenkins diretamente.

Fluxo:

1. Usuario pede uma rerodagem no dashboard.
2. Dashboard envia o pedido para a API.
3. JenkinsBridge consulta a API.
4. JenkinsBridge encontra pedidos pendentes.
5. JenkinsBridge chama o Jenkins.
6. JenkinsBridge atualiza o status na API.

Status que ele acompanha:

- solicitado;
- enviado ao Jenkins;
- na fila;
- rodando;
- cancelando;
- cancelado;
- finalizado.

### Onde fica

```text
JenkinsBridge\
```

Script principal:

```text
JenkinsBridge\jenkins_bridge.py
```

Configuracao:

```text
JenkinsBridge\.env
```

### Variaveis do JenkinsBridge

```env
JENKINS_URL=http://IP_DO_JENKINS:11000
```

URL base do Jenkins.

De onde vem:

- endereco onde o Jenkins esta hospedado.
- exemplo atual: VM/host do Jenkins.

```env
JENKINS_JOB_PATH=/view/Progvisual20/job/testepipeline/buildWithParameters
```

Caminho do job/pipeline que inicia a rodagem.

De onde vem:

- URL do job no Jenkins.
- normalmente e a parte da URL depois do dominio/porta.

Exemplo:

Se a URL completa for:

```text
http://192.168.9.201:11000/view/Progvisual20/job/testepipeline/buildWithParameters
```

Entao:

```env
JENKINS_URL=http://192.168.9.201:11000
JENKINS_JOB_PATH=/view/Progvisual20/job/testepipeline/buildWithParameters
```

```env
JENKINS_USER=usuario_jenkins
```

Usuario usado para automatizar chamadas ao Jenkins.

De onde vem:

- usuario criado ou escolhido no Jenkins.

```env
JENKINS_API_TOKEN=token_jenkins
```

Token do usuario Jenkins.

De onde vem:

- gerado dentro do Jenkins, nas configuracoes do usuario.
- nao e o mesmo token da API.

```env
JENKINS_BRIDGE_BACKEND=api
```

Define que o Bridge fala com a API.

Valor atual:

```text
api
```

```env
AGENT_TC_API_URL=http://127.0.0.1:8000
```

URL da API para o Bridge.

De onde vem:

- se Bridge e API rodam na mesma D01, pode ser `http://127.0.0.1:8000`.
- se estiverem em maquinas diferentes, usar IP/DNS da API.

```env
AGENT_TC_BRIDGE_TOKEN=mesmo_token_da_api
```

Token usado para a API aceitar chamadas do Bridge.

De onde vem:

- mesmo valor do `AGENT_TC_BRIDGE_TOKEN` no `docker.env`.

### Como o JenkinsBridge funciona

O JenkinsBridge roda em loop.

A cada alguns segundos, ele:

1. pergunta para a API se existe pedido novo;
2. se existir, envia para o Jenkins;
3. consulta fila/build do Jenkins;
4. atualiza a API com o status mais recente.

## 4. Dashboard

O dashboard e a interface visual usada pelas pessoas.

Ele mostra:

- rodagens;
- falhas;
- evidencias;
- agrupamentos;
- performance;
- historico;
- pedidos de rerodagem;
- tela de usuarios/admin.

O dashboard nao fala com banco, storage ou Jenkins diretamente.

Ele fala somente com a API.

### Onde fica

Projeto:

```text
tczinho
```

Configuracao:

```text
.env
```

### Variaveis do dashboard

```env
VITE_DATA_PROVIDER=api
```

Define que o dashboard usa a API como fonte de dados.

Valor atual:

```text
api
```

```env
VITE_AGENT_TC_API_URL=http://IP_DA_D01:8000
```

URL da API central.

De onde vem:

- infra deve informar o IP/DNS/HTTPS oficial da API.

Exemplos:

```text
http://192.168.9.201:8000
https://agent-tc.sci.com.br
```

### Como o dashboard usa essa URL

O dashboard chama endpoints da API, por exemplo:

```text
GET /runs
GET /runs/{id}
GET /runs/{id}/failures
POST /auth/login
POST /rerun-requests
POST /runs/{id}/ai-group
```

O login tambem passa pela API.

Quando o usuario faz login:

1. Dashboard envia usuario e senha para `/auth/login`.
2. API valida no PostgreSQL.
3. API retorna um token local.
4. Dashboard guarda esse token no navegador.
5. Dashboard usa esse token nas chamadas protegidas.

### O que a infra/web precisa fornecer

Para finalizar o ambiente hospedado, precisamos receber:

- URL final da API.
- URL final do dashboard.
- Confirmacao se a API sera acessada por HTTP ou HTTPS.
- Porta exposta da API, se nao houver HTTPS.
- Politica de acesso externo: quem pode acessar fora da rede SCI.
- Local de backup do PostgreSQL.
- Responsavel por guardar/rotacionar senhas e tokens.
- Confirmacao de onde o volume de evidencias ficara salvo.

## Resumo para explicar em uma frase

O Agent TC agora funciona assim:

```text
As VMs processam a rodagem, enviam tudo para uma API central na D01,
a API grava no PostgreSQL e salva arquivos locais, o dashboard consulta a API,
e o JenkinsBridge usa a API para transformar pedidos do dashboard em rodagem no Jenkins.
```


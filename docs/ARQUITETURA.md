# Arquitetura e funcionamento

Estado documentado em 26/10/2026. Os enderecos abaixo descrevem a implantacao
temporaria na D01; confirme mudancas de rede e DNS com a equipe responsavel.

## Objetivo

O Agent TC centraliza os resultados dos testes automatizados. A parte Python
faz a leitura deterministica dos arquivos de rodagem. A API armazena dados e
arquivos, oferece autenticacao e entrega informacoes ao dashboard. A IA e usada
para agrupar falhas, nao para substituir a leitura dos logs. Tres processos na
D01 (os "Bridges") executam o que o dashboard pede e a API nao pode fazer
sozinha: o JenkinsBridge transforma pedidos em builds do Jenkins, o
RegravacaoBridge grava bases no SVN e importa rodagens, e o MergeBridge faz
merge entre branches do projeto do TC.

## Componentes

| Componente | Onde roda | Funcao |
| --- | --- | --- |
| PythonRodagem | VMs que executam testes | Le compactados, logs, MDS/PJS e tempos; envia payload e evidencias. |
| `agent-tc-api` | Docker na D01 | Recebe ingestao, atende o dashboard, autentica usuarios e grava dados. |
| `agent-tc-postgres` | Docker na D01 | Guarda rodagens, falhas, metadados, usuarios, sessoes e pedidos ao Jenkins. |
| Volume de evidencias | Docker na D01 | Guarda o conteudo dos arquivos enviados pela ingestao. |
| `agent-tc-web` | Docker na D01 | Caddy local que serve o dashboard e encaminha chamadas para a API. |
| JenkinsBridge | Processo Python na D01 | Consulta pedidos na API, chama o Jenkins e atualiza estados. |
| RegravacaoBridge | Processo Python na D01 | Regrava bases (`Stores\Files`) no SVN e importa rodagens pedidas no dashboard. |
| MergeBridge | Processo Python na D01 | Faz merge entre branches do projeto do TC e publica a lista de branches. |
| Vigia Docker | Tarefa agendada na D01 | Reabre o Docker Desktop, reinicia container travado e faz a limpeza diaria. |
| Jenkins | Container existente na D01 | Executa os jobs automatizados. |

O dashboard vem do repositorio `tczinho`, separado do codigo Python. O Caddy
usa a imagem `caddy:2-alpine` dentro da D01; DuckDNS fornece somente o nome
DNS. O agrupamento configurado atualmente usa Gemini, um servico externo.

## Fluxo de uma rodagem

1. O TestComplete termina uma execucao na VM e produz compactados, logs e
   possivelmente dados de tempo.
2. `PythonRodagem/run_agent_tc_python.bat` escolhe a pasta da rodagem, le os
   arquivos, cruza os casos com MDS/PJS e monta o payload.
3. A VM envia o payload e as evidencias para `POST /ingest` na D01 com o token
   tecnico. A VM nao acessa o PostgreSQL diretamente.
4. A API grava linhas no PostgreSQL e copia os arquivos para o volume de
   evidencias. O banco guarda metadados e caminhos, nao o conteudo dos arquivos.
5. O dashboard consulta a API para exibir resumo, falhas, evidencias, historico
   e performance. Ao abrir um arquivo, ele usa uma chamada autenticada a
   `/api/files/...`.
6. Quando solicitado, a API envia um resumo para o provedor de IA configurado
   e grava os agrupamentos retornados no PostgreSQL.

O payload tambem leva a origem SVN da rodagem (`svn info` da copia de trabalho
do projeto na VM: URL, raiz e revisao), usada pela regravacao; os tempos de
cada caso lidos do `<Sistema> Tempo CT.csv` (aba Performance); e, no Unico, a
lista de casos que o `mod_xml.py` desmarca em toda rodagem (aparecem riscados
na aba Casos de teste).

### Importar rodagem pelo dashboard

Quando a VM nao enviou a rodagem, o botao "Adicionar rodagem" (visao geral e
cabecalho do modulo) pede a importacao pela pasta do log compactado. O
RegravacaoBridge roda o mesmo `run_agent_tc_python.bat` na D01. Como a copia do
projeto na D01 esta sempre na principal, o `svn info` dela nao diz a branch da
VM: a pessoa escolhe a branch no pedido (padrao: a principal) e o Bridge a
repassa ao pipeline pela variavel `AGENT_TC_BRANCH_URL`, que substitui a URL do
`svn info` (a revisao fica vazia).

## Fluxo de uma regravacao de bases

1. Quem tem a permissao "regravar" escolhe, na rodagem, as diferencas a
   regravar (na branch da rodagem ou em outra da lista do SVN).
2. A API grava o pedido; o RegravacaoBridge o pega (a cada 15 segundos).
3. Para cada arquivo, o Bridge baixa o `_Atual` das evidencias, confere o hash
   e confere se a base no SVN ainda e a da rodagem. Tudo conferido, grava os
   arquivos num unico commit (`svnmucc`). Qualquer divergencia vira conflito e
   nada e gravado.
4. Com `SVN_COMMIT` diferente de `1`, o Bridge so simula.

## Fluxo de um merge entre branches

1. Quem tem a permissao "merge" escolhe origem e destino na lista de branches,
   que o MergeBridge publica na API a cada 2 minutos (`svn ls`).
2. O MergeBridge, com uma copia de trabalho propria (`MERGE_WC_DIR`), troca para
   o destino, faz o merge e, sem conflito, o commit. Com conflito desfaz tudo.
3. Com `MERGE_COMMIT` diferente de `1`, o Bridge so simula.

## Fluxo de uma solicitacao ao Jenkins

1. Um usuario aprovado cria ou cancela um pedido no dashboard.
2. O dashboard chama `POST /api/rerun-requests` ou a rota de cancelamento.
3. O JenkinsBridge consulta a API a cada 10 segundos com um token tecnico.
4. O Bridge envia `CONFIG_JSON` ao job Jenkins, acompanha fila/build e atualiza
   o pedido na API. O dashboard mostra o estado salvo no PostgreSQL.

O navegador nao recebe o token do Jenkins e nao chama o Jenkins diretamente.

## Enderecos e portas atuais

| Origem | Endereco | Destino |
| --- | --- | --- |
| Navegador na rede SCI | `http://192.168.9.201:8000` | Dashboard/Caddy na D01. |
| Navegador externo | `http://SEU_SUBDOMINIO.duckdns.org:10443` | NAT TCP `10443` -> D01 `8000`. |
| Dashboard | `/api/*` no mesmo endereco | Caddy remove `/api` e repassa a API. |
| JenkinsBridge na D01 | `http://127.0.0.1:8000` | Caddy repassa rotas tecnicas a API. |
| RegravacaoBridge e MergeBridge | `http://127.0.0.1:8001` | Direto na API (padrao do `.env`). |
| PythonRodagem nas VMs | `http://192.168.9.201:8000` | Caddy repassa `/ingest` a API. |
| API no host D01 | `http://127.0.0.1:8001` | Container `agent-tc-api`, porta interna `8000`. |
| API -> banco | `postgres:5432` | Rede Docker privada do Compose da API. |

O Compose publica `8000` e `8001` no host. A regra NAT conhecida encaminha
somente a porta externa `10443` para `8000`; o alcance de `8001` pela rede
interna depende das regras de firewall da D01. O gateway tambem encaminha as
rotas tecnicas `/ingest` e `/bridge/*`, protegidas pelo token tecnico na API.
Dentro da SCI, use o IP interno se o DNS publico nao funcionar por falta de
NAT loopback.

## Autenticacao e limites do acesso

Usuarios e sessoes ficam no PostgreSQL local. O primeiro cadastro da instalacao
vira administrador aprovado; cadastros seguintes aguardam aprovacao. O login
retorna um token de sessao, armazenado pelo dashboard no navegador e enviado no
cabecalho `Authorization: Bearer`. A duracao configurada na D01 e 24 horas;
apos expirar, e necessario entrar novamente. As rotas de dados e evidencias
exigem sessao valida. Cadastro/login e `/health` sao publicos. As operacoes do
Bridge e a ingestao exigem `AGENT_TC_BRIDGE_TOKEN`.

Alem do papel `admin`/`user`, cada usuario tem:

- **Permissoes** (coluna `permissions`): `rodagem` (pedidos ao Jenkins,
  pre-definicoes e importar rodagem), `merge` e `regravar` (bases no SVN e
  tempo base dos casos). Lista vazia = so ver. O admin pode tudo.
- **Modulos** (coluna `modules`): slugs como `folha` e `fiscal`, ou `*` para
  todos. Cada um so ve rodagens, falhas, evidencias e pedidos dos seus modulos,
  e as acoes de rodagem e de regravacao so valem neles.

As permissoes sao conferidas na API (`agent_tc_core/permissions.py`); o
dashboard so esconde o que a pessoa nao pode usar.

O acesso externo atual usa HTTP. O trafego, inclusive login e tokens, nao e
criptografado. HTTPS e backup regular de banco/evidencias sao requisitos para a
implantacao definitiva. DuckDNS e o provedor de IA sao dependencias externas;
o dashboard, a API, o banco, os arquivos e o Caddy rodam na D01.

## Hospedagem definitiva

O host futuro precisa servir o dashboard, encaminhar `/api/*` para a API e
preservar `Authorization`. O PostgreSQL e as evidencias podem continuar na D01
se a API tiver conectividade apropriada, ou ser migrados em etapa separada.
Documente novas URLs, limites de acesso, certificados, backups e responsaveis
antes da troca do endereco usado pelos usuarios.

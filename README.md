# Agent TC

O Agent TC transforma os resultados das rodadas automatizadas do TestComplete em
dados consultaveis. Ele le compactados e projetos MDS/PJS, registra rodagens,
falhas, evidencias e tempos, permite agrupar falhas com IA e envia solicitacoes
de novas execucoes ao Jenkins.

## Como o sistema esta distribuido

```text
Usuarios -> dashboard/Caddy -> API -> PostgreSQL + volume de evidencias
VMs de rodagem -> PythonRodagem -> Caddy -> API
JenkinsBridge -> Caddy -> API
JenkinsBridge -> Jenkins
```

Na D01, o dashboard e a entrada publica na porta `8000`. A API usa a porta
`8001` do host; o PostgreSQL usa apenas a rede Docker. O acesso externo
temporario e `http://SEU_SUBDOMINIO.duckdns.org:10443`, por NAT para a porta `8000`
da D01. O acesso interno e `http://192.168.9.201:8000`. O dashboard e mantido
em outro repositorio, `tczinho`, e publicado na D01 pelo container `agent-tc-web`.

## Pastas

| Pasta | Responsabilidade |
| --- | --- |
| `API/` | Imagem Docker, Compose e entrada da API central. |
| `PythonRodagem/` | Leitura das rodadas, modelo de dados, API Python, migrations e testes. |
| `JenkinsBridge/` | Consulta pedidos na API, aciona e acompanha o Jenkins. |
| `docs/` | Arquitetura, configuracao, operacao e modelo de dados. |

Os atalhos na raiz encaminham para os scripts das subpastas e existem para
compatibilidade com chamadas antigas. O codigo da API fica em
`PythonRodagem/agent_tc_core`; a pasta `API/` nao duplica esse pacote.

## Onde comecar

- [Arquitetura e funcionamento](docs/ARQUITETURA.md): objetivos, fluxos e hospedagem.
- [Configuracao](docs/CONFIGURACAO.md): os quatro arquivos `.env` e seus valores.
- [Operacao da D01](docs/OPERACAO_D01.md): verificacao, atualizacao e manutencao.
- [Modelo de dados](docs/CANONICAL_DATA_MODEL.md): tabelas e migrations.
- [Deploy da API](API/deploy/d01/DEPLOY_D01_DOCKER.md): referencia curta para o Compose da API.

`C:\TC\Util Compartilhado\AgenteTC` e uma copia operacional do repositorio
`agent-tc-api`, nao um clone Git. Segredos reais ficam nos arquivos `.env`
locais e nao devem entrar no repositorio. O dashboard tem seu proprio Git e
deploy em `C:\TC\AgentTC-Dashboard` na D01.

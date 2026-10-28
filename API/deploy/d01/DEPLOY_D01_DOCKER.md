# API e PostgreSQL na D01

Este Compose cria `agent-tc-api` e `agent-tc-postgres`. Ele usa dois volumes
persistentes: `agent-tc-postgres-data` para o banco e `agent-tc-evidencias`
para os arquivos. O dashboard/Caddy pertence ao repositorio separado
`tczinho` e ocupa a porta `8000` do host; a API ocupa `8001`.

## Arquivos

- `docker-compose.d01.yml`: servicos, portas, volumes e healthchecks.
- `docker.env`: configuracao real e segredos da D01, fora do Git.
- `docker.env.example`: modelo sem segredos, alinhado ao arranjo atual.
- `API/Dockerfile`: imagem Python da API, que incorpora o codigo de
  `PythonRodagem/agent_tc_core` e suas migrations.

O banco usa a porta `5432` somente na rede Docker. A API usa `postgres:5432`
internamente e publica a porta `8001` no host. O Caddy recebe chamadas na
`8000` e encaminha as rotas da API. A NAT externa `10443` chega a `8000`.

## Configuracao

Na instalacao inicial, copie `docker.env.example` para `docker.env` e preencha
`POSTGRES_PASSWORD`, `AGENT_TC_BRIDGE_TOKEN` e a chave do provedor de IA.
Os valores atuais de endereco e porta sao:

```env
AGENT_TC_API_PORT=8001
AGENT_TC_PUBLIC_BASE_URL=http://SEU_SUBDOMINIO.duckdns.org:10443/api/files
AI_PROVIDER=gemini
```

O Compose define `AGENT_TC_BACKEND=postgres`, `AGENT_TC_STORAGE=local`,
`AGENT_TC_STORAGE_ROOT=/data/evidencias` e `AGENT_TC_AUTH_BACKEND=local`.
Consulte [Configuracao](../../../docs/CONFIGURACAO.md) para cada variavel.

## Atualizar sem trocar os volumes

`C:\TC\Util Compartilhado\AgenteTC` e uma copia operacional. Depois de
sincronizar o codigo versionado para essa pasta na D01, descubra o projeto
Compose que ja possui os containers:

```powershell
cd "C:\TC\Util Compartilhado\AgenteTC\API\deploy\d01"
$agentTcProject = ((docker inspect agent-tc-api | ConvertFrom-Json)[0].Config.Labels).'com.docker.compose.project'
$agentTcProject
docker compose -p $agentTcProject --env-file docker.env -f docker-compose.d01.yml up -d --build
```

O nome retornado deve ser reutilizado nas atualizacoes. Para validar:

```powershell
docker ps --filter "name=agent-tc"
irm http://127.0.0.1:8001/health
irm http://127.0.0.1:8000/health
```

O segundo `/health` testa tambem o encaminhamento pelo Caddy. As rotas de
dados e evidencias exigem sessao de usuario; `/ingest` e `/bridge/*` usam o
token tecnico. Nunca inclua `-v` em um `docker compose down` desse ambiente.

Para operacao diaria, evidencias, manutencao e backup, consulte
[Operacao da D01](../../../docs/OPERACAO_D01.md).

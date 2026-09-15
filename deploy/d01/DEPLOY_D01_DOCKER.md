# Deploy Agent TC na D01

Este guia prepara a D01 para hospedar a API do Agent TC e um PostgreSQL local em Docker, sem mexer no container atual do Jenkins.

## Arquivos

- `Dockerfile`: imagem da API.
- `docker-compose.d01.yml`: sobe API + PostgreSQL + volumes.
- `docker.env.example`: modelo de variaveis sem segredos.
- `docker.env`: arquivo real da D01, criado manualmente a partir do exemplo.

## Preparar variaveis

Na D01, dentro da pasta `C:\TC\Util Compartilhado\AgenteTC\deploy\d01`:

```powershell
copy docker.env.example docker.env
notepad docker.env
```

Preencher pelo menos:

```env
POSTGRES_PASSWORD=uma_senha_forte
AGENT_TC_PUBLIC_BASE_URL=http://IP_DA_D01:8000/files
AGENT_TC_BRIDGE_TOKEN=um_token_interno_forte
```

Evite caracteres especiais de URL na senha do Postgres, como `@`, `:`, `/`, `?`, `#` e `&`. Se a infra exigir esses caracteres, a senha precisa ir URL-encoded dentro de `POSTGRES_DSN`.

Se a API tiver reverse proxy/HTTPS, usar a URL final:

```env
AGENT_TC_PUBLIC_BASE_URL=https://agent-tc.sci.../files
```

## Subir

```powershell
docker compose --env-file docker.env -f docker-compose.d01.yml up -d --build
```

Ou use o atalho:

```powershell
.\run_agent_tc_docker_up.bat
```

## Validar

```powershell
docker ps
docker logs agent-tc-api --tail 80
docker logs agent-tc-postgres --tail 80
```

Para acompanhar os logs em tempo real:

```powershell
.\run_agent_tc_docker_logs.bat
```

Endpoints esperados:

```text
http://IP_DA_D01:8000/health
http://IP_DA_D01:8000/modules
http://IP_DA_D01:8000/runs
```

## Bridge

Quando o dashboard apontar para a API da D01, o `JenkinsBridge\.env` deve usar:

```env
JENKINS_BRIDGE_BACKEND=api
AGENT_TC_API_URL=http://IP_DA_D01:8000
```

Se `AGENT_TC_BRIDGE_TOKEN` for configurado no `docker.env`, repetir o mesmo valor no `JenkinsBridge\.env`.

## Auth local

A API possui endpoints de Auth local gravando no PostgreSQL da D01:

```text
POST /auth/register
POST /auth/login
POST /auth/logout
GET  /auth/me
GET  /auth/users
PATCH /auth/users/{id}
```

O primeiro usuario cadastrado vira `admin` e ja fica aprovado. Os proximos cadastros entram como `pending` ate um admin aprovar.

Opcionalmente ajuste a duracao da sessao no `docker.env`:

```env
AGENT_TC_AUTH_BACKEND=local
AGENT_TC_AUTH_SESSION_HOURS=24
```

## Agrupamento por IA

O agrupamento usa o Auth local da API. O dashboard envia o token obtido em `/auth/login`; a API valida esse token antes de executar o agrupamento.

Para habilitar agrupamento, preencher no `docker.env`:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini
```

Se for usar Gemini em vez de OpenAI:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.5-flash
```

Depois de alterar somente variaveis do `docker.env`, recriar a API para ela carregar os novos valores:

```powershell
docker compose --env-file docker.env -f docker-compose.d01.yml up -d --force-recreate --no-deps api
```

## Observacoes

- O dashboard deve falar com a API, nunca direto com o PostgreSQL.
- O PostgreSQL do compose fica acessivel para a API pelo hostname interno `postgres`.
- A porta publicada da API vem de `AGENT_TC_API_PORT`, por padrao `8000`.
- Os arquivos de evidencia ficam no volume Docker `agent-tc-evidencias` e sao servidos pela API em `/files/...`.
- O volume `agent-tc-postgres-data` guarda os dados do banco; backup precisa ser definido pela infra.
- Esta pasta `deploy\d01` precisa ficar dentro da pasta do projeto `AgenteTC`, porque o compose usa `..\..` como contexto de build para encontrar `Dockerfile`, `agent_tc_core`, `cli` e `database`.
- Para parar os containers sem apagar os volumes:

```powershell
.\run_agent_tc_docker_down.bat
```

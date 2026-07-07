# API publica do Agent TC

Objetivo:

- A VM do Bridge/processamento apenas envia dados ao banco.
- O dashboard Loveable acessa uma API publica HTTPS.
- A API publica le o banco atual, hoje Supabase, e depois pode trocar para outro adapter.

## Fluxo correto

```text
VMs/TestComplete -> run_agent_tc_python.bat -> Banco
Dashboard Loveable -> API publica -> Banco
```

A VM do Bridge nao precisa ser hospedagem publica.

## Onde hospedar

Use um servico que publique Docker/HTTP com HTTPS, por exemplo:

- Render
- Railway
- Fly.io
- VPS propria

Para o MVP, Render ou Railway e o caminho mais rapido.

## Variaveis de ambiente da API publica

Configure no painel do host:

```text
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_BUCKET=...
SUPABASE_SCHEMA=public
SUPABASE_TABLE_PREFIX=agent_tc_
```

Nao coloque `SUPABASE_SERVICE_ROLE_KEY` no Loveable ou no frontend.

## Comando de start sem Docker

Se o host usar comando Python direto:

```bash
python cli/agent_tc_api.py --backend supabase --host 0.0.0.0 --port $PORT --read-only
```

Se o host nao definir `PORT`, use:

```bash
python cli/agent_tc_api.py --backend supabase --host 0.0.0.0 --port 8000 --read-only
```

## Docker

Esta pasta ja possui `Dockerfile`.

Build local:

```bash
docker build -t agent-tc-api .
```

Run local:

```bash
docker run --rm -p 8000:8000 ^
  -e SUPABASE_URL=... ^
  -e SUPABASE_SERVICE_ROLE_KEY=... ^
  -e SUPABASE_BUCKET=... ^
  agent-tc-api
```

Teste:

```bash
curl http://127.0.0.1:8000/health
```

## Configuracao no Loveable

Depois que o host publico gerar a URL, use:

```text
VITE_DATA_PROVIDER=api
VITE_AGENT_TC_API_URL=https://SUA-API-PUBLICA
```

Nao use `192.168.x.x` em producao. Esse IP so funciona dentro da rede local.

## Seguranca

O deploy publico deve usar `--read-only`.

Isso bloqueia:

- `POST /analyze`

Os endpoints `GET` continuam funcionando para o dashboard.

O endpoint `POST /rerun-requests` continua liberado para o dashboard solicitar rodagens ao JenkinsBridge.

Se futuramente esse endpoint precisar ficar restrito por usuario, devemos adicionar autenticacao antes do insert.

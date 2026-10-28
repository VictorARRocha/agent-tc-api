# Operacao da D01

Este guia usa a implantacao atual: `agent-tc-web` na porta `8000`,
`agent-tc-api` na `8001`, `agent-tc-postgres` na rede Docker e Jenkins em seu
container existente. JenkinsBridge, RegravacaoBridge e MergeBridge sao
processos Python separados do Compose. Execute os comandos Docker em um
PowerShell da D01.

Ha dois projetos Compose: `agenttc_test` (API e PostgreSQL, os dados de
producao) e `d01` (dashboard). O nome do projeto define o nome dos volumes:
use sempre o mesmo nome ao atualizar.

## Verificar os servicos

```powershell
docker version
docker ps
irm http://127.0.0.1:8000/health
irm http://127.0.0.1:8001/health
```

`/health` deve retornar `ok: true`. A primeira URL passa pelo Caddy; a segunda
fala diretamente com a API. Uma chamada sem sessao a `/runs` deve retornar
`401`, o que confirma a exigencia de login. No navegador, teste login, rodagem,
evidencia, agrupamento e pedido ao Jenkins. Teste o endereco DuckDNS a partir
de uma rede externa, por exemplo 5G; dentro da SCI, use o IP da D01.

O Bridge grava em `JenkinsBridge/logs/jenkins_bridge.log`, com rotacao a cada
10 MB e ate 5 arquivos antigos (`.log.1` a `.log.5`). Mensagens repetidas so
sao registradas quando o estado muda. Saidas inesperadas do Python, como um
traceback ao iniciar pelo `.bat`, vao para `logs/jenkins_bridge_console.log`.
O Bridge consulta a API a cada 10 segundos e o job so recebe pedidos enquanto
esse processo estiver ativo. Verifique separadamente o estado do container
Jenkins e seu endereco definido em `JENKINS_URL`.

RegravacaoBridge e MergeBridge seguem o mesmo padrao, cada um na sua pasta:
`logs/regravacao_bridge.log` e `logs/merge_bridge.log` (mesma rotacao), e a
saida inesperada do Python em `logs/regravacao_bridge_console.log` e
`logs/merge_bridge_console.log`. Ambos consultam a API a cada 15 segundos.

### Bridges como tarefas agendadas

Na D01, cada Bridge roda pela sua tarefa, do usuario que mantem o Docker
Desktop aberto. Ela inicia no logon e verifica a cada 5 minutos se o processo
esta ativo; se tiver caido, inicia de novo.

| Bridge | Tarefa | Instalador |
| --- | --- | --- |
| JenkinsBridge | `AgenteTC - JenkinsBridge` | `JenkinsBridge\instalar_tarefa_bridge.ps1` |
| RegravacaoBridge | `AgenteTC - RegravacaoBridge` | `RegravacaoBridge\instalar_tarefa_regravacao.ps1` |
| MergeBridge | `AgenteTC - MergeBridge` | `MergeBridge\instalar_tarefa_merge.ps1` |

Para instalar ou reinstalar, encerre antes qualquer Bridge iniciado
manualmente. Exemplo com o JenkinsBridge:

```powershell
cd "C:\TC\Util Compartilhado\AgenteTC\JenkinsBridge"
powershell -ExecutionPolicy Bypass -File .\instalar_tarefa_bridge.ps1
Get-ScheduledTask -TaskName 'AgenteTC - JenkinsBridge' | Get-ScheduledTaskInfo
```

`-Remover` apaga a tarefa. Com a tarefa instalada, nao rode tambem o `.bat` ou
o `.vbs` do Bridge: seriam dois Bridges ao mesmo tempo. Depois de alterar o
`.env` ou o codigo de um Bridge, reinicie a tarefa dele:

```powershell
Stop-ScheduledTask -TaskName "AgenteTC - RegravacaoBridge"
Start-ScheduledTask -TaskName "AgenteTC - RegravacaoBridge"
Get-Content "C:\TC\Util Compartilhado\AgenteTC\RegravacaoBridge\logs\regravacao_bridge.log" -Tail 5
```

A unidade `S:` precisa existir na sessao da tarefa do RegravacaoBridge (as
importacoes leem `ArquivosCompactados` por ela).

## Onde ficam dados e arquivos

- O PostgreSQL guarda os registros estruturados e as sessoes de usuarios no
  volume logico `agent-tc-postgres-data`.
- Os arquivos de evidencia ficam no volume logico `agent-tc-evidencias`, montado
  em `/data/evidencias` no container da API.
- O dashboard e composto por arquivos estaticos dentro da imagem
  `agent-tc-web:local`. Seu volume `agent-tc-caddy-config` guarda configuracao
  do Caddy, nao rodagens.
- Logs do Bridge e da pos-rodagem ficam nas respectivas pastas configuradas;
  nao substituem backup do PostgreSQL ou das evidencias.

O nome fisico de um volume Docker recebe o prefixo do projeto Compose. Para
descobrir os volumes reais sem exibir variaveis sensiveis do container:

```powershell
(docker inspect agent-tc-postgres | ConvertFrom-Json)[0].Mounts | Select-Object Name,Destination
(docker inspect agent-tc-api | ConvertFrom-Json)[0].Mounts | Select-Object Name,Destination
docker volume ls
```

Os dados vivem no disco virtual usado pelo Docker Desktop na D01. O backup
cobre o dump do PostgreSQL e o volume de evidencias; os arquivos `.env` reais
precisam de guarda separada e protegida e nao entram nele.

## Backup e restauracao

`API/deploy/d01/backup_agent_tc.ps1` cria, a cada execucao, uma pasta
`agent_tc_AAAAMMDD_HHMMSS` no destino com `agent_tc.dump` (validado com
`pg_restore --list`), `evidencias.tar.gz` e `manifesto.txt`. O destino padrao
e `S:\Teste automatico\Arquivos\AgenteTC\backups`; pastas com mais de 14 dias
sao removidas. Um backup que falha fica como `FALHOU_agent_tc_...` e o motivo
vai para `backup_agent_tc.log` no destino. Se o proprio destino estiver
indisponivel, o erro fica em `API/deploy/d01/backup_agent_tc_erro.log`.

```powershell
cd "C:\TC\Util Compartilhado\AgenteTC\API\deploy\d01"
docker exec agent-tc-api du -sh /data/evidencias
powershell -ExecutionPolicy Bypass -File .\backup_agent_tc.ps1
powershell -ExecutionPolicy Bypass -File .\backup_agent_tc.ps1 -InstalarTarefa -Horario 02:00
```

O primeiro comando mostra o tamanho das evidencias, que define o espaco
necessario: cada backup guarda uma copia completa. O terceiro registra a
tarefa diaria `AgenteTC - Backup diario` no usuario atual. Se a unidade `S:`
nao estiver disponivel na sessao da tarefa, use o caminho UNC em `-Destino`.

Para restaurar, com a API parada e um backup escolhido em `$b`:

```powershell
docker stop agent-tc-api
docker cp "$b\agent_tc.dump" agent-tc-postgres:/tmp/restore.dump
docker exec agent-tc-postgres pg_restore -U agent_tc -d agent_tc --clean --if-exists /tmp/restore.dump
docker cp "$b\evidencias.tar.gz" agent-tc-api:/tmp/evidencias.tar.gz
docker start agent-tc-api
docker exec agent-tc-api sh -c "rm -rf /data/evidencias/* && tar -xzf /tmp/evidencias.tar.gz -C /data && rm /tmp/evidencias.tar.gz"
```

`--clean` substitui o conteudo atual do banco. Teste a restauracao
periodicamente, de preferencia em outro ambiente.

## Atualizar a API

O codigo da API, do PythonRodagem e dos Bridges e versionado no SVN, em
`C:\TC\Util Compartilhado\AgenteTC`. O Util Compartilhado nao tem branches: um
commit vale na hora para todas as VMs. Arquivos `.env` e `docker.env` nunca vao
para o SVN. Depois do commit, na D01:

```powershell
cd "C:\TC\Util Compartilhado\AgenteTC"
& "C:\Program Files\TortoiseSVN\bin\svn.exe" update

cd "C:\TC\Util Compartilhado\AgenteTC\API\deploy\d01"
$agentTcProject = ((docker inspect agent-tc-api | ConvertFrom-Json)[0].Config.Labels).'com.docker.compose.project'
$agentTcProject
```

So continue se aparecer `agenttc_test`.

```powershell
docker compose -p $agentTcProject --env-file docker.env -f docker-compose.d01.yml up -d --build
irm http://127.0.0.1:8001/health
```

Use o mesmo nome de projeto retornado pelo container atual para reutilizar os
volumes existentes. Nao use `down -v`, `docker volume rm`, limpeza de dados ou
reset de fabrica para uma atualizacao comum. A API aplica sozinha as migrations
novas ao iniciar; o `/health` pode recusar a conexao nos primeiros segundos,
espere um pouco e repita. Se o codigo de um Bridge mudou, reinicie a tarefa
dele. Mudancas apenas no `docker.env` exigem recriar a API para entrar em vigor.

## Atualizar o dashboard

O codigo do dashboard esta no repositorio `tczinho`, com o checkout da D01 em
`C:\TC\AgentTC-Dashboard`. As mudancas chegam em pacotes na pasta
`C:\TC\Util Compartilhado\AgenteTC-Atualizacoes\<pacote>`, com:

- `originais\` e `novos\`: cada arquivo como esta em producao e como fica;
- `aplicar.ps1`: confere se os `originais` batem com o checkout (se nao
  baterem, para sem mexer em nada), cria uma branch com o nome do pacote e
  copia os `novos`. Sem `-Aplicar`, so confere;
- `testar_em_paralelo.ps1`: sobe uma copia de teste na porta `8002`
  (`-Remover` apaga).

```powershell
cd "C:\TC\AgentTC-Dashboard"
$principal = (git rev-parse --abbrev-ref HEAD).Trim()

cd "C:\TC\Util Compartilhado\AgenteTC-Atualizacoes\<pacote>"
powershell -ExecutionPolicy Bypass -File .\aplicar.ps1 -Aplicar
cd "C:\TC\AgentTC-Dashboard"
git commit -m "<descricao>"
git checkout $principal
git merge --ff-only <pacote>
git push
cd deploy\d01
.\run_agent_tc_public_up.bat
```

Se o `aplicar.ps1` acusar arquivo "diferente da versao em producao" ou o
`merge --ff-only` recusar, nao force: confira o que mudou no checkout. Depois
de publicado, a pasta do pacote pode ser apagada.

O build define `VITE_AGENT_TC_API_URL=/api`; o arquivo `web.env` define apenas
a porta HTTP `8000`. O Caddy encaminha `/api/*` para a API na `8001` e conserva
as rotas antigas na raiz para PythonRodagem e JenkinsBridge. O `npm run build`
leva menos de 1 minuto; se ficar parado em "transforming", o Docker Desktop
esta sem recursos: interrompa, reinicie o Docker Desktop e rode o `.bat` de
novo (o dashboard antigo continua no ar ate o build terminar). Valide o
dashboard em `http://192.168.9.201:8000` e, externamente, em
`http://SEU_SUBDOMINIO.duckdns.org:10443`.

## Rodagem manual e limpeza

Na VM de rodagem, para escolher uma pasta especifica:

```powershell
& "C:\TC\Util Compartilhado\AgenteTC\run_agent_tc_python.bat" a08 "S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados\A08\NOME_DA_RODAGEM"
```

O primeiro argumento e a VM; o segundo e a pasta. Sem a pasta, o script escolhe
a mais recente daquela VM. Um terceiro argumento pode filtrar o inicio do nome
da versao. O log e o marcador de conclusao vao para `AGENT_TC_LOG_DIR`.

Pelo dashboard, o botao "Adicionar rodagem" (visao geral e cabecalho do modulo;
permissao "Solicitar rodagem") pede a mesma importacao: informa-se a pasta, que
precisa estar em `ArquivosCompactados\<VM>\<rodagem>`, e a branch do TC em que
a rodagem rodou (padrao: a principal). O RegravacaoBridge roda o
`run_agent_tc_python.bat` na D01 com a VM e a pasta, um pedido por vez (ate 60
minutos), e passa a branch em `AGENT_TC_BRANCH_URL`: e ela que a regravacao
dessa rodagem usa, ja que a copia da D01 esta sempre na principal. O resultado
e a saida do `.bat` aparecem na propria janela; o log do Bridge registra cada
importacao. A unidade `S:` precisa existir na sessao da tarefa do Bridge.

Cada ingestao e gravada em uma unica transacao no PostgreSQL: se falhar, nada
da rodagem e alterado alem do status `import_failed`. Reprocessar a mesma pasta
substitui ocorrencias, evidencias, diferencas e tempos daquela rodagem pelos
novos, removendo os que nao vieram de novo; o agrupamento feito pela IA e
mantido. Um agrupamento por IA interrompido (por exemplo, por reinicio da API)
deixa de bloquear um novo pedido depois de `AGENT_TC_AI_JOB_STALE_MINUTES`.

A limpeza de versoes inativas deve rodar no container da API, que enxerga banco
e volume de evidencias. A CLI usa 30 dias por padrao e faz apenas uma previa
sem `--apply`:

```powershell
docker exec agent-tc-api python cli/agent_tc_maintenance.py purge-inactive-versions --backend postgres --retention-days 30
```

O mesmo comando com `--apply` remove as rodagens e os arquivos
correspondentes. O criterio e a ultima rodagem de cada par sistema/versao.
O vigia (`API/deploy/d01/vigia_docker.ps1`, tarefa "AgenteTC - Vigia Docker")
roda esse `--apply` uma vez por dia, com 30 dias de retencao
(`-DiasRetencao`); o resultado fica em `logs\vigia_docker.log` e a data da
ultima limpeza em `logs\limpeza_ultima_data.txt`. O TestComplete nao chama mais
a limpeza no fim da rodagem; o atalho `PythonRodagem/run_agent_tc_maintenance.bat`
so a executa com backend `sqlite`/`postgres` (com `AGENT_TC_BACKEND=api`, como nas
VMs, ele avisa e sai sem erro).

## Quando o Docker Desktop nao responde

Se Jenkins, API e dashboard cairem juntos, verifique primeiro `docker version`.
Um erro do Docker Engine afeta todos esses containers ao mesmo tempo. A funcao
"Restart Docker Desktop" reinicia o engine; depois confira `docker ps -a`,
`docker volume ls` e os healthchecks. Nao recrie o banco com outro projeto
Compose sem identificar os volumes existentes.

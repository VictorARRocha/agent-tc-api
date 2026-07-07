# AgenteTC

Projeto do Agent TC para substituir o pos-processamento manual do Codex CLI por Python.

## Estrutura

- `agent_tc_core/`: pacote principal Python. Faz parsing da rodagem, extracao, evidencias, MDS, API e persistencia.
- `cli/`: comandos Python de operacao.
  - `agent_tc_ingest.py`: analisa uma pasta de rodagem e envia ao backend.
  - `agent_tc_api.py`: sobe a API consumida pelo dashboard.
  - `agent_tc_db.py`: utilitarios de banco, resumo e importacao.
- `database/`: scripts SQL do modelo canonico.
- `docs/`: documentacao de setup, banco, API e deploy.
- `JenkinsBridge/`: bridge atual do Jenkins.
- `Prompts/`: material legado do fluxo com Codex CLI.
- `legacy/`: scripts antigos mantidos como referencia.
- `logs/`: logs de runtime da API local.

## Entradas principais

- `run_agent_tc_python.bat`: deve ser chamado no pos-rodagem para processar a pasta mais recente da VM ou uma pasta informada.
- `run_agent_tc_api.bat`: sobe a API em `0.0.0.0:8000`.
- `start_agent_tc_api_hidden.vbs`: inicia a API escondida na VM que hospeda o Bridge.

Leia `docs/DEPLOY_BRIDGE_VM.md` antes de copiar para o Util Compartilhado.

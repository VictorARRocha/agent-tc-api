# PythonRodagem

O pos-processamento roda nas VMs de teste apos o TestComplete. Os launchers
`run_agent_tc_python.bat` e `.ps1` localizam a rodagem, leem compactados,
MDS/PJS e tempos, produzem o payload e enviam os dados a API da D01.

- `agent_tc_core/`: parser, pipeline, persistencia, storage, API e Auth local.
- `cli/`: entradas de ingestao, servidor e manutencao.
- `database/`: migrations PostgreSQL e SQLite.
- `tests/`: testes do pipeline e da API.

Nas VMs, use `AGENT_TC_BACKEND=api` em `PythonRodagem/.env` e aponte
`AGENT_TC_API_URL` para `http://192.168.9.201:8000`. O token tecnico deve
coincidir com `AGENT_TC_BRIDGE_TOKEN` da API. Consulte
[Configuracao](../docs/CONFIGURACAO.md) para os caminhos opcionais de logs e
MDS/PJS.

Os launchers procuram `.env` nesta ordem:

1. `PythonRodagem\.env`
2. `..\.env` legado da raiz

`run_agent_tc_maintenance.bat` e uma ferramenta de retencao com aplicacao
direta. Para a implantacao atual, execute primeiro a previa da manutencao no
container da API, conforme [Operacao da D01](../docs/OPERACAO_D01.md).

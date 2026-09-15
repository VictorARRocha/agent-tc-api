# PythonRodagem

Esta pasta contem o pos-rodagem em Python.

- `run_agent_tc_python.bat` / `run_agent_tc_python.ps1`: processam a pasta da rodagem, leem compactados, MDS/PJS e enviam o payload ao backend.
- `run_agent_tc_maintenance.bat`: rotina de limpeza/retencao.
- `agent_tc_core/`: parser, pipeline, repositories, storage, API server e Auth local.
- `cli/`: entrypoints Python.
- `database/`: migrations PostgreSQL/SQLite.
- `tests/`: testes automatizados.

Os scripts procuram `.env` nesta ordem:

1. `PythonRodagem\.env`
2. `..\.env` legado da raiz

O modo recomendado nas VMs de rodagem e `AGENT_TC_BACKEND=api`, apontando para a API central da D01.

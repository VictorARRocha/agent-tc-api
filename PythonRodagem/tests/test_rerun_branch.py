"""Branch do projeto na rodagem: o Jenkins faz svn revert + svn switch com o nome, entao so passa o que existe no SVN."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _write_env
from test_merge_api import BRANCHES, BRIDGE, User

PEDIDO = {"vm_name": "a07", "versao": "PROXIMA", "casos_teste": "[2]", "paralelo": "", "ct_desmarcar": "[0.3]",
          "data_hora": "13/10/2026 10:00:00"}


class RerunBranchTests(unittest.TestCase):
    def _apis(self, root: Path, *, com_branches: bool = True):
        root.mkdir(parents=True, exist_ok=True)
        env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
        for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
            repository.initialize()
            api = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User("ana", "user"))
            if com_branches:
                self.assertEqual(200, api.route_post("/bridge/merge/branches", {"branches": BRANCHES}, BRIDGE)[0])
            yield api, repository

    def _pedir(self, api, branch, n=[0]):
        n[0] += 1
        return api.route_post("/rerun-requests", {**PEDIDO, "id": f"rr{n[0]}", "branch": branch}, "Bearer x")

    def _branch_gravada(self, repository):
        pedido = repository.rerun_requests()[0]
        config = pedido.get("config_json")
        if isinstance(config, str):
            import json
            config = json.loads(config)
        return pedido.get("branch"), config.get("branch")

    def test_sem_branch_nao_troca(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api, repository in self._apis(Path(temp_dir)):
                self.assertEqual(201, self._pedir(api, "")[0])
                self.assertEqual(("", ""), self._branch_gravada(repository))

    def test_trunk_e_branch_da_lista(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api, repository in self._apis(Path(temp_dir)):
                self.assertEqual(201, self._pedir(api, " LOCAL ")[0])
                self.assertEqual(("local", "local"), self._branch_gravada(repository))
            for api, repository in self._apis(Path(temp_dir) / "b"):
                self.assertEqual(201, self._pedir(api, "Folha_sustentação")[0])
                self.assertEqual(("Folha_sustentação", "Folha_sustentação"), self._branch_gravada(repository))

    def test_fora_da_lista_e_recusado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api, repository in self._apis(Path(temp_dir)):
                for invalida in ("Inventada", "Unico", 'Proxima10_0" & del C:\\*', "../Unico"):
                    status, body = self._pedir(api, invalida)
                    self.assertEqual((400, "invalid_branch"), (status, body["error"]), invalida)
                self.assertEqual([], repository.rerun_requests())

    def test_sem_lista_do_svn_so_aceita_nao_trocar_ou_trunk(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api, _repository in self._apis(Path(temp_dir), com_branches=False):
                self.assertEqual(400, self._pedir(api, "Proxima10_0")[0])
                self.assertEqual(201, self._pedir(api, "local")[0])


if __name__ == "__main__":
    unittest.main()

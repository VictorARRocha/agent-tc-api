"""Importar rodagem pelo dashboard: pedido, validacao da pasta e rotas do RegravacaoBridge."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.importacao import DEFAULT_LOGS_BASE, ImportacaoError, pasta_da_rodagem
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _write_env

BRIDGE = "Bearer token-do-bridge"
PASTA = DEFAULT_LOGS_BASE + r"\A07\PROXIMA1.26.10.99 18_10_2026 08_16_16"


class User:
    def __init__(self, permissions=("rodagem",)):
        self.permissions = list(permissions)

    def validate(self, authorization):
        return {"id": "usr_ana", "username": "ana", "role": "user", "permissions": self.permissions}


class PastaDaRodagemTests(unittest.TestCase):
    def test_accepts_the_run_folder_and_takes_the_vm_from_it(self):
        self.assertEqual((PASTA, "A07"), pasta_da_rodagem(PASTA, DEFAULT_LOGS_BASE))
        sujo = '"' + PASTA.lower().replace("\\", "/") + '\\" '
        pasta, vm = pasta_da_rodagem(sujo, DEFAULT_LOGS_BASE)
        self.assertEqual("a07", vm)
        self.assertTrue(pasta.startswith(DEFAULT_LOGS_BASE + "\\"))

    def test_rejects_anything_else(self):
        for value in (
            "",
            r"C:\Windows\System32",
            DEFAULT_LOGS_BASE,
            DEFAULT_LOGS_BASE + r"\A07",
            DEFAULT_LOGS_BASE + r"\A07\rodagem\caso",
            DEFAULT_LOGS_BASE + r"\..\x",
            DEFAULT_LOGS_BASE + r"\A07\..",
            DEFAULT_LOGS_BASE + r"\A07\x & calc",
            DEFAULT_LOGS_BASE + r"\A07\x %PATH%",
            DEFAULT_LOGS_BASE + r'\A07\x" y',
        ):
            with self.assertRaises(ImportacaoError, msg=value):
                pasta_da_rodagem(value, DEFAULT_LOGS_BASE)


class ImportApiTests(unittest.TestCase):
    def _apis(self, root: Path, permissions=("rodagem",)):
        env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
        for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
            repository.initialize()
            yield AgentTcApi(root, repository=repository, env_path=env, auth_validator=User(permissions))

    def test_request_claim_and_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api in self._apis(Path(temp_dir)):
                status, row = api.route_post("/imports", {"pasta": PASTA}, "Bearer x")
                self.assertEqual(201, status)
                self.assertEqual(("solicitado", "A07", "ana"), (row["status"], row["vm"], row["requested_by"]))
                self.assertEqual(409, api.route_post("/imports", {"pasta": PASTA.upper()}, "Bearer x")[0])
                self.assertEqual(400, api.route_post("/imports", {"pasta": r"C:\x\y"}, "Bearer x")[0])

                self.assertEqual(401, api.route_get("/bridge/imports/requested", {}, "Bearer errado")[0])
                status, pending = api.route_get("/bridge/imports/requested", {}, BRIDGE)
                self.assertEqual([row["id"]], [r["id"] for r in pending])
                status, claim = api.route_post(f"/bridge/imports/{row['id']}/claim", {}, BRIDGE)
                self.assertTrue(claim["claimed"])
                self.assertFalse(api.route_post(f"/bridge/imports/{row['id']}/claim", {}, BRIDGE)[1]["claimed"])
                self.assertEqual([row["id"]], [r["id"] for r in api.route_get("/bridge/imports/processing", {}, BRIDGE)[1]])

                self.assertEqual(400, api.route_post(f"/bridge/imports/{row['id']}/update", {"status": "x"}, BRIDGE)[0])
                status, done = api.route_post(
                    f"/bridge/imports/{row['id']}/update", {"status": "concluido", "exit_code": 0, "output": "ok"}, BRIDGE
                )
                self.assertEqual(("concluido", 0, "ok"), (done["import"]["status"], done["import"]["exit_code"], done["import"]["output"]))
                self.assertTrue(done["import"]["finished_at"])
                status, listed = api.route_get("/imports", {}, "Bearer x")
                self.assertEqual(["concluido"], [r["status"] for r in listed])
                # Terminada, a mesma pasta pode ser importada de novo.
                self.assertEqual(201, api.route_post("/imports", {"pasta": PASTA}, "Bearer x")[0])

    def test_branch_of_the_run_comes_from_the_svn_list(self):
        url = "svn://svn/ProjetoUnico/branches/PRE-CLIENTES"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api in self._apis(Path(temp_dir)):
                api.repository.save_merge_branches([{"name": "PRE-CLIENTES", "url": url, "kind": "branch"}])
                self.assertEqual(400, api.route_post("/imports", {"pasta": PASTA, "branch_url": "svn://outra"}, "Bearer x")[0])
                status, row = api.route_post("/imports", {"pasta": PASTA, "branch_url": url + "/"}, "Bearer x")
                self.assertEqual((201, url), (status, row["branch_url"]))
                claim = api.route_post(f"/bridge/imports/{row['id']}/claim", {}, BRIDGE)[1]
                self.assertEqual(url, claim["import"]["branch_url"])

    def test_needs_the_rodagem_permission(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api in self._apis(Path(temp_dir), permissions=()):
                self.assertEqual(403, api.route_post("/imports", {"pasta": PASTA}, "Bearer x")[0])


if __name__ == "__main__":
    unittest.main()

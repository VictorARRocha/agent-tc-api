"""Lista de casos de teste da aba do modulo: a descricao do .mds chega ate a API."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.mds import MdsCollection
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _payload, _write_env

MDS = (
    '<Project><Node name="[3] Contabil" description="">'
    '<Node name="[3.1] Lancamentos" description="Rotinas de lancamento">'
    '<Node name="[3.1.1] Lancamento padrao" description="#171353 - Lanca e confere o saldo." />'
    '<Node name="[3.1.2] Sem descricao" />'
    "</Node></Node></Project>"
)


class TestcaseListTests(unittest.TestCase):
    def test_description_from_mds_is_stored_and_returned(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            mds = root / "Unico.mds"
            mds.write_text(MDS, encoding="utf-8")
            rows = MdsCollection([mds]).hierarchy_rows()
            self.assertEqual("#171353 - Lanca e confere o saldo.", {r["node_id"]: r for r in rows}["3.1.1"]["description"])
            self.assertTrue(all("testMoniker" not in r for r in rows))

            env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
            for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
                repository.initialize()
                payload = _payload(root, ["falha_1"])
                payload["testcase_hierarchy"] = rows
                repository.import_payload(payload)
                api = AgentTcApi(root, repository=repository, env_path=env, require_user_auth=False)
                status, body = api.route_get("/testcase-hierarchy", {"module": ["contabil"]})
                name = type(repository).__name__
                self.assertEqual(200, status, name)
                by_id = {r["node_id"]: r for r in body}
                self.assertEqual("#171353 - Lanca e confere o saldo.", by_id["3.1.1"]["descricao"], name)
                self.assertEqual("Rotinas de lancamento", by_id["3.1"]["descricao"], name)
                self.assertEqual("", by_id["3.1.2"]["descricao"], name)
                self.assertEqual(("grupo", "caso"), (by_id["3.1"]["node_type"], by_id["3.1.1"]["node_type"]), name)

    def test_sqlite_created_before_the_column_gets_it_on_initialize(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            repository = SQLiteRepository(Path(temp) / "agent-tc.sqlite")
            repository.initialize()
            repository.initialize()  # roda de novo sem erro (coluna ja existe)
            with repository.connect() as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(testcase_hierarchy)")}
            self.assertIn("description", columns)


class User:
    def __init__(self, permissions=("rodagem",), modules=("*",), role="user"):
        self.permissions, self.modules, self.role = list(permissions), list(modules), role

    def validate(self, authorization):
        return {"username": "ana", "role": self.role, "permissions": self.permissions, "modules": self.modules}


class TestcaseDisabledTests(unittest.TestCase):
    def _each_repository(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
            for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
                repository.initialize()
                yield repository, lambda user: AgentTcApi(root, repository=repository, env_path=env, auth_validator=user)

    def test_mark_list_and_unmark(self):
        for repository, api_for in self._each_repository():
            name = type(repository).__name__
            api = api_for(User(modules=["fiscal"]))
            status, row = api.route_post("/testcase-disabled", {"node_id": "[2.1.3]", "motivo": "  Aguardando   o chamado #12345 "}, None)
            self.assertEqual(201, status, (name, row))
            self.assertEqual(("2.1.3", "fiscal", "Aguardando o chamado #12345", "ana"),
                             (row["node_id"], row["module_slug"], row["reason"], row["disabled_by"]), name)
            # Marcar de novo so atualiza o motivo.
            self.assertEqual(201, api.route_post("/testcase-disabled", {"node_id": "2.1.3"}, None)[0], name)
            lista = api.route_get("/testcase-disabled", {"module": ["fiscal"]}, None)[1]
            self.assertEqual([("2.1.3", "")], [(r["node_id"], r["reason"]) for r in lista], name)
            self.assertEqual(200, api.route_post("/testcase-disabled/2.1.3/delete", {}, None)[0], name)
            self.assertEqual([], api.route_get("/testcase-disabled", {}, None)[1], name)
            self.assertEqual(404, api.route_post("/testcase-disabled/2.1.3/delete", {}, None)[0], name)

    def test_who_can_mark_and_who_sees(self):
        for repository, api_for in self._each_repository():
            name = type(repository).__name__
            admin = api_for(User(permissions=[], role="admin"))
            self.assertEqual(201, admin.route_post("/testcase-disabled", {"node_id": "1.4.2"}, None)[0], name)
            self.assertEqual(201, admin.route_post("/testcase-disabled", {"node_id": "2.1.3"}, None)[0], name)
            # Sem a permissao de rodagem: nao marca nem desmarca.
            sem_permissao = api_for(User(permissions=[], modules=["*"]))
            self.assertEqual(403, sem_permissao.route_post("/testcase-disabled", {"node_id": "2.1.4"}, None)[0], name)
            self.assertEqual(403, sem_permissao.route_post("/testcase-disabled/2.1.3/delete", {}, None)[0], name)
            # Com a permissao, so no modulo dela; e so ve os do modulo dela.
            folha = api_for(User(modules=["folha"]))
            status, body = folha.route_post("/testcase-disabled", {"node_id": "2.1.4"}, None)
            self.assertEqual((403, "module_required"), (status, body["error"]), name)
            self.assertEqual(403, folha.route_post("/testcase-disabled/2.1.3/delete", {}, None)[0], name)
            self.assertEqual(["1.4.2"], [r["node_id"] for r in folha.route_get("/testcase-disabled", {}, None)[1]], name)
            self.assertEqual(403, folha.route_get("/testcase-disabled", {"module": ["fiscal"]}, None)[0], name)
            self.assertEqual(200, folha.route_post("/testcase-disabled/1.4.2/delete", {}, None)[0], name)
            # Rotina geral [0.x] ou codigo sem modulo: so quem tem todos os modulos.
            self.assertEqual(403, folha.route_post("/testcase-disabled", {"node_id": "0.4"}, None)[0], name)
            self.assertEqual(201, api_for(User()).route_post("/testcase-disabled", {"node_id": "0.4"}, None)[0], name)

    def test_invalid_requests(self):
        for repository, api_for in self._each_repository():
            api = api_for(User())
            for body in ({"node_id": ""}, {"node_id": "abc"}, {"node_id": "2..1"}, {"node_id": "2.1", "motivo": "x" * 301}):
                status, data = api.route_post("/testcase-disabled", body, None)
                self.assertEqual(400, status, (type(repository).__name__, body, data))


if __name__ == "__main__":
    unittest.main()

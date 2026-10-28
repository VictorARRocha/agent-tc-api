"""Acesso por modulo: as acoes de rodagem e regravacao so valem nos modulos do usuario, e cada um
so ve os dados (rodagens, falhas, evidencias, pedidos) dos seus modulos.

Admin e "todos os modulos" (["*"], padrao de quem ja existia) passam sempre.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.local_auth import LocalAuthService
from agent_tc_core.permissions import invalid_modules, modules_of_test_cases, normalize_modules
from test_ingest_robustness import _write_env
from test_permissions import AuthSQLiteRepository

RERUN = {"vm_name": "a07", "versao": "PROXIMA", "ct_desmarcar": "[0.3]", "data_hora": "01/10/2026 10:00:00"}


class ModuleRuleTests(unittest.TestCase):
    def test_module_comes_from_the_test_case_code(self):
        casos = {
            "[1]": ["folha"], "[2]": ["fiscal"], "[3], [4], [7]": ["contabil"], "[5]": ["financeiro"], "[6]": ["geral"],
            "[9]": ["gestao"], "[16.1]": ["suprema"], "[19.2]": ["practice"],
            "[2.1.3], [2.1.4]": ["fiscal"], "[ 2.1 ]": ["fiscal"], "[3,4,7]": ["contabil"], "[02.1]": ["fiscal"],
            # Rotinas gerais (restaurar base, desmarcar) nao sao de nenhum modulo.
            "[0.4], [0.5], [9.1.4.1.3]": ["gestao"], "[0.3]": [],
            "[2.1], [1.4]": ["fiscal", "folha"],
        }
        for texto, esperado in casos.items():
            self.assertEqual((esperado, []), modules_of_test_cases(texto), texto)
        self.assertEqual(([], ["8"]), modules_of_test_cases("[8.1]"))
        self.assertEqual(([], ["x"]), modules_of_test_cases("[x]"))
        self.assertEqual(([], []), modules_of_test_cases(None))

    def test_stored_list(self):
        self.assertEqual(["*"], normalize_modules(None))  # usuario anterior a coluna: todos
        self.assertEqual(["*"], normalize_modules(["folha", "*"]))
        self.assertEqual(["folha", "fiscal"], normalize_modules(["fiscal", "folha", "fiscal", "inventado"]))
        self.assertEqual([], normalize_modules([]))
        self.assertEqual([], normalize_modules("folha"))
        self.assertEqual([], invalid_modules(["*", "folha", "practice"]))
        self.assertEqual(["inventado"], invalid_modules(["folha", "inventado"]))
        self.assertIsNone(invalid_modules("folha"))


class ModuleApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.repo = AuthSQLiteRepository(root / "agent-tc.sqlite")
        self.repo.initialize()
        self.api = AgentTcApi(root, repository=self.repo, env_path=_write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n"))
        self.auth = LocalAuthService(self.repo)
        self.auth.register({"username": "chefe", "password": "senha-forte"})  # primeiro = admin
        self.admin = "Bearer " + self.auth.login("chefe", "senha-forte")["token"]
        status, body = self.api.route_post("/auth/register", {"username": "ana", "password": "senha-forte", "modules": ["folha"]})
        self.assertEqual(201, status)
        self.ana_id = body["user"]["id"]
        self._set({"status": "approved", "permissions": ["rodagem", "regravar"]})
        self.ana = "Bearer " + self.auth.login("ana", "senha-forte")["token"]

    def tearDown(self):
        self.temp.cleanup()

    def _set(self, body, user_id=None):
        status, data = self.api.route_patch(f"/auth/users/{user_id or self.ana_id}", body, self.admin)
        self.assertEqual(200, status, data)
        return data["user"]

    def _rerun(self, casos, token=None, n=[0]):
        n[0] += 1
        return self.api.route_post("/rerun-requests", {**RERUN, "id": f"r{n[0]}", "casos_teste": casos}, token or self.ana)

    def _code(self, response):
        status, body = response
        return status, body.get("error") if isinstance(body, dict) else None

    def test_register_keeps_requested_modules_and_rejects_invalid(self):
        me = self.api.route_get("/auth/me", {}, self.ana)[1]["user"]
        self.assertEqual(["folha"], me["modules"])
        self.assertEqual((400, "invalid_modules"), self._code(self.api.route_post("/auth/register", {"username": "bia", "password": "senha-forte", "modules": ["folha", "rh"]})))
        self.assertEqual((400, "invalid_modules"), self._code(self.api.route_post("/auth/register", {"username": "bia", "password": "senha-forte", "modules": "folha"})))
        # Cadastro sem escolher (dashboard antigo): sem modulos; o admin marca na aprovacao.
        self.assertEqual([], self.api.route_post("/auth/register", {"username": "caio", "password": "senha-forte"})[1]["user"]["modules"])
        # O primeiro usuario (admin) ficou com todos.
        self.assertEqual(["*"], self.api.route_get("/auth/me", {}, self.admin)[1]["user"]["modules"])

    def test_rodagem_only_in_own_modules(self):
        self.assertEqual(201, self._rerun("[1]")[0])
        self.assertEqual(201, self._rerun("[0.4], [0.5], [1.2.3]")[0])  # reexecucao com restaurar base
        status, body = self._rerun("[2.1.3], [2.1.4]")
        self.assertEqual((403, "module_required", ["fiscal"]), (status, body["error"], body["modules"]))
        self.assertIn("Fiscal", body["message"])
        self.assertEqual((403, "module_required"), self._code(self._rerun("[3], [4], [7]")))
        self.assertEqual((403, "module_required"), self._code(self._rerun("[1], [2]")))  # mistura: precisa dos dois
        self.assertEqual((403, "module_unknown"), self._code(self._rerun("[8.1]")))
        self.assertEqual((403, "module_unknown"), self._code(self._rerun("[x]")))
        # Com os dois modulos, a mistura passa.
        self._set({"modules": ["folha", "fiscal"]})
        self.assertEqual(201, self._rerun("[1], [2]")[0])
        # Nada foi gravado nos pedidos barrados.
        casos = sorted(r["test_cases"] for r in self.api.route_get("/rerun-requests", {}, self.ana)[1])
        self.assertEqual(["[0.4], [0.5], [1.2.3]", "[1]", "[1], [2]"], casos)

    def test_all_modules_and_admin_are_never_blocked_by_module(self):
        self._set({"modules": ["*"]})
        self.assertEqual(201, self._rerun("[2]")[0])
        self.assertEqual(201, self._rerun("[8.1]")[0])  # sem restricao de modulo, nem le os codigos
        self.assertEqual(201, self._rerun("[3], [4], [7]", token=self.admin)[0])

    def test_no_module_means_no_action_and_change_applies_at_once(self):
        self._set({"modules": []})
        self.assertEqual((403, "module_required"), self._code(self._rerun("[1]")))
        self.assertEqual(201, self._rerun("[0.4]")[0])  # so rotina geral: nao e de modulo nenhum
        self._set({"modules": ["folha"]})
        self.assertEqual(201, self._rerun("[1]")[0])

    def test_cancel_rodagem_checks_the_module_of_the_request(self):
        self.assertEqual(201, self._rerun("[2]", token=self.admin)[0])
        rid = [r["id"] for r in self.api.route_get("/rerun-requests", {}, self.admin)[1] if r["test_cases"] == "[2]"][0]
        self.assertEqual((403, "module_required"), self._code(self.api.route_post(f"/rerun-requests/{rid}/cancel", {}, self.ana)))
        self._set({"modules": ["fiscal"]})
        self.assertEqual(202, self.api.route_post(f"/rerun-requests/{rid}/cancel", {}, self.ana)[0])

    def test_regravacao_checks_the_module_of_the_run(self):
        self.repo.run = lambda run_id: {"id": run_id, "modulo_slug": "fiscal"} if run_id == "rod_fiscal" else None
        status, body = self.api.route_post("/regravacoes", {"run_id": "rod_fiscal", "difference_ids": ["d"]}, self.ana)
        self.assertEqual((403, "module_required", ["fiscal"]), (status, body["error"], body["modules"]))
        self._set({"modules": ["fiscal"]})
        status, body = self.api.route_post("/regravacoes", {"run_id": "rod_fiscal", "difference_ids": ["d"]}, self.ana)
        self.assertNotIn(body.get("error"), ("module_required", "module_unknown", "permission_required"))

        # Cancelar: modulo da rodagem do pedido.
        pedido = {"id": "rg1", "status": "solicitado", "requested_by": "ana", "run_id": "rod_fiscal"}
        self.repo.regravacao_request = lambda request_id: dict(pedido) if request_id == "rg1" else None
        self.repo.update_regravacao_request = lambda request_id, fields: {**pedido, **fields}
        self._set({"modules": ["folha"]})
        self.assertEqual((403, "module_required"), self._code(self.api.route_post("/regravacoes/rg1/cancel", {}, self.ana)))
        self._set({"modules": ["fiscal"]})
        self.assertEqual(200, self.api.route_post("/regravacoes/rg1/cancel", {}, self.ana)[0])

    def test_admin_edits_modules_and_values_are_checked(self):
        self.assertEqual(["*"], self._set({"modules": ["folha", "*"]})["modules"])
        self.assertEqual(["folha", "contabil"], self._set({"modules": ["contabil", "folha"]})["modules"])
        for invalido in (["folha", "rh"], "folha", None):
            status, body = self.api.route_patch(f"/auth/users/{self.ana_id}", {"modules": invalido}, self.admin)
            self.assertEqual((400, "invalid_modules"), (status, body["error"]), invalido)
        # Mexer em outra coisa nao apaga os modulos; e a lista de usuarios mostra.
        self._set({"permissions": ["rodagem"]})
        users = {u["username"]: u for u in self.api.route_get("/auth/users", {}, self.admin)[1]["users"]}
        self.assertEqual(["folha", "contabil"], users["ana"]["modules"])
        # So o admin muda.
        status, body = self.api.route_patch(f"/auth/users/{self.ana_id}", {"modules": ["*"]}, self.ana)
        self.assertEqual((403, "admin_required"), (status, body["error"]))

    def _fake_runs(self):
        """Uma rodagem da Folha, uma do Fiscal e uma sem modulo identificado."""
        runs = {
            "rod_folha": {"id_rodagem": "rod_folha", "modulo_slug": "folha"},
            "rod_fiscal": {"id_rodagem": "rod_fiscal", "modulo_slug": "fiscal"},
            "rod_sem": {"id_rodagem": "rod_sem", "modulo_slug": ""},
        }
        self.repo.modules = lambda: [{"slug": "folha"}, {"slug": "fiscal"}, {"slug": "contabil"}]
        self.repo.latest_runs_by_module = lambda: [
            {"modulo": {"slug": "folha"}, "rodagem": runs["rod_folha"]},
            {"modulo": {"slug": "fiscal"}, "rodagem": runs["rod_fiscal"]},
        ]
        self.repo.runs = lambda module=None: [r for r in runs.values() if not module or r["modulo_slug"] == module]
        self.repo.run = lambda run_id: runs.get(run_id)
        self.repo.failures = lambda run_id: [{"id_falha": "f_" + run_id}]
        self.repo.evidences_by_failure = lambda failure_id: [
            {"id": "e1", "modulo_slug": "folha"}, {"id": "e2", "modulo_slug": "fiscal"}
        ]
        self.repo.testcase_hierarchy = lambda module=None: [
            row for row in [{"node_id": "1.1", "module_id": "mod_folha"}, {"node_id": "2.1", "module_code": "2"}]
            if not module or {"mod_folha": "folha"}.get(row.get("module_id"), "fiscal") == module
        ]
        self.repo.regravacao_requests = lambda run_id=None: [
            r for r in [{"id": "rg1", "run_id": "rod_folha"}, {"id": "rg2", "run_id": "rod_fiscal"}]
            if not run_id or r["run_id"] == run_id
        ]

    def _get(self, path, query=None, token=None):
        return self.api.route_get(path, query or {}, token or self.ana)

    def test_each_one_only_sees_the_data_of_their_modules(self):
        self._fake_runs()
        slugs = lambda rows, key="slug": [row[key] for row in rows]
        self.assertEqual(["folha"], slugs(self._get("/modules")[1]))
        self.assertEqual(["folha"], [item["modulo"]["slug"] for item in self._get("/modules/latest-runs")[1]])
        self.assertEqual(["rod_folha"], slugs(self._get("/runs")[1], "id_rodagem"))
        self.assertEqual(["rod_folha"], slugs(self._get("/modules/folha/runs")[1], "id_rodagem"))
        self.assertEqual(200, self._get("/runs/rod_folha")[0])
        self.assertEqual(200, self._get("/runs/rod_folha/failures")[0])
        # Outro modulo, pelo link ou pelo filtro: barrado com a mensagem do modulo.
        for path, query in (("/modules/fiscal/runs", {}), ("/runs", {"module": ["fiscal"]}),
                            ("/runs/rod_fiscal", {}), ("/runs/rod_fiscal/failures", {}), ("/runs/rod_fiscal/regravacao", {}),
                            ("/testcase-hierarchy", {"module": ["fiscal"]}), ("/regravacoes", {"run_id": ["rod_fiscal"]})):
            status, body = self._get(path, query)
            self.assertEqual((403, "module_required"), (status, body["error"]), path)
            self.assertIn("Fiscal", body["message"])
        # Rodagem sem modulo identificado: so quem tem todos.
        self.assertEqual((403, "module_unknown"), self._code(self._get("/runs/rod_sem")))
        self.assertEqual(404, self._get("/runs/nao_existe")[0])
        # Listas misturadas: so as linhas dos modulos dela.
        self.assertEqual(["e1"], slugs(self._get("/failures/f1/evidences")[1], "id"))
        self.assertEqual(["1.1"], slugs(self._get("/testcase-hierarchy")[1], "node_id"))
        self.assertEqual(["rg1"], slugs(self._get("/regravacoes")[1], "id"))

    def test_rerun_history_shows_only_requests_of_own_modules(self):
        for casos in ("[1]", "[2]", "[1], [2]", "[0.4], [1.2]", "[0.3]", "[8.1]"):
            self.assertEqual(201, self._rerun(casos, token=self.admin)[0])
        casos = lambda token: sorted(r["test_cases"] for r in self._get("/rerun-requests", token=token)[1])
        # Mistura so com os dois; rotina geral [0.x] para todos; codigo desconhecido escondido.
        self.assertEqual(["[0.3]", "[0.4], [1.2]", "[1]"], casos(self.ana))
        self.assertEqual(6, len(casos(self.admin)))

    def test_admin_and_all_modules_see_everything_and_change_applies_at_once(self):
        self._fake_runs()
        for token in (self.admin, None):
            if token is None:
                self._set({"modules": ["*"]})
            token = token or self.ana
            self.assertEqual(3, len(self._get("/modules", token=token)[1]))
            self.assertEqual(3, len(self._get("/runs", token=token)[1]))
            self.assertEqual(200, self._get("/runs/rod_sem", token=token)[0])
            self.assertEqual(2, len(self._get("/regravacoes", token=token)[1]))
        self._set({"modules": []})
        self.assertEqual([], self._get("/modules")[1])
        self.assertEqual([], self._get("/runs")[1])

    def test_evidence_files_follow_the_module_of_the_run(self):
        self._fake_runs()
        self.assertIsNone(self.api._file_module_error(self.ana, "folha/PROXIMA/rod_folha/1.1/print/tela.png"))
        self.assertEqual((403, "module_required"), self._code(self.api._file_module_error(self.ana, "fiscal/PROXIMA/rod_fiscal/2.1/print/tela.png")))
        # Caminho que nao aponta para uma rodagem conhecida: nada.
        for caminho in ("folha/PROXIMA/inventada/x.png", "x.png", "folha%2FPROXIMA%2Frod_fiscal%2Fx.png"):
            self.assertIn(self._code(self.api._file_module_error(self.ana, caminho))[0], (403, 404), caminho)
        self.assertIsNone(self.api._file_module_error(self.admin, "fiscal/PROXIMA/rod_fiscal/2.1/print/tela.png"))
        self.assertIsNone(self.api._file_module_error(self.admin, "qualquer/coisa.png"))


class ModuleViewRealDataTests(unittest.TestCase):
    """Mesmo filtro com uma rodagem importada de verdade (SQLite e o repositorio do Postgres)."""

    def test_contabil_run_is_seen_only_by_who_has_contabil(self):
        from test_regravacao_api import _payload_with_differences
        from test_ingest_robustness import RUN_ID, InMemoryCanonicalRepository
        from agent_tc_core.sqlite_repository import SQLiteRepository

        class User:
            def __init__(self, modules):
                self.modules = modules

            def validate(self, authorization):
                return {"username": "ana", "role": "user", "permissions": ["regravar"], "modules": self.modules}

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
            for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
                repository.initialize()
                repository.import_payload(_payload_with_differences(root))
                contabil = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User(["contabil"]))
                folha = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User(["folha"]))
                name = type(repository).__name__

                runs = contabil.route_get("/runs", {})[1]
                self.assertEqual([RUN_ID], [r["id_rodagem"] for r in runs], name)
                self.assertEqual("contabil", runs[0]["modulo_slug"], name)
                self.assertEqual(["contabil"], [m["slug"] for m in contabil.route_get("/modules", {})[1]], name)
                falhas = contabil.route_get(f"/runs/{RUN_ID}/failures", {})[1]
                self.assertTrue(falhas, name)
                evidencias = contabil.route_get(f"/failures/{falhas[0]['id']}/evidences", {})[1]
                self.assertTrue(evidencias, name)
                self.assertEqual(200, contabil.route_get(f"/runs/{RUN_ID}/regravacao", {})[0], name)
                self.assertIsNone(contabil._file_module_error(None, evidencias[0]["storage_path"]), name)

                self.assertEqual([], folha.route_get("/runs", {})[1], name)
                self.assertEqual(["folha"], [m["slug"] for m in folha.route_get("/modules", {})[1]], name)
                self.assertEqual([], [i for i in folha.route_get("/modules/latest-runs", {})[1] if i["rodagem"]], name)
                for path in (f"/runs/{RUN_ID}", f"/runs/{RUN_ID}/failures", f"/runs/{RUN_ID}/evidences", f"/runs/{RUN_ID}/regravacao"):
                    self.assertEqual(403, folha.route_get(path, {})[0], (name, path))
                self.assertEqual([], folha.route_get(f"/failures/{falhas[0]['id']}/evidences", {})[1], name)
                self.assertEqual([], folha.route_get("/testcase-hierarchy", {})[1], name)
                self.assertEqual(403, folha._file_module_error(None, evidencias[0]["storage_path"])[0], name)


if __name__ == "__main__":
    unittest.main()

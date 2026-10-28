"""Permissoes por usuario, com o login local de verdade (cadastro, aprovacao, token).

Ver rodagens: todos. Solicitar rodagem / merge / regravar: so com a permissao. Admin pode tudo.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.local_auth import LocalAuthService
from agent_tc_core.permissions import has_permission, normalize_permissions
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import _write_env
from test_local_auth import InMemoryAuthRepository

RERUN = {"vm_name": "a07", "versao": "PROXIMA", "casos_teste": "[2]", "ct_desmarcar": "[0.3]", "data_hora": "01/10/2026 10:00:00"}
MERGE_BODY = {"source_url": "https://svn/x/Unico", "target_url": "https://svn/x/branches/b", "direto": True}
PRESET = {"nome": "Fiscal a07", "modo": "simplificada", "config": {"vm_name": "a07", "modulo": "Fiscal", "versao": "PROXIMA"}}


class AuthSQLiteRepository(SQLiteRepository):
    """Dados no SQLite + usuarios/sessoes em memoria (o SQLite nao tem o login local)."""

    def __init__(self, path):
        super().__init__(path)
        self._auth = InMemoryAuthRepository()

    def __getattr__(self, name):
        if name.startswith("auth_"):
            return getattr(self._auth, name)
        raise AttributeError(name)


class PermissionRuleTests(unittest.TestCase):
    def test_admin_can_everything_and_unknown_values_are_ignored(self):
        self.assertTrue(has_permission({"role": "admin", "permissions": []}, "regravar"))
        self.assertFalse(has_permission({"role": "user", "permissions": []}, "rodagem"))
        self.assertTrue(has_permission({"role": "user", "permissions": ["merge"]}, "merge"))
        self.assertFalse(has_permission(None, "merge"))
        # Coluna ainda nula, texto solto ou valores desconhecidos: sem permissao.
        self.assertEqual([], normalize_permissions(None))
        self.assertEqual([], normalize_permissions("merge"))
        self.assertEqual(["rodagem", "regravar"], normalize_permissions(["regravar", "x", "rodagem", "rodagem"]))


class PermissionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.repo = AuthSQLiteRepository(root / "agent-tc.sqlite")
        self.repo.initialize()
        self.api = AgentTcApi(root, repository=self.repo, env_path=_write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n"))
        auth = LocalAuthService(self.repo)
        auth.register({"username": "chefe", "password": "senha-forte"})  # primeiro = admin
        self.admin = "Bearer " + auth.login("chefe", "senha-forte")["token"]
        self.ana_id = auth.register({"username": "ana", "password": "senha-forte"})["id"]
        # Estes testes sao das permissoes de acao: a ana tem todos os modulos (os modulos tem testes proprios).
        self.assertEqual(200, self._set(self.ana_id, {"status": "approved", "modules": ["*"]})[0])
        self.ana = "Bearer " + auth.login("ana", "senha-forte")["token"]

    def tearDown(self):
        self.temp.cleanup()

    def _set(self, user_id, body, token=None):
        return self.api.route_patch(f"/auth/users/{user_id}", body, token or self.admin)

    def _denied(self, response, permission):
        status, body = response
        self.assertEqual((403, "permission_required", permission), (status, body.get("error"), body.get("permission")), body)
        self.assertIn("permissao", body["message"])

    def test_approved_user_starts_only_seeing(self):
        status, me = self.api.route_get("/auth/me", {}, self.ana)
        self.assertEqual((200, []), (status, me["user"]["permissions"]))
        self.assertEqual(200, self.api.route_get("/modules", {}, self.ana)[0])  # ver rodagens: todos
        self._denied(self.api.route_post("/rerun-requests", RERUN, self.ana), "rodagem")
        self._denied(self.api.route_post("/rerun-requests/qualquer/cancel", {}, self.ana), "rodagem")
        self._denied(self.api.route_post("/run-presets", PRESET, self.ana), "rodagem")
        self._denied(self.api.route_patch("/run-presets/qualquer", {"nome": "x"}, self.ana), "rodagem")
        self._denied(self.api.route_post("/run-presets/qualquer/delete", {}, self.ana), "rodagem")
        self._denied(self.api.route_post("/merges", MERGE_BODY, self.ana), "merge")
        self._denied(self.api.route_post("/merges/qualquer/confirm", {}, self.ana), "merge")
        self._denied(self.api.route_post("/merges/qualquer/cancel", {}, self.ana), "merge")
        self._denied(self.api.route_post("/regravacoes", {"run_id": "r", "difference_ids": ["d"]}, self.ana), "regravar")
        self._denied(self.api.route_post("/regravacoes/qualquer/cancel", {}, self.ana), "regravar")
        # Nada foi gravado.
        self.assertEqual([], self.api.route_get("/rerun-requests", {}, self.ana)[1])
        self.assertEqual([], self.api.route_get("/run-presets", {}, self.ana)[1])

    def test_permission_given_by_admin_applies_at_once_with_the_same_session(self):
        status, body = self._set(self.ana_id, {"permissions": ["rodagem"]})
        self.assertEqual((200, ["rodagem"]), (status, body["user"]["permissions"]))
        self.assertEqual(201, self.api.route_post("/rerun-requests", RERUN, self.ana)[0])
        self.assertEqual(201, self.api.route_post("/run-presets", PRESET, self.ana)[0])
        self._denied(self.api.route_post("/merges", MERGE_BODY, self.ana), "merge")

        # Com merge: passa da permissao (aqui para em "lista de branches ainda nao enviada").
        self._set(self.ana_id, {"permissions": ["rodagem", "merge"]})
        self.assertEqual((503, "branches_unavailable"), (lambda r: (r[0], r[1]["error"]))(self.api.route_post("/merges", MERGE_BODY, self.ana)))
        # Com regravar: passa da permissao (rodagem inexistente).
        self._set(self.ana_id, {"permissions": ["regravar"]})
        status, body = self.api.route_post("/regravacoes", {"run_id": "nao-existe", "difference_ids": ["d"]}, self.ana)
        self.assertNotEqual("permission_required", body.get("error"))

        # Tirar a permissao tambem vale na hora.
        self._set(self.ana_id, {"permissions": []})
        self._denied(self.api.route_post("/rerun-requests", RERUN, self.ana), "rodagem")
        self.assertEqual([], self.api.route_get("/auth/me", {}, self.ana)[1]["user"]["permissions"])

    def test_admin_does_everything_without_the_list(self):
        self.assertEqual([], self.api.route_get("/auth/me", {}, self.admin)[1]["user"]["permissions"])
        self.assertEqual(201, self.api.route_post("/rerun-requests", RERUN, self.admin)[0])
        self.assertEqual(201, self.api.route_post("/run-presets", PRESET, self.admin)[0])
        self.assertEqual(503, self.api.route_post("/merges", MERGE_BODY, self.admin)[0])  # sem branches, mas autorizado

    def test_only_admin_changes_permissions_and_values_are_checked(self):
        self._set(self.ana_id, {"permissions": ["rodagem"]})
        status, body = self._set(self.ana_id, {"permissions": ["rodagem", "merge", "regravar"]}, token=self.ana)
        self.assertEqual((403, "admin_required"), (status, body["error"]))
        self.assertEqual(["rodagem"], self.api.route_get("/auth/me", {}, self.ana)[1]["user"]["permissions"])
        for invalid in (["rodagem", "deletar_tudo"], "merge", None, {"merge": True}):
            status, body = self._set(self.ana_id, {"permissions": invalid})
            self.assertEqual((400, "invalid_permissions"), (status, body["error"]), invalid)
        # Repetidas e fora de ordem: grava normalizado.
        self.assertEqual(["rodagem", "merge"], self._set(self.ana_id, {"permissions": ["merge", "rodagem", "merge"]})[1]["user"]["permissions"])
        # A lista de usuarios do admin mostra as permissoes de cada um; trocar outro campo nao apaga.
        self._set(self.ana_id, {"first_name": "Ana"})
        users = {u["username"]: u for u in self.api.route_get("/auth/users", {}, self.admin)[1]["users"]}
        self.assertEqual(["rodagem", "merge"], users["ana"]["permissions"])
        self.assertEqual([], users["chefe"]["permissions"])

    def test_new_user_registered_after_has_no_permission(self):
        status, body = self.api.route_post("/auth/register", {"username": "novo", "password": "senha-forte"})
        self.assertEqual((201, []), (status, body["user"]["permissions"]))

    def test_regravacao_cancel_only_own_or_admin(self):
        joao_id = LocalAuthService(self.repo).register({"username": "joao", "password": "senha-forte"})["id"]
        self._set(joao_id, {"status": "approved", "permissions": ["regravar"]})
        self._set(self.ana_id, {"permissions": ["regravar"]})
        joao = "Bearer " + LocalAuthService(self.repo).login("joao", "senha-forte")["token"]
        # Pedido na fila, feito pela ana (a regra testada aqui e so quem pode cancelar).
        pedido = {"id": "rg1", "status": "solicitado", "requested_by": "ana"}
        self.repo.regravacao_request = lambda request_id: dict(pedido) if request_id == "rg1" else None
        self.repo.update_regravacao_request = lambda request_id, fields: {**pedido, **fields}
        self.assertEqual((403, "not_owner"), (lambda r: (r[0], r[1]["error"]))(self.api.route_post("/regravacoes/rg1/cancel", {}, joao)))
        self.assertEqual(200, self.api.route_post("/regravacoes/rg1/cancel", {}, self.ana)[0])


if __name__ == "__main__":
    unittest.main()

"""API do merge entre branches do TC (pedido, previa, confirmacao e rotas do MergeBridge)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _write_env

BRIDGE = "Bearer token-do-bridge"
ROOT = "https://svn/testcomplete/unico/ProjetoUnico"
UNICO = ROOT + "/Unico"
PROXIMA = ROOT + "/branches/Proxima10_0"
FOLHA = ROOT + "/branches/Folha_sustenta%C3%A7%C3%A3o"
BRANCHES = [
    {"name": "Unico", "url": UNICO, "kind": "trunk", "last_revision": "23553", "last_author": "tiago"},
    {"name": "Proxima10_0", "url": PROXIMA, "kind": "branch", "last_revision": "23500"},
    {"name": "Folha_sustentação", "url": FOLHA, "kind": "branch"},
]


class User:
    def __init__(self, username: str, role: str, permissions=("rodagem", "merge", "regravar")):
        self.username, self.role, self.permissions = username, role, list(permissions)

    def validate(self, authorization):
        return {"id": "usr_" + self.username, "username": self.username, "role": self.role, "permissions": self.permissions}


class MergeApiTests(unittest.TestCase):
    def _apis(self, root: Path, *, with_branches: bool = True):
        env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
        for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
            repository.initialize()
            ana = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User("ana", "user"))
            joao = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User("joao", "user"))
            admin = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User("chefe", "admin"))
            if with_branches:
                status, _ = ana.route_post("/bridge/merge/branches", {"branches": BRANCHES}, BRIDGE)
                self.assertEqual(200, status)
            yield ana, joao, admin

    def _new(self, api, source=UNICO, target=PROXIMA):
        return api.route_post("/merges", {"source_url": source, "target_url": target}, "Bearer x")

    def test_branch_list_comes_from_the_bridge(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, _, _ in self._apis(Path(temp_dir)):
                self.assertEqual(401, ana.route_post("/bridge/merge/branches", {"branches": BRANCHES}, "Bearer errado")[0])
                self.assertEqual(400, ana.route_post("/bridge/merge/branches", {"branches": []}, BRIDGE)[0])
                status, data = ana.route_get("/merge/branches", {}, "Bearer x")
                self.assertEqual(200, status)
                self.assertEqual(["Unico", "Proxima10_0", "Folha_sustentação"], [b["name"] for b in data["branches"]])
                self.assertEqual("trunk", data["branches"][0]["kind"])
                self.assertTrue(data["updated_at"])

    def test_any_approved_user_requests_a_preview_between_known_branches(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, _, _ in self._apis(Path(temp_dir)):
                status, row = self._new(ana)
                self.assertEqual(201, status)
                self.assertEqual(("previa_solicitada", "ana", "Unico", "Proxima10_0", "branch"),
                                 (row["status"], row["requested_by"], row["source_name"], row["target_name"], row["target_kind"]))
                # Branch -> branch e branch -> Unico tambem sao permitidos.
                self.assertEqual(201, self._new(ana, PROXIMA, FOLHA)[0])
                self.assertEqual(201, self._new(ana, FOLHA, UNICO)[0])
                # Mesma origem e destino com merge em andamento: bloqueado.
                status, body = self._new(ana)
                self.assertEqual((409, "merge_in_progress"), (status, body["error"]))
                for source, target, error in ((UNICO, UNICO, "same_branch"), (UNICO, ROOT + "/branches/inventada", "unknown_branch"),
                                              ("", PROXIMA, "branches_required")):
                    status, body = self._new(ana, source, target)
                    self.assertEqual((400, error), (status, body["error"]), (source, target))
                listed = ana.route_get("/merges", {}, "Bearer x")[1]
                self.assertEqual(3, len(listed))

    def test_without_branch_list_explains_that_the_bridge_did_not_send_it(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, _, _ in self._apis(Path(temp_dir), with_branches=False):
                status, body = self._new(ana)
                self.assertEqual((503, "branches_unavailable"), (status, body["error"]))

    def test_bridge_flow_preview_confirm_and_merge(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, joao, _ in self._apis(Path(temp_dir)):
                rid = self._new(ana)[1]["id"]
                self.assertEqual(401, ana.route_get("/bridge/merges/pending", {}, "Bearer errado")[0])
                self.assertEqual([rid], [r["id"] for r in ana.route_get("/bridge/merges/pending", {}, BRIDGE)[1]])

                # Previa
                claim = ana.route_post(f"/bridge/merges/{rid}/claim", {}, BRIDGE)[1]
                self.assertEqual((True, "previa_processando"), (claim["claimed"], claim["merge"]["status"]))
                self.assertFalse(ana.route_post(f"/bridge/merges/{rid}/claim", {}, BRIDGE)[1]["claimed"])
                self.assertEqual([rid], [r["id"] for r in ana.route_get("/bridge/merges/processing", {}, BRIDGE)[1]])
                # Nao confirma antes da previa ficar pronta.
                self.assertEqual(409, ana.route_post(f"/merges/{rid}/confirm", {}, "Bearer x")[0])
                preview = {"source_revision": "23553", "revisoes": [{"revisao": "23550"}], "mensagem_padrao": "Merge..."}
                status, upd = ana.route_post(f"/bridge/merges/{rid}/update", {"status": "previa_pronta", "preview_json": preview}, BRIDGE)
                self.assertEqual(200, status)
                self.assertEqual("23553", upd["merge"]["preview_json"]["source_revision"])
                self.assertIsNone(upd["merge"]["finished_at"])

                # Outro usuario aprovado tambem pode confirmar (a previa e publica).
                status, conf = joao.route_post(f"/merges/{rid}/confirm", {"mensagem": "  Atualiza a Proxima  "}, "Bearer x")
                self.assertEqual(200, status)
                self.assertEqual(("solicitado", "Atualiza a Proxima", "joao"),
                                 (conf["merge"]["status"], conf["merge"]["commit_message"], conf["merge"]["confirmed_by"]))

                # Execucao
                self.assertEqual("processando", ana.route_post(f"/bridge/merges/{rid}/claim", {}, BRIDGE)[1]["merge"]["status"])
                self.assertEqual(400, ana.route_post(f"/bridge/merges/{rid}/update", {"status": "cancelado"}, BRIDGE)[0])
                done = ana.route_post(f"/bridge/merges/{rid}/update", {"status": "concluido", "svn_revision": "23560"}, BRIDGE)[1]["merge"]
                self.assertEqual(("concluido", "23560"), (done["status"], done["svn_revision"]))
                self.assertTrue(done["finished_at"])
                self.assertEqual("23553", done["preview_json"]["source_revision"])  # a previa continua guardada
                self.assertEqual(404, ana.route_post("/bridge/merges/nao-existe/update", {"status": "erro"}, BRIDGE)[0])

    def test_merge_into_unico_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, _, _ in self._apis(Path(temp_dir)):
                rid = self._new(ana, PROXIMA, UNICO)[1]["id"]
                ana.route_post(f"/bridge/merges/{rid}/claim", {}, BRIDGE)
                ana.route_post(f"/bridge/merges/{rid}/update", {"status": "previa_pronta", "preview_json": {}}, BRIDGE)
                status, body = ana.route_post(f"/merges/{rid}/confirm", {}, "Bearer x")
                self.assertEqual((400, "trunk_confirmation_required"), (status, body["error"]))
                self.assertIn("Unico", body["message"])
                self.assertEqual(200, ana.route_post(f"/merges/{rid}/confirm", {"confirma_trunk": True}, "Bearer x")[0])

    def test_cancel_only_own_or_admin_and_only_before_running(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, joao, admin in self._apis(Path(temp_dir)):
                rid = self._new(ana)[1]["id"]
                self.assertEqual(403, joao.route_post(f"/merges/{rid}/cancel", {}, "Bearer x")[0])
                status, body = ana.route_post(f"/merges/{rid}/cancel", {}, "Bearer x")
                self.assertEqual((200, "cancelado"), (status, body["merge"]["status"]))
                self.assertTrue(body["merge"]["finished_at"])
                # Cancelado libera a mesma origem/destino para um novo pedido.
                rid2 = self._new(ana)[1]["id"]
                ana.route_post(f"/bridge/merges/{rid2}/claim", {}, BRIDGE)
                self.assertEqual(409, admin.route_post(f"/merges/{rid2}/cancel", {}, "Bearer x")[0])  # ja processando
                ana.route_post(f"/bridge/merges/{rid2}/update", {"status": "previa_pronta", "preview_json": {}}, BRIDGE)
                self.assertEqual(200, admin.route_post(f"/merges/{rid2}/cancel", {}, "Bearer x")[0])  # admin cancela

    def test_conflict_is_terminal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, _, _ in self._apis(Path(temp_dir)):
                rid = self._new(ana)[1]["id"]
                ana.route_post(f"/bridge/merges/{rid}/claim", {}, BRIDGE)
                row = ana.route_post(f"/bridge/merges/{rid}/update", {
                    "status": "conflito", "error_message": "Conflito", "preview_json": {"conflitos": [{"caminho": "a.sd"}]},
                }, BRIDGE)[1]["merge"]
                self.assertEqual("conflito", row["status"])
                self.assertTrue(row["finished_at"])
                self.assertEqual(409, ana.route_post(f"/merges/{rid}/confirm", {}, "Bearer x")[0])
                self.assertEqual(409, ana.route_post(f"/merges/{rid}/cancel", {}, "Bearer x")[0])
                self.assertEqual(201, self._new(ana)[0])  # pode pedir de novo depois de resolver

    def test_direct_merge_skips_the_preview(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for ana, _, _ in self._apis(Path(temp_dir)):
                status, row = ana.route_post("/merges", {"source_url": UNICO, "target_url": PROXIMA, "direto": True,
                                                         "mensagem": "  Merge da Unico  "}, "Bearer x")
                self.assertEqual(201, status)
                self.assertEqual(("solicitado", "Merge da Unico", "ana"), (row["status"], row["commit_message"], row["confirmed_by"]))
                self.assertTrue(row["confirmed_at"])
                pending = ana.route_get("/bridge/merges/pending", {}, BRIDGE)[1]
                self.assertEqual([row["id"]], [r["id"] for r in pending])
                self.assertEqual("Merge da Unico", ana.route_get(f"/merges/{row['id']}", {}, "Bearer x")[1]["commit_message"])
                # Sem mensagem: o Bridge usa a padrao.
                self.assertIsNone(ana.route_post("/merges", {"source_url": PROXIMA, "target_url": FOLHA, "direto": True}, "Bearer x")[1]["commit_message"])
                # Destino Unico continua exigindo a confirmacao explicita.
                status, body = ana.route_post("/merges", {"source_url": PROXIMA, "target_url": UNICO, "direto": True}, "Bearer x")
                self.assertEqual((400, "trunk_confirmation_required"), (status, body["error"]))
                status, body = ana.route_post("/merges", {"source_url": PROXIMA, "target_url": UNICO, "direto": True,
                                                          "confirma_trunk": True, "mensagem": "x" * 4001}, "Bearer x")
                self.assertEqual((400, "message_too_long"), (status, body["error"]))
                status, row = ana.route_post("/merges", {"source_url": PROXIMA, "target_url": UNICO, "direto": True,
                                                         "confirma_trunk": True}, "Bearer x")
                self.assertEqual((201, "solicitado"), (status, row["status"]))
                # O merge direto nao pode ser "confirmado" de novo.
                self.assertEqual(409, ana.route_post(f"/merges/{row['id']}/confirm", {"confirma_trunk": True}, "Bearer x")[0])


if __name__ == "__main__":
    unittest.main()

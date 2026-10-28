from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _payload, _write_env
from test_pipeline_regressions import _payload_with_repeated_case


class FixedUser:
    """Validador de sessao fixo, no lugar do Auth local."""

    def __init__(self, username: str, role: str = "user", permissions=("rodagem", "merge", "regravar")):
        self.username = username
        self.role = role
        self.permissions = list(permissions)

    def validate(self, authorization):
        return {"id": "usr_1", "username": self.username, "role": self.role, "permissions": self.permissions}


def _run_payload(run_id: str, started_at: str, module_id: str = "mod_contabil") -> dict:
    payload = _payload_with_repeated_case()
    payload["rodagem"] = {**payload["rodagem"], "id_rodagem": run_id, "data_inicio": started_at}
    payload["modulo"] = {"id_modulo": module_id, "nome": "Contabil"}
    for failure in payload["falhas"]:
        failure["id_falha"] = failure["id_falha"] + "_" + run_id
    payload["diferencas_relatorio"] = []
    payload["agrupamentos_shadow"] = []
    return payload


class LatestRunsByModuleTests(unittest.TestCase):
    def test_sqlite_returns_latest_run_per_module(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            repository.import_payload(_run_payload("rod_antiga", "2026-07-01T10:00:00-03:00"))
            repository.import_payload(_run_payload("rod_nova", "2026-07-09T10:00:00-03:00"))
            api = AgentTcApi(Path(temp_dir), repository=repository, require_user_auth=False)

            status, payload = api.route_get("/modules/latest-runs", {})

            self.assertEqual(200, status)
            by_slug = {item["modulo"]["slug"]: item["rodagem"] for item in payload}
            self.assertEqual("rod_nova", by_slug["contabil"]["id"])
            self.assertEqual("contabil", by_slug["contabil"]["modulo_slug"])
            self.assertIsNone(by_slug["folha"])
            self.assertEqual(8, len(payload))

    def test_latest_runs_requires_session(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            api = AgentTcApi(Path(temp_dir), repository=repository)

            status, _ = api.route_get("/modules/latest-runs", {})

            self.assertEqual(401, status)

    def test_canonical_latest_runs_and_single_module_lookup_for_lists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = InMemoryCanonicalRepository(_write_env(root))
            repository.import_payload(_payload(root, ["falha_1"], run_id="rod_1"))
            repository.import_payload(_payload(root, ["falha_2"], run_id="rod_2"))
            repository.tables["runs"][("rod_1",)]["started_at"] = "2026-07-01T10:00:00-03:00"
            repository.tables["runs"][("rod_2",)]["started_at"] = "2026-07-09T10:00:00-03:00"

            latest = {item["modulo"]["slug"]: item["rodagem"] for item in repository.latest_runs_by_module()}
            self.assertEqual("rod_2", latest["contabil"]["id"])
            self.assertIsNone(latest["fiscal"])

            module_queries = []
            original = repository._select

            def counting_select(table, params=None):
                if table == "modules":
                    module_queries.append(params)
                return original(table, params)

            repository._select = counting_select
            runs = repository.runs()

            self.assertEqual(2, len(runs))
            self.assertEqual({"contabil"}, {run["modulo_slug"] for run in runs})
            self.assertEqual(1, len(module_queries))


class AiGroupStatusExpiryTests(unittest.TestCase):
    def test_status_query_releases_interrupted_job(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            payload = _payload_with_repeated_case()
            repository.import_payload(payload)
            run_id = payload["rodagem"]["id_rodagem"]
            created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
            repository.save_ai_job(
                {
                    "id": "aij_preso",
                    "run_id": run_id,
                    "provider": "fake",
                    "model": "fake",
                    "request_json": {},
                    "response_json": {},
                    "status": "running",
                    "error_message": None,
                    "created_at": created,
                    "completed_at": None,
                }
            )
            api = AgentTcApi(Path(temp_dir), repository=repository, require_user_auth=False)

            status, payload = api.route_get(f"/runs/{run_id}/ai-group-status", {})

            self.assertEqual(200, status)
            self.assertEqual("failed", payload["status"])
            self.assertIn("interrompido", payload["error_message"])


class RerunRequestAuthorTests(unittest.TestCase):
    def test_author_comes_from_session_and_stays_out_of_jenkins_config(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            api = AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("marcelo"))

            status, created = api.route_post(
                "/rerun-requests",
                {"vm_name": "a08", "versao": "PROXIMA", "casos_teste": "[3]", "solicitado_por": "outra.pessoa"},
                "Bearer qualquer",
            )

            self.assertEqual(201, status)
            self.assertEqual("marcelo", created["requested_by"])
            config = json.loads(created["config_json"])
            self.assertNotIn("requested_by", config)
            self.assertEqual("[3]", config["casos_teste"])



class RunPresetTests(unittest.TestCase):
    def _apis(self, temp_dir):
        sqlite_repo = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
        memory_repo = InMemoryCanonicalRepository(_write_env(Path(temp_dir)))
        for repository in (sqlite_repo, memory_repo):
            yield AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("marcelo"))

    def test_create_list_update_and_delete(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api in self._apis(temp_dir):
                status, created = api.route_post(
                    "/run-presets",
                    {"nome": "  Fiscal   a07 ", "modo": "simplificada", "config": {"vm_name": "a07", "modulo": "Fiscal", "versao": "PROXIMA", "extra": "x"}},
                    "Bearer x",
                )
                self.assertEqual(201, status)
                self.assertEqual("Fiscal a07", created["name"])
                self.assertEqual({"vm_name": "a07", "modulo": "Fiscal", "versao": "PROXIMA"}, created["config_json"])
                self.assertEqual("marcelo", created["created_by"])

                status, updated = api.route_patch(f"/run-presets/{created['id']}", {"nome": "Fiscal PROXIMA"}, "Bearer x")
                self.assertEqual(200, status)
                self.assertEqual("Fiscal PROXIMA", updated["name"])
                self.assertEqual(created["config_json"], updated["config_json"])
                self.assertEqual(created["created_at"], updated["created_at"])

                status, com_branch = api.route_post(
                    "/run-presets",
                    {"nome": "Folha branch", "modo": "simplificada", "config": {"vm_name": "a09", "modulo": "Folha", "versao": "x", "branch": " minha-branch "}},
                    "Bearer x",
                )
                self.assertEqual(201, status)
                self.assertEqual("minha-branch", com_branch["config_json"]["branch"])
                self.assertEqual(200, api.route_post(f"/run-presets/{com_branch['id']}/delete", {}, "Bearer x")[0])

                status, rows = api.route_get("/run-presets", {}, "Bearer x")
                self.assertEqual(200, status)
                self.assertEqual(["Fiscal PROXIMA"], [row["name"] for row in rows])
                self.assertEqual("PROXIMA", rows[0]["config_json"]["versao"])

                status, _ = api.route_post(f"/run-presets/{created['id']}/delete", {}, "Bearer x")
                self.assertEqual(200, status)
                self.assertEqual([], api.route_get("/run-presets", {}, "Bearer x")[1])
                self.assertEqual(404, api.route_post(f"/run-presets/{created['id']}/delete", {}, "Bearer x")[0])

    def test_configured_preset_keeps_json_but_drops_data_hora(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api in self._apis(temp_dir):
                config = {"vm_name": "a08", "versao": "PROXIMA", "casos_teste": "[9.1.4.1.3]", "ct_desmarcar": "[0.3]", "data_hora": "01/10/2026 10:00:00", "branch": ""}
                status, created = api.route_post("/run-presets", {"nome": "Casos NFe", "modo": "configurada", "config": config}, "Bearer x")
                self.assertEqual(201, status)
                self.assertNotIn("data_hora", created["config_json"])
                self.assertEqual("[9.1.4.1.3]", created["config_json"]["casos_teste"])

    def test_validation_errors(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for api in self._apis(temp_dir):
                base = {"nome": "A", "modo": "configurada", "config": {"casos_teste": "[2]"}}
                self.assertEqual(201, api.route_post("/run-presets", base, "Bearer x")[0])
                status, body = api.route_post("/run-presets", {**base, "nome": "a"}, "Bearer x")
                self.assertEqual((409, "run_preset_name_exists"), (status, body["error"]))
                # Mesmo nome em outro modo e permitido.
                self.assertEqual(201, api.route_post("/run-presets", {**base, "modo": "simplificada"}, "Bearer x")[0])
                self.assertEqual(400, api.route_post("/run-presets", {**base, "nome": " "}, "Bearer x")[0])
                self.assertEqual(400, api.route_post("/run-presets", {**base, "nome": "B", "modo": "x"}, "Bearer x")[0])
                self.assertEqual(400, api.route_post("/run-presets", {**base, "nome": "B", "config": "[2]"}, "Bearer x")[0])
                self.assertEqual(404, api.route_patch("/run-presets/nao-existe", {"nome": "C"}, "Bearer x")[0])

    def test_each_user_owns_their_presets_and_admin_can_delete_any(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            sqlite_repo = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            memory_repo = InMemoryCanonicalRepository(_write_env(Path(temp_dir)))
            for repository in (sqlite_repo, memory_repo):
                ana = AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("ana"))
                joao = AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("joao"))
                chefe = AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("chefe", "admin"))
                base = {"nome": "Fiscal a07", "modo": "simplificada", "config": {"vm_name": "a07"}}
                status, da_ana = ana.route_post("/run-presets", base, "Bearer x")
                self.assertEqual((201, "ana"), (status, da_ana["created_by"]))
                # Outro usuario pode ter uma com o mesmo nome; o mesmo usuario nao.
                status, do_joao = joao.route_post("/run-presets", base, "Bearer x")
                self.assertEqual((201, "joao"), (status, do_joao["created_by"]))
                self.assertEqual((409, "run_preset_name_exists"), (lambda r: (r[0], r[1]["error"]))(ana.route_post("/run-presets", base, "Bearer x")))
                # Todos veem todas (a tela separa "minhas" e "de outros usuarios").
                self.assertEqual(2, len(joao.route_get("/run-presets", {}, "Bearer x")[1]))

                # So o dono altera; nem o admin altera a dos outros.
                for api in (joao, chefe):
                    status, body = api.route_patch(f"/run-presets/{da_ana['id']}", {"nome": "Outro"}, "Bearer x")
                    self.assertEqual((403, "run_preset_not_owner"), (status, body["error"]))
                self.assertEqual(200, ana.route_patch(f"/run-presets/{da_ana['id']}", {"nome": "Fiscal a07 nova"}, "Bearer x")[0])
                # Renomear para um nome que so outro usuario usa e permitido.
                self.assertEqual(200, ana.route_patch(f"/run-presets/{da_ana['id']}", {"nome": "Fiscal a07"}, "Bearer x")[0])

                # Excluir: o dono ou o admin.
                self.assertEqual(403, joao.route_post(f"/run-presets/{da_ana['id']}/delete", {}, "Bearer x")[0])
                self.assertEqual(200, chefe.route_post(f"/run-presets/{da_ana['id']}/delete", {}, "Bearer x")[0])
                self.assertEqual(200, joao.route_post(f"/run-presets/{do_joao['id']}/delete", {}, "Bearer x")[0])
                self.assertEqual([], ana.route_get("/run-presets", {}, "Bearer x")[1])

    def test_old_presets_without_owner_are_read_only_except_admin_delete(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            repository.save_run_preset({"id": "antiga", "name": "Geral", "mode": "simplificada", "config_json": {"vm_name": "a01"},
                                        "created_by": None, "updated_by": None, "created_at": "2026-01-01", "updated_at": "2026-01-01"})
            ana = AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("ana"))
            chefe = AgentTcApi(Path(temp_dir), repository=repository, auth_validator=FixedUser("chefe", "admin"))
            self.assertEqual(403, ana.route_patch("/run-presets/antiga", {"nome": "Minha"}, "Bearer x")[0])
            self.assertEqual(403, ana.route_post("/run-presets/antiga/delete", {}, "Bearer x")[0])
            # Copiar para as minhas com o mesmo nome e permitido (dono diferente).
            self.assertEqual(201, ana.route_post("/run-presets", {"nome": "Geral", "modo": "simplificada", "config": {"vm_name": "a01"}}, "Bearer x")[0])
            self.assertEqual(200, chefe.route_post("/run-presets/antiga/delete", {}, "Bearer x")[0])

    def test_requires_login(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            api = AgentTcApi(Path(temp_dir), repository=SQLiteRepository(Path(temp_dir) / "a.sqlite"), env_path=_write_env(Path(temp_dir)))
            self.assertEqual(401, api.route_get("/run-presets", {}, None)[0])
            self.assertEqual(401, api.route_patch("/run-presets/x", {"nome": "C"}, None)[0])


if __name__ == "__main__":
    unittest.main()

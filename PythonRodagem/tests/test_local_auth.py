from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_tc_core.api_server import AgentTcApi, make_server
from agent_tc_core.local_auth import LocalAuthService, hash_password, verify_password


class InMemoryAuthRepository:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.audit: list[dict] = []

    def auth_user_count(self):
        return len(self.users)

    def auth_user_by_username(self, username_normalized):
        for user in self.users.values():
            if user.get("username_normalized") == username_normalized:
                return dict(user)
        return None

    def auth_user_by_id(self, user_id):
        user = self.users.get(user_id)
        return dict(user) if user else None

    def auth_create_user(self, row):
        self.users[row["id"]] = dict(row)
        return dict(row)

    def auth_update_user(self, user_id, fields):
        if user_id not in self.users:
            return None
        self.users[user_id].update(fields)
        return dict(self.users[user_id])

    def auth_list_users(self):
        return [dict(user) for user in self.users.values()]

    def auth_create_session(self, row):
        self.sessions[row["token_hash"]] = dict(row)
        return dict(row)

    def auth_session_by_token_hash(self, token_hash):
        session = self.sessions.get(token_hash)
        return dict(session) if session else None

    def auth_revoke_session(self, token_hash, revoked_at):
        if token_hash in self.sessions:
            self.sessions[token_hash]["revoked_at"] = revoked_at

    def auth_log_admin_action(self, row):
        self.audit.append(dict(row))

    def modules(self):
        return [{"slug": "contabil", "nome": "Contabil"}]


class LocalAuthTests(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        encoded = hash_password("senha-forte")
        self.assertTrue(verify_password("senha-forte", encoded))
        self.assertFalse(verify_password("senha-errada", encoded))

    def test_first_registered_user_is_admin_and_can_login(self):
        repo = InMemoryAuthRepository()
        service = LocalAuthService(repo)

        user = service.register({"username": "Marcelo", "password": "senha-forte"})
        self.assertEqual("admin", user["role"])
        self.assertEqual("approved", user["status"])

        result = service.login("marcelo", "senha-forte")
        self.assertIn("token", result)
        identity = service.validate("Bearer " + result["token"])
        self.assertEqual(user["id"], identity["id"])

    def test_api_auth_endpoints(self):
        repo = InMemoryAuthRepository()
        api = AgentTcApi(".", repository=repo)

        status, payload = api.route_post("/auth/register", {"username": "admin", "password": "senha-forte"})
        self.assertEqual(201, status)
        self.assertEqual("admin", payload["user"]["role"])

        status, payload = api.route_post("/auth/login", {"username": "admin", "password": "senha-forte"})
        self.assertEqual(200, status)
        token = payload["token"]

        status, payload = api.route_get("/auth/me", {}, "Bearer " + token)
        self.assertEqual(200, status)
        self.assertEqual("admin", payload["user"]["username"])

        status, payload = api.route_get("/auth/users", {}, "Bearer " + token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["users"]))

        status, payload = api.route_get("/modules", {})
        self.assertEqual(401, status)
        self.assertEqual("unauthorized", payload["error"])

        status, payload = api.route_get("/modules", {}, "Bearer " + token)
        self.assertEqual(200, status)
        self.assertEqual("contabil", payload[0]["slug"])

    def test_evidence_files_require_a_valid_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "evidencias" / "run-1" / "erro.txt"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("falha autenticada", encoding="utf-8")
            env_path = root / ".env"
            env_path.write_text(
                f"AGENT_TC_STORAGE=local\nAGENT_TC_STORAGE_ROOT={root / 'evidencias'}\n",
                encoding="utf-8",
            )

            repo = InMemoryAuthRepository()
            service = LocalAuthService(repo)
            service.register({"username": "admin", "password": "senha-forte"})
            token = service.login("admin", "senha-forte")["token"]
            server = make_server("127.0.0.1", 0, root, repository=repo, env_path=env_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/files/run-1/erro.txt"

            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(url, timeout=5)
                self.assertEqual(401, error.exception.code)
                error.exception.close()

                request = Request(url, headers={"Authorization": "Bearer " + token})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(b"falha autenticada", response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_technical_routes_fail_closed_without_bridge_token(self):
        api = AgentTcApi(".", repository=InMemoryAuthRepository(), require_user_auth=False)

        status, payload = api.route_get("/bridge/rerun-requests/requested", {})
        self.assertEqual(503, status)
        self.assertEqual("bridge_token_not_configured", payload["error"])

        status, payload = api.route_post("/ingest", {"payload": {}})
        self.assertEqual(503, status)
        self.assertEqual("ingest_token_not_configured", payload["error"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from agent_tc_core.api_server import AgentTcApi
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


if __name__ == "__main__":
    unittest.main()

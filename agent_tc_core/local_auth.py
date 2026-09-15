from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import AuthenticationError


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")


class LocalAuthError(AuthenticationError):
    def __init__(self, message: str, *, error: str = "auth_error", status: int = 400):
        super().__init__(message)
        self.error = error
        self.status = status


class LocalAuthService:
    def __init__(self, repository: Any, *, session_hours: int = 24):
        self.repository = repository
        self.session_hours = max(1, session_hours)

    @classmethod
    def from_env(cls, repository: Any, env_path: str | Path | None = None) -> "LocalAuthService":
        values = _read_env(env_path)
        return cls(repository, session_hours=_positive_int(values.get("AGENT_TC_AUTH_SESSION_HOURS"), 24))

    def register(self, data: dict[str, Any]) -> dict[str, Any]:
        username = normalize_username(str(data.get("username") or ""))
        password = str(data.get("password") or "")
        if not USERNAME_RE.match(username):
            raise LocalAuthError("Usuario deve ter 3 a 40 caracteres e usar letras, numeros, '.', '_' ou '-'.", error="invalid_username")
        if len(password) < 8:
            raise LocalAuthError("Senha deve ter pelo menos 8 caracteres.", error="invalid_password")
        if self.repository.auth_user_by_username(username):
            raise LocalAuthError("Usuario ja cadastrado.", error="username_already_exists", status=409)

        first_user = self.repository.auth_user_count() == 0
        now = now_iso()
        user = {
            "id": make_id("usr"),
            "auth_user_id": None,
            "username": username,
            "username_normalized": username,
            "first_name": _nullable_text(data.get("first_name")),
            "last_name": _nullable_text(data.get("last_name")),
            "email": _nullable_text(data.get("email")) or f"{username}@agent-tc.local",
            "role": "admin" if first_user else "user",
            "status": "approved" if first_user else "pending",
            "password_hash": hash_password(password),
            "password_updated_at": now,
            "must_change_password": False,
            "approved_at": now if first_user else None,
            "approved_by": None,
            "rejected_at": None,
            "rejected_by": None,
            "rejection_reason": None,
            "disabled_at": None,
            "disabled_by": None,
            "last_login_at": None,
            "failed_login_attempts": 0,
            "locked_until": None,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.auth_create_user(user)
        return public_user(user)

    def login(self, username: str, password: str) -> dict[str, Any]:
        normalized = normalize_username(username)
        user = self.repository.auth_user_by_username(normalized)
        if not user or not user.get("password_hash"):
            raise LocalAuthError("Usuario ou senha invalidos.", error="invalid_credentials", status=401)
        if not verify_password(password, str(user.get("password_hash") or "")):
            self._record_failed_login(user)
            raise LocalAuthError("Usuario ou senha invalidos.", error="invalid_credentials", status=401)
        if user.get("status") != "approved":
            raise LocalAuthError(
                "Usuario ainda nao aprovado." if user.get("status") == "pending" else "Usuario sem acesso.",
                error="user_not_approved",
                status=403,
            )
        if _is_future(user.get("locked_until")):
            raise LocalAuthError("Usuario temporariamente bloqueado.", error="user_locked", status=423)

        now = now_dt()
        token = secrets.token_urlsafe(32)
        session = {
            "id": make_id("sess"),
            "user_id": user["id"],
            "token_hash": hash_token(token),
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(hours=self.session_hours)).isoformat(timespec="seconds"),
            "last_seen_at": now.isoformat(timespec="seconds"),
            "revoked_at": None,
            "ip_address": None,
            "user_agent": None,
        }
        self.repository.auth_create_session(session)
        self.repository.auth_update_user(
            user["id"],
            {"last_login_at": now.isoformat(timespec="seconds"), "failed_login_attempts": 0, "updated_at": now.isoformat(timespec="seconds")},
        )
        updated_user = self.repository.auth_user_by_id(user["id"]) or user
        return {"token": token, "session": public_session(session), "user": public_user(updated_user)}

    def logout(self, authorization: str | None) -> dict[str, Any]:
        token = bearer_token(authorization)
        if token:
            self.repository.auth_revoke_session(hash_token(token), now_iso())
        return {"ok": True}

    def validate(self, authorization: str | None) -> dict[str, Any]:
        token = bearer_token(authorization)
        if not token:
            raise AuthenticationError("Token de acesso ausente")
        session = self.repository.auth_session_by_token_hash(hash_token(token))
        if not session or session.get("revoked_at"):
            raise AuthenticationError("Sessao invalida ou expirada")
        if _is_past(session.get("expires_at")):
            self.repository.auth_revoke_session(hash_token(token), now_iso())
            raise AuthenticationError("Sessao invalida ou expirada")
        user = self.repository.auth_user_by_id(str(session.get("user_id") or ""))
        if not user or user.get("status") != "approved":
            raise AuthenticationError("Usuario sem acesso")
        return {"id": user["id"], "email": user.get("email"), "username": user.get("username"), "role": user.get("role"), "user": public_user(user)}

    def current_user_response(self, authorization: str | None) -> dict[str, Any]:
        identity = self.validate(authorization)
        return {"ok": True, "user": identity["user"]}

    def list_users(self, authorization: str | None) -> list[dict[str, Any]]:
        actor = self.require_admin(authorization)
        rows = self.repository.auth_list_users()
        return [public_user(row, admin_view=True) for row in rows if row.get("id") != actor.get("id") or True]

    def update_user(self, user_id: str, data: dict[str, Any], authorization: str | None) -> dict[str, Any]:
        actor = self.require_admin(authorization)
        current = self.repository.auth_user_by_id(user_id)
        if not current:
            raise LocalAuthError("Usuario nao encontrado.", error="user_not_found", status=404)

        now = now_iso()
        fields: dict[str, Any] = {"updated_at": now}
        if "role" in data:
            role = str(data.get("role") or "")
            if role not in {"user", "admin"}:
                raise LocalAuthError("Role invalida.", error="invalid_role")
            fields["role"] = role
        if "status" in data:
            status = str(data.get("status") or "")
            if status not in {"pending", "approved", "rejected", "disabled"}:
                raise LocalAuthError("Status invalido.", error="invalid_status")
            fields["status"] = status
            if status == "approved":
                fields.update({"approved_at": now, "approved_by": actor["id"], "rejected_at": None, "rejected_by": None, "rejection_reason": None, "disabled_at": None, "disabled_by": None})
            elif status == "rejected":
                fields.update({"rejected_at": now, "rejected_by": actor["id"], "rejection_reason": _nullable_text(data.get("rejection_reason"))})
            elif status == "disabled":
                fields.update({"disabled_at": now, "disabled_by": actor["id"]})
        for key in ("first_name", "last_name", "email"):
            if key in data:
                fields[key] = _nullable_text(data.get(key))
        if "password" in data and data.get("password"):
            password = str(data.get("password"))
            if len(password) < 8:
                raise LocalAuthError("Senha deve ter pelo menos 8 caracteres.", error="invalid_password")
            fields["password_hash"] = hash_password(password)
            fields["password_updated_at"] = now
            fields["must_change_password"] = bool(data.get("must_change_password", False))

        updated = self.repository.auth_update_user(user_id, fields)
        self.repository.auth_log_admin_action(
            {
                "id": make_id("audit"),
                "actor_id": actor["id"],
                "actor_username": actor.get("username"),
                "target_id": user_id,
                "target_username": current.get("username"),
                "action": "user_update",
                "details": {"fields": sorted(fields)},
                "created_at": now,
            }
        )
        return public_user(updated or self.repository.auth_user_by_id(user_id) or current, admin_view=True)

    def require_admin(self, authorization: str | None) -> dict[str, Any]:
        identity = self.validate(authorization)
        user = identity["user"]
        if user.get("role") != "admin":
            raise LocalAuthError("Acesso de administrador requerido.", error="admin_required", status=403)
        return user

    def _record_failed_login(self, user: dict[str, Any]) -> None:
        attempts = int(user.get("failed_login_attempts") or 0) + 1
        fields: dict[str, Any] = {"failed_login_attempts": attempts, "updated_at": now_iso()}
        if attempts >= 10:
            fields["locked_until"] = (now_dt() + timedelta(minutes=15)).isoformat(timespec="seconds")
        self.repository.auth_update_user(user["id"], fields)


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            PASSWORD_ALGORITHM,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()


def normalize_username(username: str) -> str:
    normalized = unicodedata.normalize("NFD", username.strip().lower())
    without_accents = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9._-]", "", without_accents)


def make_id(prefix: str) -> str:
    return prefix + "_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")


def public_user(row: dict[str, Any], *, admin_view: bool = False) -> dict[str, Any]:
    allowed = {
        "id",
        "auth_user_id",
        "username",
        "first_name",
        "last_name",
        "email",
        "role",
        "status",
        "rejection_reason",
        "created_at",
        "updated_at",
        "approved_at",
        "rejected_at",
        "disabled_at",
        "last_login_at",
        "must_change_password",
    }
    if admin_view:
        allowed.update({"approved_by", "rejected_by", "disabled_by", "locked_until", "failed_login_attempts"})
    return {key: row.get(key) for key in allowed if key in row}


def public_session(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("id", "user_id", "created_at", "expires_at", "last_seen_at") if key in row}


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _read_env(path: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path and Path(path).exists():
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("AGENT_TC_AUTH_SESSION_HOURS",):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        text = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_past(value: Any) -> bool:
    dt = _parse_dt(value)
    return bool(dt and dt <= now_dt())


def _is_future(value: Any) -> bool:
    dt = _parse_dt(value)
    return bool(dt and dt > now_dt())

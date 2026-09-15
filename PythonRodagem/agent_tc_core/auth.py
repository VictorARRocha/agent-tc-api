from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AuthenticationError(RuntimeError):
    pass


class SupabaseAuthValidator:
    def __init__(self, *, url: str, api_key: str, timeout: int = 15):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        if not self.url or not self.api_key:
            raise AuthenticationError("Supabase Auth nao configurado no backend")

    @classmethod
    def from_env(cls, env_path: str | Path | None) -> "SupabaseAuthValidator":
        values = _read_env(env_path)
        return cls(
            url=values.get("SUPABASE_URL", ""),
            api_key=values.get("SUPABASE_SERVICE_ROLE_KEY") or values.get("SUPABASE_SECRET_KEY") or "",
        )

    def validate(self, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthenticationError("Token de acesso ausente")
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise AuthenticationError("Token de acesso ausente")
        request = Request(
            self.url + "/auth/v1/user",
            headers={"apikey": self.api_key, "Authorization": "Bearer " + token},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                user = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AuthenticationError("Sessao invalida ou expirada") from exc
        except URLError as exc:
            raise AuthenticationError("Nao foi possivel validar a sessao") from exc
        if not user.get("id"):
            raise AuthenticationError("Usuario autenticado nao identificado")
        return {"id": user["id"], "email": user.get("email")}


def _read_env(path: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path and Path(path).exists():
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY"):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values

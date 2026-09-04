from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_BUCKET = "agent-tc-evidences"
SIGNED_URL_SECONDS = 60 * 60 * 24 * 7


class StorageHttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:500]}")


class StorageAdapter(Protocol):
    provider: str
    bucket: str
    dry_run: bool

    def initialize(self) -> None:
        ...

    def upload_file(self, local_path: str | Path, storage_path: str, mime_type: str | None) -> dict[str, Any]:
        ...

    def delete_paths(self, storage_paths: list[str]) -> dict[str, Any]:
        ...


class SupabaseStorageAdapter:
    provider = "supabase"

    def __init__(
        self,
        *,
        url: str,
        service_key: str,
        bucket: str,
        storage_public: bool,
        dry_run: bool = False,
    ):
        self.url = url.rstrip("/")
        self.service_key = service_key
        self.bucket = bucket
        self.storage_public = storage_public
        self.dry_run = dry_run

    def initialize(self) -> None:
        if self.dry_run:
            return
        self.ensure_bucket()

    def ensure_bucket(self) -> None:
        if self.dry_run:
            return
        try:
            self._storage_json("GET", f"/bucket/{quote(self.bucket, safe='')}")
            return
        except StorageHttpError as exc:
            if exc.status != 404:
                raise
        self._storage_json(
            "POST",
            "/bucket",
            {
                "id": self.bucket,
                "name": self.bucket,
                "public": self.storage_public,
            },
        )

    def upload_file(self, local_path: str | Path, storage_path: str, mime_type: str | None) -> dict[str, Any]:
        local_path = Path(local_path)
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(str(local_path))
        if self.dry_run:
            return {
                "provider": self.provider,
                "bucket": self.bucket,
                "storage_path": storage_path,
                "public_url": self.public_url(storage_path) if self.storage_public else "",
                "signed_url": "",
                "signed_url_expires_at": None,
            }
        content = local_path.read_bytes()
        quoted_path = quote(storage_path.replace("\\", "/"), safe="/")
        self._storage_bytes(
            "POST",
            f"/object/{quote(self.bucket, safe='')}/{quoted_path}",
            content,
            {
                "Content-Type": mime_type or "application/octet-stream",
                "x-upsert": "true",
            },
        )
        signed_url = ""
        signed_expires_at = None
        if not self.storage_public:
            signed_url = self.create_signed_url(storage_path, SIGNED_URL_SECONDS)
            signed_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_SECONDS)
            ).isoformat(timespec="seconds")
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "storage_path": storage_path,
            "public_url": self.public_url(storage_path) if self.storage_public else "",
            "signed_url": signed_url,
            "signed_url_expires_at": signed_expires_at,
        }

    def delete_paths(self, storage_paths: list[str]) -> dict[str, Any]:
        unique_paths = sorted({normalize_storage_path(path) for path in storage_paths if path})
        result: dict[str, Any] = {"requested": len(unique_paths), "deleted": 0, "errors": []}
        if self.dry_run:
            return result
        for storage_path in unique_paths:
            quoted_path = quote(storage_path, safe="/")
            try:
                self._storage_bytes(
                    "DELETE",
                    f"/object/{quote(self.bucket, safe='')}/{quoted_path}",
                    b"",
                    {},
                )
                result["deleted"] += 1
            except StorageHttpError as exc:
                if exc.status == 404:
                    result["deleted"] += 1
                    continue
                result["errors"].append(
                    {
                        "storage_path": storage_path,
                        "status": exc.status,
                        "message": exc.body[:500],
                    }
                )
        return result

    def public_url(self, storage_path: str) -> str:
        return f"{self.url}/storage/v1/object/public/{quote(self.bucket, safe='')}/{quote(storage_path, safe='/')}"

    def create_signed_url(self, storage_path: str, expires_in: int) -> str:
        result = self._storage_json(
            "POST",
            f"/object/sign/{quote(self.bucket, safe='')}/{quote(storage_path, safe='/')}",
            {"expiresIn": expires_in},
        )
        signed = result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
        if not signed:
            return ""
        if signed.startswith("http"):
            return signed
        return self.url + "/storage/v1" + signed

    def _storage_json(self, method: str, path: str, body: Any | None = None) -> Any:
        headers = {
            "Accept": "application/json",
            "apikey": self.service_key,
            "Authorization": "Bearer " + self.service_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        response = self._request_bytes(method, self.url + "/storage/v1" + path, headers, data)
        if not response:
            return None
        return json.loads(response.decode("utf-8"))

    def _storage_bytes(self, method: str, path: str, body: bytes, extra_headers: dict[str, str]) -> bytes:
        headers = {
            "apikey": self.service_key,
            "Authorization": "Bearer " + self.service_key,
            **extra_headers,
        }
        return self._request_bytes(method, self.url + "/storage/v1" + path, headers, body)

    def _request_bytes(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: bytes | None,
    ) -> bytes:
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise StorageHttpError(method, url, exc.code, body) from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {url} -> {exc}") from exc


class LocalFileStorageAdapter:
    provider = "local"

    def __init__(
        self,
        *,
        root: str | Path,
        bucket: str = "agent-tc-evidences",
        public_base_url: str = "",
        dry_run: bool = False,
    ):
        self.root = Path(root)
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")
        self.dry_run = dry_run

    def initialize(self) -> None:
        if not self.dry_run:
            self.root.mkdir(parents=True, exist_ok=True)

    def upload_file(self, local_path: str | Path, storage_path: str, mime_type: str | None) -> dict[str, Any]:
        local_path = Path(local_path)
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(str(local_path))
        normalized_path = normalize_storage_path(storage_path)
        target = safe_storage_target(self.root, normalized_path)
        if not self.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, target)
        public_url = self.public_url(normalized_path)
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "storage_path": normalized_path,
            "public_url": public_url,
            "signed_url": public_url,
            "signed_url_expires_at": None,
        }

    def delete_paths(self, storage_paths: list[str]) -> dict[str, Any]:
        unique_paths = sorted({normalize_storage_path(path) for path in storage_paths if path})
        result: dict[str, Any] = {"requested": len(unique_paths), "deleted": 0, "errors": []}
        if self.dry_run:
            return result
        for storage_path in unique_paths:
            try:
                target = safe_storage_target(self.root, storage_path)
                if target.exists() and target.is_file():
                    target.unlink()
                result["deleted"] += 1
                prune_empty_parents(target.parent, self.root)
            except Exception as exc:
                result["errors"].append({"storage_path": storage_path, "message": str(exc)[:500]})
        return result

    def public_url(self, storage_path: str) -> str:
        if not self.public_base_url:
            return ""
        return self.public_base_url + "/" + quote(normalize_storage_path(storage_path), safe="/")


def storage_from_env(
    env: dict[str, str],
    *,
    supabase_url: str,
    supabase_service_key: str,
    default_bucket: str,
    storage_public: bool,
    dry_run: bool,
) -> StorageAdapter:
    provider = (env.get("AGENT_TC_STORAGE") or env.get("STORAGE_PROVIDER") or "supabase").strip().lower()
    bucket = env.get("AGENT_TC_STORAGE_BUCKET") or env.get("SUPABASE_BUCKET") or default_bucket
    if provider == "supabase":
        return SupabaseStorageAdapter(
            url=supabase_url,
            service_key=supabase_service_key,
            bucket=bucket,
            storage_public=storage_public,
            dry_run=dry_run,
        )
    if provider in {"local", "file", "filesystem"}:
        root = env.get("AGENT_TC_STORAGE_ROOT") or env.get("LOCAL_STORAGE_ROOT")
        if not root:
            raise ValueError("AGENT_TC_STORAGE_ROOT precisa ser configurado quando AGENT_TC_STORAGE=local")
        return LocalFileStorageAdapter(
            root=root,
            bucket=bucket,
            public_base_url=env.get("AGENT_TC_PUBLIC_BASE_URL") or env.get("LOCAL_STORAGE_PUBLIC_BASE_URL") or "",
            dry_run=dry_run,
        )
    if provider == "s3":
        raise NotImplementedError("AGENT_TC_STORAGE=s3 ainda nao foi implementado. Use local ou supabase por enquanto.")
    raise ValueError(f"AGENT_TC_STORAGE invalido: {provider}")


def normalize_storage_path(storage_path: str) -> str:
    return storage_path.replace("\\", "/").strip("/")


def safe_storage_target(root: Path, storage_path: str) -> Path:
    root = root.resolve()
    target = (root / normalize_storage_path(storage_path)).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"storage_path fora da raiz configurada: {storage_path}")
    return target


def prune_empty_parents(path: Path, root: Path) -> None:
    root = root.resolve()
    current = path.resolve()
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent

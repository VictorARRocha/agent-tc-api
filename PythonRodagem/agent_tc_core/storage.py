from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote


DEFAULT_BUCKET = "agent-tc-evidences"


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
            "signed_url": "",
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
    default_bucket: str,
    dry_run: bool,
    default_provider: str = "local",
) -> StorageAdapter:
    provider = (env.get("AGENT_TC_STORAGE") or env.get("STORAGE_PROVIDER") or default_provider).strip().lower()
    bucket = env.get("AGENT_TC_STORAGE_BUCKET") or default_bucket
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
        raise NotImplementedError("AGENT_TC_STORAGE=s3 ainda nao foi implementado. Use local por enquanto.")
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

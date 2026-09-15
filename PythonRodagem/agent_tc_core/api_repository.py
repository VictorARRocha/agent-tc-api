from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .constants import MODULE_CODES_BY_ID
from .utils import ascii_lower


OFFICIAL_MODULES = [
    {"id_modulo": "mod_folha", "nome": "Folha", "slug": "folha", "ordem": 1},
    {"id_modulo": "mod_fiscal", "nome": "Fiscal", "slug": "fiscal", "ordem": 2},
    {"id_modulo": "mod_contabil", "nome": "Cont\u00e1bil", "slug": "contabil", "ordem": 3},
    {"id_modulo": "mod_gestao", "nome": "Gest\u00e3o", "slug": "gestao", "ordem": 4},
    {"id_modulo": "mod_financeiro", "nome": "Financeiro", "slug": "financeiro", "ordem": 5},
    {"id_modulo": "mod_geral", "nome": "Geral", "slug": "geral", "ordem": 6},
    {"id_modulo": "mod_suprema", "nome": "Suprema", "slug": "suprema", "ordem": 7},
    {"id_modulo": "mod_practice", "nome": "Practice", "slug": "practice", "ordem": 8},
]

MODULE_BY_SLUG = {module["slug"]: module for module in OFFICIAL_MODULES}
SLUG_BY_MODULE_ID = {module["id_modulo"]: module["slug"] for module in OFFICIAL_MODULES}
DEFAULT_ENV = Path(__file__).resolve().parents[1] / ".env"


def slugify(value: object) -> str:
    text = ascii_lower(value)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


class LocalPayloadRepository:
    def __init__(self, logs_root: str | Path):
        self.logs_root = Path(logs_root)

    def modules(self) -> list[dict[str, Any]]:
        return [
            {
                **module,
                "codes": list(MODULE_CODES_BY_ID.get(module["id_modulo"], ())),
            }
            for module in OFFICIAL_MODULES
        ]

    def runs(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        rows = [self._run_row(payload) for payload in self._payloads()]
        if module_slug:
            rows = [row for row in rows if row.get("modulo_slug") == module_slug]
        return sorted(rows, key=lambda row: row.get("data_inicio") or "", reverse=True)

    def run(self, run_id: str) -> dict[str, Any] | None:
        payload = self.payload(run_id)
        return self._run_row(payload) if payload else None

    def payload(self, run_id: str) -> dict[str, Any] | None:
        for payload in self._payloads():
            rodagem = payload.get("rodagem") or {}
            if rodagem.get("id_rodagem") == run_id:
                return payload
        return None

    def failures(self, run_id: str) -> list[dict[str, Any]]:
        payload = self.payload(run_id)
        if not payload:
            return []
        return list(payload.get("falhas") or [])

    def evidences(self, run_id: str) -> list[dict[str, Any]]:
        payload = self.payload(run_id)
        if not payload:
            return []
        return list(payload.get("evidencias") or [])

    def groups(self, run_id: str) -> list[dict[str, Any]]:
        payload = self.payload(run_id)
        if not payload:
            return []
        return list(payload.get("agrupamentos") or payload.get("agrupamentos_shadow") or [])

    def next_steps(self, run_id: str) -> list[dict[str, Any]]:
        payload = self.payload(run_id)
        if not payload:
            return []
        return list(payload.get("proximos_passos") or [])

    def performance(self, run_id: str) -> list[dict[str, Any]]:
        payload = self.payload(run_id)
        if not payload:
            return []
        return list(payload.get("atrasos_rodagem") or [])

    def testcase_hierarchy(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        codes = set()
        if module_slug and module_slug in MODULE_BY_SLUG:
            module_id = MODULE_BY_SLUG[module_slug]["id_modulo"]
            codes = set(MODULE_CODES_BY_ID.get(module_id, ()))

        by_id: dict[str, dict[str, Any]] = {}
        for payload in self._payloads():
            for row in payload.get("testcase_hierarchy") or []:
                if codes and str(row.get("modulo_codigo") or "") not in codes:
                    continue
                node_id = str(row.get("node_id") or "")
                if node_id:
                    by_id[node_id] = row

        return sorted(by_id.values(), key=lambda row: _node_sort_key(str(row.get("node_id") or "")))

    def record_rerun_request(self, request: dict[str, Any]) -> dict[str, Any]:
        path = self.logs_root / "api_rerun_requests.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        current = {
            "id": "local_" + re.sub(r"[^0-9]", "", _now_stamp()),
            "status": "solicitado_local",
            **request,
        }


class RemoteApiRepository:
    """Envia payload e evidencias da VM para a API central do Agent TC."""

    def __init__(
        self,
        env_path: str | Path | None = None,
        *,
        api_url: str | None = None,
        token: str | None = None,
        dry_run: bool = False,
    ):
        env = _read_env(Path(env_path or DEFAULT_ENV))
        self.api_url = (api_url or env.get("AGENT_TC_API_URL") or env.get("AGENT_TC_INGEST_API_URL") or "").rstrip("/")
        self.token = token if token is not None else (env.get("AGENT_TC_API_TOKEN") or env.get("AGENT_TC_BRIDGE_TOKEN") or "")
        self.dry_run = dry_run
        if not self.api_url:
            raise ValueError("AGENT_TC_API_URL nao configurado para --backend api")

    def import_payload(self, payload: dict[str, Any], source: str = "payload") -> dict[str, Any]:
        files = self._collect_evidence_files(payload)
        body = {
            "payload": payload,
            "source": source,
            "dry_run": self.dry_run,
            "files": files,
        }
        return self._post_json("/ingest", body)

    def _collect_evidence_files(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for evidence in payload.get("evidencias") or []:
            evidence_id = str(evidence.get("id_evidencia") or "")
            local_path = Path(str(evidence.get("caminho_evidencia") or ""))
            if not evidence_id or not local_path.is_file():
                continue
            content = local_path.read_bytes()
            files.append(
                {
                    "id_evidencia": evidence_id,
                    "nome_arquivo": evidence.get("nome_arquivo") or local_path.name,
                    "caminho_origem": str(local_path),
                    "mime_type": evidence.get("mime_type") or "application/octet-stream",
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        return files

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url + path,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )
        if self.token:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Agent TC API HTTP {exc.code}: {error_body[:2000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Falha ao conectar na Agent TC API em {self.api_url}: {exc}") from exc
        return json.loads(response_body) if response_body.strip() else {}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(current, ensure_ascii=False) + "\n")
        return current

    def rerun_requests(self) -> list[dict[str, Any]]:
        path = self.logs_root / "api_rerun_requests.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(rows))

    def _payload_files(self) -> list[Path]:
        if not self.logs_root.exists():
            return []
        return sorted(
            self.logs_root.rglob("shadow_payload.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _payloads(self) -> list[dict[str, Any]]:
        payloads = []
        for path in self._payload_files():
            try:
                payloads.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return payloads

    def _run_row(self, payload: dict[str, Any]) -> dict[str, Any]:
        rodagem = dict(payload.get("rodagem") or {})
        module = dict(payload.get("modulo") or {})
        module_id = str(module.get("id_modulo") or rodagem.get("fk_modulo") or "")
        return {
            **rodagem,
            "modulo": module,
            "modulo_slug": SLUG_BY_MODULE_ID.get(module_id) or slugify(module.get("nome") or ""),
            "falhas_count": len(payload.get("falhas") or []),
            "evidencias_count": len(payload.get("evidencias") or []),
            "diferencas_count": len(payload.get("diferencas_relatorio") or []),
        }


def _node_sort_key(node_id: str) -> tuple[int, ...]:
    parts = []
    for item in node_id.split("."):
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _now_stamp() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="microseconds")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values

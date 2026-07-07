from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .api_repository import LocalPayloadRepository
from .pipeline import run_shadow_pipeline


class AgentTcApi:
    def __init__(self, logs_root: str | Path, repository: Any | None = None, *, read_only: bool = False):
        self.logs_root = Path(logs_root)
        self.repository = repository or LocalPayloadRepository(self.logs_root)
        self.read_only = read_only

    def route_get(self, path: str, query: dict[str, list[str]]) -> tuple[int, Any]:
        parts = _path_parts(path)
        if not parts:
            return HTTPStatus.OK, self.index()
        if parts == ["health"]:
            return HTTPStatus.OK, {"ok": True, "service": "agent-tc-api"}
        if parts == ["modules"]:
            return HTTPStatus.OK, self.repository.modules()
        if len(parts) == 3 and parts[0] == "modules" and parts[2] == "runs":
            return HTTPStatus.OK, self.repository.runs(parts[1])
        if len(parts) == 1 and parts[0] == "runs":
            module = _first(query, "module")
            return HTTPStatus.OK, self.repository.runs(module)
        if len(parts) == 2 and parts[0] == "runs":
            row = self.repository.run(parts[1])
            return (HTTPStatus.OK, row) if row else (HTTPStatus.NOT_FOUND, {"error": "run_not_found"})
        if len(parts) == 3 and parts[0] == "runs":
            return self._run_child(parts[1], parts[2])
        if len(parts) == 3 and parts[0] == "failures" and parts[2] == "evidences":
            if hasattr(self.repository, "evidences_by_failure"):
                return HTTPStatus.OK, self.repository.evidences_by_failure(parts[1])
            return HTTPStatus.NOT_FOUND, {"error": "not_supported"}
        if parts == ["testcase-hierarchy"]:
            return HTTPStatus.OK, self.repository.testcase_hierarchy(_first(query, "module"))
        if parts == ["rerun-requests"]:
            return HTTPStatus.OK, self.repository.rerun_requests()
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path}

    def route_post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if self.read_only:
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only_api"}
        parts = _path_parts(path)
        if parts == ["analyze"]:
            return self._analyze(body)
        if parts == ["rerun-requests"]:
            return HTTPStatus.CREATED, self.repository.record_rerun_request(body)
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path}

    def index(self) -> dict[str, Any]:
        return {
            "service": "Agent TC API",
            "mode": "local-json",
            "logs_root": str(self.logs_root),
            "endpoints": [
                "GET /health",
                "GET /modules",
                "GET /modules/{slug}/runs",
                "GET /runs",
                "GET /runs/{id}",
                "GET /runs/{id}/payload",
                "GET /runs/{id}/failures",
                "GET /runs/{id}/evidences",
                "GET /runs/{id}/groups",
                "GET /runs/{id}/group-links",
                "GET /runs/{id}/next-steps",
                "GET /runs/{id}/performance",
                "GET /runs/{id}/reexecutable-cases",
                "GET /failures/{id}/evidences",
                "GET /testcase-hierarchy?module=contabil",
                "GET /rerun-requests",
                "POST /rerun-requests",
                "POST /analyze",
            ],
        }

    def _run_child(self, run_id: str, child: str) -> tuple[int, Any]:
        if not self.repository.run(run_id):
            return HTTPStatus.NOT_FOUND, {"error": "run_not_found"}
        if child == "payload":
            return HTTPStatus.OK, self.repository.payload(run_id)
        if child == "failures":
            return HTTPStatus.OK, self.repository.failures(run_id)
        if child == "evidences":
            return HTTPStatus.OK, self.repository.evidences(run_id)
        if child == "groups":
            return HTTPStatus.OK, self.repository.groups(run_id)
        if child == "group-links":
            if hasattr(self.repository, "group_links"):
                return HTTPStatus.OK, self.repository.group_links(run_id)
            return HTTPStatus.OK, {}
        if child == "next-steps":
            return HTTPStatus.OK, self.repository.next_steps(run_id)
        if child == "performance":
            return HTTPStatus.OK, self.repository.performance(run_id)
        if child == "reexecutable-cases":
            if hasattr(self.repository, "reexecutable_cases"):
                return HTTPStatus.OK, self.repository.reexecutable_cases(run_id)
            return HTTPStatus.OK, []
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "child": child}

    def _analyze(self, body: dict[str, Any]) -> tuple[int, Any]:
        run_folder = body.get("run_folder")
        mds_path = body.get("mds_path") or body.get("mds")
        output_root = body.get("output_root") or str(self.logs_root)
        if not run_folder or not mds_path:
            return HTTPStatus.BAD_REQUEST, {
                "error": "missing_fields",
                "required": ["run_folder", "mds_path"],
            }
        report_dir, payload = run_shadow_pipeline(
            run_folder=run_folder,
            mds_path=mds_path,
            output_root=output_root,
            vm_name=body.get("vm_name"),
        )
        import_result = None
        if hasattr(self.repository, "import_payload"):
            import_result = self.repository.import_payload(
                payload,
                source=str(Path(report_dir) / "shadow_payload.json"),
            )
        return HTTPStatus.CREATED, {
            "ok": True,
            "report_dir": str(report_dir),
            "import_result": import_result,
            "rodagem": payload.get("rodagem"),
            "falhas": len(payload.get("falhas") or []),
            "evidencias": len(payload.get("evidencias") or []),
            "diferencas": len(payload.get("diferencas_relatorio") or []),
            "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
        }


class AgentTcRequestHandler(BaseHTTPRequestHandler):
    api: AgentTcApi

    def do_OPTIONS(self) -> None:
        self._send(HTTPStatus.NO_CONTENT, None)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            status, payload = self.api.route_get(parsed.path, parse_qs(parsed.query))
            self._send(status, payload)
        except Exception as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "message": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            status, payload = self.api.route_post(parsed.path, self._read_json_body())
            self._send(status, payload)
        except Exception as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "message": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _send(self, status: int, payload: Any) -> None:
        body = b""
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def make_server(
    host: str,
    port: int,
    logs_root: str | Path,
    repository: Any | None = None,
    *,
    read_only: bool = False,
) -> ThreadingHTTPServer:
    api = AgentTcApi(logs_root, repository=repository, read_only=read_only)

    class Handler(AgentTcRequestHandler):
        pass

    Handler.api = api
    return ThreadingHTTPServer((host, port), Handler)


def _path_parts(path: str) -> list[str]:
    return [unquote(part) for part in path.strip("/").split("/") if part]


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .ai_grouping import (
    AiGroupingError,
    AiGroupingInvalidJsonError,
    AiGroupingValidationError,
    ai_client_from_env,
    build_ai_grouping_input,
    group_failures_in_batches,
    make_job_id,
    materialize_ai_rows,
    write_ai_dry_run,
)
from .api_repository import LocalPayloadRepository
from .auth import AuthenticationError
from .local_auth import LocalAuthError, LocalAuthService
from .pipeline import run_shadow_pipeline
from .storage import safe_storage_target


class AgentTcApi:
    def __init__(
        self,
        logs_root: str | Path,
        repository: Any | None = None,
        *,
        read_only: bool = False,
        env_path: str | Path | None = None,
        openai_client: Any | None = None,
        auth_validator: Any | None = None,
        require_ai_auth: bool = True,
        require_user_auth: bool = True,
    ):
        self.logs_root = Path(logs_root)
        self.repository = repository or LocalPayloadRepository(self.logs_root)
        self.read_only = read_only
        self.env_path = Path(env_path) if env_path else None
        self.openai_client = openai_client
        self.auth_validator = auth_validator
        self.require_ai_auth = require_ai_auth
        self.require_user_auth = require_user_auth
        self.local_files_root = _local_files_root(self.env_path)

    def route_get(self, path: str, query: dict[str, list[str]], authorization: str | None = None) -> tuple[int, Any]:
        parts = _path_parts(path)
        if not parts:
            return HTTPStatus.OK, self.index()
        if parts == ["health"]:
            return HTTPStatus.OK, {"ok": True, "service": "agent-tc-api"}
        if len(parts) >= 2 and parts[0] == "auth":
            return self._auth_get(parts[1:], authorization)
        if len(parts) == 3 and parts[0] == "bridge" and parts[1] == "rerun-requests":
            return self._bridge_get(parts[2], authorization)
        auth_error = self._user_auth_error(authorization)
        if auth_error:
            return auth_error
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

    def route_post(self, path: str, body: dict[str, Any], authorization: str | None = None) -> tuple[int, Any]:
        parts = _path_parts(path)
        if len(parts) >= 2 and parts[0] == "auth":
            return self._auth_post(parts[1:], body, authorization)
        if self.read_only:
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only_api"}
        if parts == ["ingest"]:
            return self._ingest(body, authorization)
        if len(parts) == 4 and parts[0] == "bridge" and parts[1] == "rerun-requests":
            return self._bridge_post(parts[2], parts[3], body, authorization)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "ai-group":
            return self._ai_group(parts[1], body, authorization)
        auth_error = self._user_auth_error(authorization)
        if auth_error:
            return auth_error
        if parts == ["analyze"]:
            return self._analyze(body)
        if parts == ["rerun-requests"]:
            return HTTPStatus.CREATED, self.repository.record_rerun_request(body)
        if len(parts) == 3 and parts[0] == "rerun-requests" and parts[2] == "cancel":
            if not hasattr(self.repository, "cancel_rerun_request"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_cancel"}
            result = self.repository.cancel_rerun_request(parts[1], body)
            if result is None:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": "rerun_request_not_found"}
            if isinstance(result, dict) and result.get("error") == "rerun_request_not_cancellable":
                return HTTPStatus.CONFLICT, {"ok": False, **result}
            return HTTPStatus.ACCEPTED, {"ok": True, "rerun_request": result}
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path}

    def index(self) -> dict[str, Any]:
        return {
            "service": "Agent TC API",
            "mode": _repository_mode(self.repository),
            "logs_root": str(self.logs_root),
            "endpoints": [
                "GET /health",
                "POST /auth/register",
                "POST /auth/login",
                "POST /auth/logout",
                "GET /auth/me",
                "GET /auth/users",
                "PATCH /auth/users/{id}",
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
                "GET /runs/{id}/ai-group-status",
                "GET /runs/{id}/ai-group-debug",
                "GET /failures/{id}/evidences",
                "GET /testcase-hierarchy?module=contabil",
                "GET /rerun-requests",
                "POST /rerun-requests",
                "POST /rerun-requests/{id}/cancel",
                "GET /bridge/rerun-requests/requested",
                "GET /bridge/rerun-requests/active",
                "GET /bridge/rerun-requests/cancel-requested",
                "POST /bridge/rerun-requests/{id}/claim",
                "POST /bridge/rerun-requests/{id}/update",
                "POST /analyze",
                "POST /ingest",
                "POST /runs/{id}/ai-group",
            ],
        }

    def route_patch(self, path: str, body: dict[str, Any], authorization: str | None = None) -> tuple[int, Any]:
        parts = _path_parts(path)
        if len(parts) >= 2 and parts[0] == "auth":
            return self._auth_patch(parts[1:], body, authorization)
        if self.read_only:
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only_api"}
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path}

    def _auth_get(self, parts: list[str], authorization: str | None) -> tuple[int, Any]:
        service = self._local_auth_service()
        if not service:
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "local_auth_not_supported"}
        try:
            if parts == ["me"]:
                return HTTPStatus.OK, service.current_user_response(authorization)
            if parts == ["users"]:
                return HTTPStatus.OK, {"ok": True, "users": service.list_users(authorization)}
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": "auth_endpoint_not_found"}
        except LocalAuthError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}

    def _auth_post(self, parts: list[str], body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        service = self._local_auth_service()
        if not service:
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "local_auth_not_supported"}
        try:
            if parts == ["register"]:
                return HTTPStatus.CREATED, {"ok": True, "user": service.register(body)}
            if parts == ["login"]:
                return HTTPStatus.OK, {"ok": True, **service.login(str(body.get("username") or ""), str(body.get("password") or ""))}
            if parts == ["logout"]:
                return HTTPStatus.OK, service.logout(authorization)
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": "auth_endpoint_not_found"}
        except LocalAuthError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}

    def _auth_patch(self, parts: list[str], body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        service = self._local_auth_service()
        if not service:
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "local_auth_not_supported"}
        try:
            if len(parts) == 2 and parts[0] == "users":
                return HTTPStatus.OK, {"ok": True, "user": service.update_user(parts[1], body, authorization)}
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": "auth_endpoint_not_found"}
        except LocalAuthError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}

    def _local_auth_service(self) -> LocalAuthService | None:
        required = (
            "auth_user_count",
            "auth_user_by_username",
            "auth_user_by_id",
            "auth_create_user",
            "auth_update_user",
            "auth_list_users",
            "auth_create_session",
            "auth_session_by_token_hash",
            "auth_revoke_session",
            "auth_log_admin_action",
        )
        if not all(hasattr(self.repository, name) for name in required):
            return None
        return LocalAuthService.from_env(self.repository, self.env_path)

    def _validate_user_auth(self, authorization: str | None) -> dict[str, Any]:
        if self.auth_validator:
            return self.auth_validator.validate(authorization)
        auth_backend = _env_value(self.env_path, "AGENT_TC_AUTH_BACKEND").strip().lower() or "local"
        if auth_backend != "local":
            raise AuthenticationError("AGENT_TC_AUTH_BACKEND invalido. Use local.")
        local_auth = self._local_auth_service()
        if local_auth:
            return local_auth.validate(authorization)
        raise AuthenticationError("Auth local nao suportado pelo repository atual")

    def _user_auth_error(self, authorization: str | None) -> tuple[int, Any] | None:
        if not self.require_user_auth:
            return None
        try:
            self._validate_user_auth(authorization)
        except LocalAuthError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {
                "ok": False,
                "error": "unauthorized",
                "message": str(exc),
            }
        return None

    def _bridge_get(self, child: str, authorization: str | None) -> tuple[int, Any]:
        auth_error = self._bridge_auth_error(authorization)
        if auth_error:
            return auth_error
        if child == "requested":
            if hasattr(self.repository, "bridge_requested_rerun_requests"):
                return HTTPStatus.OK, self.repository.bridge_requested_rerun_requests()
        if child == "active":
            if hasattr(self.repository, "bridge_active_rerun_requests"):
                return HTTPStatus.OK, self.repository.bridge_active_rerun_requests()
        if child == "cancel-requested":
            if hasattr(self.repository, "bridge_cancel_requested_rerun_requests"):
                return HTTPStatus.OK, self.repository.bridge_cancel_requested_rerun_requests()
        return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_bridge"}

    def _bridge_post(self, request_id: str, action: str, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        auth_error = self._bridge_auth_error(authorization)
        if auth_error:
            return auth_error
        if action == "claim":
            if not hasattr(self.repository, "bridge_claim_rerun_request"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_bridge"}
            return HTTPStatus.OK, {"ok": True, "claimed": bool(self.repository.bridge_claim_rerun_request(request_id))}
        if action == "update":
            if not hasattr(self.repository, "bridge_update_rerun_request"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_bridge"}
            row = self.repository.bridge_update_rerun_request(request_id, body)
            return (HTTPStatus.OK, {"ok": True, "rerun_request": row}) if row else (HTTPStatus.NOT_FOUND, {"ok": False, "error": "rerun_request_not_found"})
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "bridge_action_not_found", "action": action}

    def _bridge_auth_error(self, authorization: str | None) -> tuple[int, Any] | None:
        token = _env_value(self.env_path, "AGENT_TC_BRIDGE_TOKEN")
        if not token:
            return HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "bridge_token_not_configured",
            }
        expected = "Bearer " + token
        if authorization != expected:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized_bridge"}
        return None

    def _ingest_auth_error(self, authorization: str | None) -> tuple[int, Any] | None:
        token = _env_value(self.env_path, "AGENT_TC_API_TOKEN") or _env_value(self.env_path, "AGENT_TC_BRIDGE_TOKEN")
        if not token:
            return HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "ingest_token_not_configured",
            }
        expected = "Bearer " + token
        if authorization != expected:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized_ingest"}
        return None

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
        if child == "ai-group-status":
            if hasattr(self.repository, "ai_grouping_status"):
                return HTTPStatus.OK, self.repository.ai_grouping_status(run_id)
            return HTTPStatus.OK, {"run_id": run_id, "status": "not_supported", "grouped": False}
        if child == "ai-group-debug":
            if hasattr(self.repository, "ai_grouping_debug"):
                return HTTPStatus.OK, self.repository.ai_grouping_debug(run_id)
            return HTTPStatus.OK, {"run_id": run_id, "status": "not_supported"}
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
            project_suite_path=body.get("project_suite_path") or body.get("project_suite"),
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

    def _ingest(self, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        auth_error = self._ingest_auth_error(authorization)
        if auth_error:
            return auth_error
        if not hasattr(self.repository, "import_payload"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_import"}
        payload = body.get("payload")
        if not isinstance(payload, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_payload"}
        run_id = str((payload.get("rodagem") or {}).get("id_rodagem") or "").strip()
        if not run_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_run_id"}
        try:
            staged = self._stage_ingest_files(payload, body.get("files") or [])
            import_result = None
            if body.get("dry_run"):
                import_result = {"dry_run": True, "staged_files": staged}
            else:
                import_result = self.repository.import_payload(
                    payload,
                    source=str(body.get("source") or "api_ingest"),
                )
            return HTTPStatus.CREATED, {
                "ok": True,
                "run_id": run_id,
                "staged_files": staged,
                "import_result": import_result,
                "falhas": len(payload.get("falhas") or []),
                "evidencias": len(payload.get("evidencias") or []),
                "diferencas": len(payload.get("diferencas_relatorio") or []),
                "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
            }
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_ingest_payload", "message": str(exc)}

    def _stage_ingest_files(self, payload: dict[str, Any], files: list[Any]) -> int:
        run_id = str((payload.get("rodagem") or {}).get("id_rodagem") or "run").strip()
        by_id: dict[str, dict[str, Any]] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("id_evidencia") or "").strip()
            if evidence_id:
                by_id[evidence_id] = item

        staged = 0
        staging_root = self.logs_root / "api_ingest_files" / _safe_segment(run_id)
        for evidence in payload.get("evidencias") or []:
            evidence_id = str(evidence.get("id_evidencia") or "").strip()
            item = by_id.get(evidence_id)
            if not item:
                continue
            raw_content = item.get("content_base64") or ""
            try:
                content = base64.b64decode(raw_content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"Arquivo base64 invalido para evidencia {evidence_id}") from exc
            expected_sha = str(item.get("sha256") or "").strip().lower()
            actual_sha = hashlib.sha256(content).hexdigest()
            if expected_sha and expected_sha != actual_sha:
                raise ValueError(f"SHA256 divergente para evidencia {evidence_id}")
            file_name = _safe_filename(str(item.get("nome_arquivo") or evidence.get("nome_arquivo") or evidence_id))
            target = staging_root / _safe_segment(evidence_id) / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            evidence["caminho_evidencia"] = str(target)
            staged += 1
        return staged

    def _ai_group(self, run_id: str, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        dry_run = body.get("dry_run", True)
        if not self.repository.run(run_id):
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": "run_not_found"}

        ai_input = build_ai_grouping_input(self.repository, run_id)
        if dry_run is True:
            output_path = write_ai_dry_run(self.logs_root, run_id, ai_input)
            return HTTPStatus.OK, {
                "ok": True,
                "dry_run": True,
                "run_id": run_id,
                "input_path": str(output_path),
                "contract_version": ai_input["contract_version"],
                "falhas": len(ai_input.get("falhas") or []),
                "evidencias": ai_input.get("metadata", {}).get("evidences_count", 0),
                "diferencas": ai_input.get("metadata", {}).get("differences_count", 0),
                "message": "JSON de entrada da IA gerado. Nenhum modelo foi chamado e nada foi gravado em agrupamentos.",
            }
        if dry_run is not False:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_dry_run"}
        if self.require_ai_auth:
            try:
                self._validate_user_auth(authorization)
            except AuthenticationError as exc:
                return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}
        if not ai_input.get("falhas"):
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "error": "run_without_failures",
                "message": "A rodagem nao possui falhas para agrupar.",
            }
        if not all(hasattr(self.repository, name) for name in ("ai_grouping_status", "save_ai_job", "persist_ai_grouping")):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_ai_grouping"}

        current = self.repository.ai_grouping_status(run_id)
        if current.get("grouped"):
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "error": "already_grouped",
                "message": "Esta rodagem ja possui agrupamento por IA.",
                "status": current,
            }
        if current.get("status") == "running":
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "error": "already_processing",
                "message": "O agrupamento desta rodagem ja esta em processamento.",
                "status": current,
            }

        client = self.openai_client
        try:
            client = client or ai_client_from_env(self.env_path)
        except AiGroupingError as exc:
            return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "ai_provider_not_configured", "message": str(exc)}

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        job_id = make_job_id(run_id, ai_input)
        job = {
            "id": job_id,
            "run_id": run_id,
            "provider": getattr(client, "provider", client.__class__.__name__),
            "model": client.model,
            "request_json": ai_input,
            "response_json": {},
            "status": "running",
            "error_message": None,
            "created_at": now,
            "completed_at": None,
        }
        self.repository.save_ai_job(job)
        try:
            validated, raw_response = group_failures_in_batches(client, ai_input)
            rows = materialize_ai_rows(self.repository.run(run_id), job_id, validated)
            self.repository.persist_ai_grouping(run_id, job_id, rows)
            job.update(
                {
                    "response_json": {"validated": validated, "openai": raw_response},
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            self.repository.save_ai_job(job)
        except AiGroupingValidationError as exc:
            response_json = {"error": str(exc)}
            if isinstance(exc, AiGroupingInvalidJsonError):
                response_json["raw_text_preview"] = exc.raw_text[:4000]
                response_json["raw_text_length"] = len(exc.raw_text)
            job.update(
                {
                    "status": "invalid_response",
                    "response_json": response_json,
                    "error_message": str(exc),
                    "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            self.repository.save_ai_job(job)
            return HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": "invalid_ai_response", "message": str(exc), "job_id": job_id}
        except Exception as exc:
            job.update({"status": "failed", "error_message": str(exc)[:2000], "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            self.repository.save_ai_job(job)
            return HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "ai_grouping_failed", "message": str(exc), "job_id": job_id}

        return HTTPStatus.OK, {
            "ok": True,
            "dry_run": False,
            "run_id": run_id,
            "job_id": job_id,
            "status": "completed",
            "grupos": len(rows["groups"]),
            "falhas": len(rows["links"]),
            "proximos_passos": len(rows["actions"]),
            "message": "Falhas agrupadas e gravadas com sucesso.",
        }


class AgentTcRequestHandler(BaseHTTPRequestHandler):
    api: AgentTcApi

    def do_OPTIONS(self) -> None:
        self._send(HTTPStatus.NO_CONTENT, None)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/files/"):
                auth_error = self.api._user_auth_error(self.headers.get("Authorization"))
                if auth_error:
                    self._send(*auth_error)
                    return
                self._send_file(parsed.path[len("/files/") :])
                return
            status, payload = self.api.route_get(parsed.path, parse_qs(parsed.query), self.headers.get("Authorization"))
            self._send(status, payload)
        except Exception as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "message": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            status, payload = self.api.route_post(
                parsed.path,
                self._read_json_body(),
                self.headers.get("Authorization"),
            )
            self._send(status, payload)
        except Exception as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "message": str(exc)})

    def do_PATCH(self) -> None:
        try:
            parsed = urlparse(self.path)
            status, payload = self.api.route_patch(
                parsed.path,
                self._read_json_body(),
                self.headers.get("Authorization"),
            )
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
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=json_default).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_file(self, storage_path: str) -> None:
        if not self.api.local_files_root:
            self._send(HTTPStatus.NOT_FOUND, {"error": "local_storage_not_enabled"})
            return
        try:
            target = safe_storage_target(self.api.local_files_root, unquote(storage_path))
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_storage_path"})
            return
        if not target.exists() or not target.is_file():
            self._send(HTTPStatus.NOT_FOUND, {"error": "file_not_found"})
            return
        mime_type, _ = mimetypes.guess_type(str(target))
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(content)


def make_server(
    host: str,
    port: int,
    logs_root: str | Path,
    repository: Any | None = None,
    *,
    read_only: bool = False,
    env_path: str | Path | None = None,
    openai_client: Any | None = None,
    auth_validator: Any | None = None,
    require_ai_auth: bool = True,
    require_user_auth: bool = True,
) -> ThreadingHTTPServer:
    api = AgentTcApi(
        logs_root,
        repository=repository,
        read_only=read_only,
        env_path=env_path,
        openai_client=openai_client,
        auth_validator=auth_validator,
        require_ai_auth=require_ai_auth,
        require_user_auth=require_user_auth,
    )

    class Handler(AgentTcRequestHandler):
        pass

    Handler.api = api
    return ThreadingHTTPServer((host, port), Handler)


def _path_parts(path: str) -> list[str]:
    return [unquote(part) for part in path.strip("/").split("/") if part]


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return text.strip("._") or "item"


def _safe_filename(value: str) -> str:
    name = Path(value).name
    return _safe_segment(name)


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _repository_mode(repository: Any) -> str:
    name = repository.__class__.__name__
    if name == "PostgresRepository":
        return "postgres"
    if name == "SQLiteRepository":
        return "sqlite"
    return "local-json"


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def _local_files_root(env_path: Path | None) -> Path | None:
    env = _read_env(env_path)
    provider = (env.get("AGENT_TC_STORAGE") or env.get("STORAGE_PROVIDER") or "local").strip().lower()
    if provider not in {"local", "file", "filesystem"}:
        return None
    root = env.get("AGENT_TC_STORAGE_ROOT") or env.get("LOCAL_STORAGE_ROOT")
    return Path(root) if root else None


def _env_value(env_path: Path | None, key: str) -> str:
    env = _read_env(env_path)
    return env.get(key) or ""


def _read_env(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path and path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "AGENT_TC_STORAGE",
        "AGENT_TC_STORAGE_ROOT",
        "STORAGE_PROVIDER",
        "LOCAL_STORAGE_ROOT",
        "AGENT_TC_BRIDGE_TOKEN",
        "AGENT_TC_AUTH_SESSION_HOURS",
        "AGENT_TC_AUTH_BACKEND",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values

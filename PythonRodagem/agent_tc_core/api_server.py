from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
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
from .permissions import (
    DENIED_MESSAGES,
    MERGE,
    REGRAVAR,
    RODAGEM,
    can_see_module,
    can_see_test_cases,
    denied_modules,
    has_all_modules,
    has_permission,
    module_denied_message,
    module_of_hierarchy_row,
    modules_of_test_cases,
)
from .pipeline import run_shadow_pipeline
from .run_presets import RunPresetError, build_run_preset, check_preset_permission
from .regravacao import now_iso as regravacao_now
from .regravacao import (
    RegravacaoError,
    bridge_update_fields,
    build_regravacao_request,
    destino_da_branch,
    public_candidates,
    regravacao_candidates,
)
from .importacao import ImportacaoError, build_import_request
from .importacao import bridge_update_fields as import_bridge_update_fields
from .merge import (
    PENDING_STATUSES as MERGE_PENDING_STATUSES,
    PROCESSING_STATUSES as MERGE_PROCESSING_STATUSES,
    MergeError,
    build_merge_request,
    bridge_update_fields as merge_bridge_update_fields,
    check_cancel as check_merge_cancel,
    clean_branches,
    confirm_fields as merge_confirm_fields,
    now_iso as merge_now,
)
from .storage import safe_storage_target
from .testcase_disabled import MOD_XML_AUTOR, TestcaseDisabledError, build_disabled_row, clean_node_id, module_of_node


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
        # Um envio por vez para cada rodagem: dois envios da mesma rodagem ao mesmo tempo (VM e
        # "Adicionar rodagem") apagariam as linhas e os arquivos um do outro.
        self._ingest_locks: dict[str, threading.Lock] = {}
        self._ingest_locks_guard = threading.Lock()

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
        if parts in (["bridge", "regravacoes", "requested"], ["bridge", "regravacoes", "processing"]):
            auth_error = self._bridge_auth_error(authorization)
            if auth_error:
                return auth_error
            if not hasattr(self.repository, "bridge_requested_regravacoes"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_regravacao"}
            if parts[2] == "requested":
                return HTTPStatus.OK, self.repository.bridge_requested_regravacoes()
            # Pedidos que o Bridge pegou e nao terminou (caiu no meio): ele retoma ao iniciar.
            return HTTPStatus.OK, [r for r in self.repository.regravacao_requests() if r.get("status") == "processando"]
        if parts in (["bridge", "imports", "requested"], ["bridge", "imports", "processing"]):
            auth_error = self._bridge_auth_error(authorization)
            if auth_error:
                return auth_error
            if not hasattr(self.repository, "import_requests"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_import"}
            return HTTPStatus.OK, self.repository.import_requests("solicitado" if parts[2] == "requested" else "processando")
        if parts in (["bridge", "merges", "pending"], ["bridge", "merges", "processing"]):
            auth_error = self._bridge_auth_error(authorization)
            if auth_error:
                return auth_error
            if not hasattr(self.repository, "merge_requests_by_status"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_merge"}
            statuses = MERGE_PENDING_STATUSES if parts[2] == "pending" else MERGE_PROCESSING_STATUSES
            return HTTPStatus.OK, self.repository.merge_requests_by_status(statuses)
        auth_error = self._user_auth_error(authorization)
        if auth_error:
            return auth_error
        if parts in (["merge", "branches"], ["merges"]) or (len(parts) == 2 and parts[0] == "merges"):
            if not hasattr(self.repository, "merge_requests"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_merge"}
            if parts == ["merge", "branches"]:
                return HTTPStatus.OK, self.repository.merge_branches()
            if parts == ["merges"]:
                return HTTPStatus.OK, self.repository.merge_requests()
            row = self.repository.merge_request(parts[1])
            return (HTTPStatus.OK, row) if row else (HTTPStatus.NOT_FOUND, {"ok": False, "error": "merge_not_found"})
        # Cada um so ve os dados dos seus modulos (admin e "todos os modulos" veem tudo).
        viewer = self._viewer(authorization)
        if parts == ["modules"]:
            return HTTPStatus.OK, [m for m in self.repository.modules() if can_see_module(viewer, m.get("slug"))]
        if parts == ["modules", "latest-runs"]:
            if hasattr(self.repository, "latest_runs_by_module"):
                return HTTPStatus.OK, [
                    item for item in self.repository.latest_runs_by_module()
                    if can_see_module(viewer, (item.get("modulo") or {}).get("slug"))
                ]
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_latest_runs"}
        if (len(parts) == 3 and parts[0] == "modules" and parts[2] == "runs") or parts == ["runs"]:
            module = parts[1] if parts[0] == "modules" else _first(query, "module")
            if module and not can_see_module(viewer, module):
                return self._module_error(authorization, [module])
            return HTTPStatus.OK, [r for r in self.repository.runs(module) if can_see_module(viewer, r.get("modulo_slug"))]
        if len(parts) in (2, 3) and parts[0] == "runs":
            module_error = self._run_module_error(authorization, parts[1])
            if module_error:
                return module_error
        if len(parts) == 2 and parts[0] == "runs":
            row = self.repository.run(parts[1])
            return (HTTPStatus.OK, row) if row else (HTTPStatus.NOT_FOUND, {"error": "run_not_found"})
        if len(parts) == 3 and parts[0] == "runs":
            return self._run_child(parts[1], parts[2], query)
        if len(parts) == 3 and parts[0] == "failures" and parts[2] == "evidences":
            if hasattr(self.repository, "evidences_by_failure"):
                return HTTPStatus.OK, [
                    e for e in self.repository.evidences_by_failure(parts[1]) if can_see_module(viewer, e.get("modulo_slug"))
                ]
            return HTTPStatus.NOT_FOUND, {"error": "not_supported"}
        if parts == ["testcase-hierarchy"]:
            module = _first(query, "module")
            if module and not can_see_module(viewer, module):
                return self._module_error(authorization, [module])
            return HTTPStatus.OK, [
                row for row in self.repository.testcase_hierarchy(module)
                if can_see_module(viewer, module_of_hierarchy_row(row))
            ]
        if parts == ["rerun-requests"]:
            return HTTPStatus.OK, [r for r in self.repository.rerun_requests() if can_see_test_cases(viewer, _rerun_test_cases(r))]
        if parts == ["regravacoes"]:
            if not hasattr(self.repository, "regravacao_requests"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_regravacao"}
            run_id = _first(query, "run_id")
            if run_id:
                module_error = self._run_module_error(authorization, run_id)
                if module_error:
                    return module_error
            return HTTPStatus.OK, self._visible_regravacoes(viewer, self.repository.regravacao_requests(run_id))
        if parts == ["imports"]:
            if not hasattr(self.repository, "import_requests"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_import"}
            return HTTPStatus.OK, self.repository.import_requests()
        if parts == ["run-presets"]:
            if not hasattr(self.repository, "run_presets"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_run_presets"}
            return HTTPStatus.OK, self.repository.run_presets()
        if len(parts) == 2 and parts[0] == "testcase-times":
            # Historico de tempo de um caso (painel da aba Performance): vale o modulo do caso.
            if not hasattr(self.repository, "testcase_time_history"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_testcase_times"}
            try:
                node_id = clean_node_id(parts[1])
            except TestcaseDisabledError as exc:
                return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
            module = module_of_node(node_id)
            if not can_see_module(viewer, module):
                return self._module_error(authorization, [module] if module else [], [] if module else [node_id])
            return HTTPStatus.OK, self.repository.testcase_time_history(node_id)
        if parts == ["testcase-disabled"]:
            if not hasattr(self.repository, "testcase_disabled"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_testcase_disabled"}
            module = _first(query, "module")
            if module and not can_see_module(viewer, module):
                return self._module_error(authorization, [module])
            return HTTPStatus.OK, [
                row for row in self.repository.testcase_disabled(module) if can_see_module(viewer, row.get("module_slug"))
            ]
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path}

    def route_post(self, path: str, body: dict[str, Any], authorization: str | None = None) -> tuple[int, Any]:
        parts = _path_parts(path)
        if len(parts) >= 2 and parts[0] == "auth":
            return self._auth_post(parts[1:], body, authorization)
        if self.read_only:
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only_api"}
        if parts == ["ingest"]:
            return self._ingest(body, authorization)
        if parts == ["testcase-times", "import"]:
            return self._testcase_times_import(body, authorization)
        if len(parts) == 4 and parts[0] == "bridge" and parts[1] == "rerun-requests":
            return self._bridge_post(parts[2], parts[3], body, authorization)
        if len(parts) == 4 and parts[0] == "bridge" and parts[1] == "regravacoes":
            return self._bridge_regravacao_post(parts[2], parts[3], body, authorization)
        if len(parts) == 4 and parts[0] == "bridge" and parts[1] == "imports":
            return self._bridge_import_post(parts[2], parts[3], body, authorization)
        if parts == ["bridge", "merge", "branches"] or (len(parts) == 4 and parts[:2] == ["bridge", "merges"]):
            return self._bridge_merge_post(parts, body, authorization)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "ai-group":
            return self._ai_group(parts[1], body, authorization)
        auth_error = self._user_auth_error(authorization)
        if auth_error:
            return auth_error
        if parts == ["analyze"]:
            return self._analyze(body)
        if parts == ["rerun-requests"]:
            permission_error = self._permission_error(authorization, RODAGEM)
            if permission_error:
                return permission_error
            module_error = self._module_error(authorization, *modules_of_test_cases(body.get("casos_teste") or body.get("test_cases")))
            if module_error:
                return module_error
            branch_error = self._rerun_branch_error(body)
            if branch_error:
                return branch_error
            result = self.repository.record_rerun_request(body, requested_by=self._session_username(authorization))
            if isinstance(result, dict) and result.get("error") == "rerun_request_already_exists":
                return HTTPStatus.CONFLICT, {"ok": False, **result}
            return HTTPStatus.CREATED, result
        if len(parts) == 3 and parts[0] == "rerun-requests" and parts[2] == "cancel":
            permission_error = self._permission_error(authorization, RODAGEM)
            if permission_error:
                return permission_error
            if not hasattr(self.repository, "cancel_rerun_request"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_cancel"}
            current = self.repository.rerun_request(parts[1]) if hasattr(self.repository, "rerun_request") else None
            if current:
                module_error = self._module_error(authorization, *modules_of_test_cases(_rerun_test_cases(current)))
                if module_error:
                    return module_error
            result = self.repository.cancel_rerun_request(parts[1], body)
            if result is None:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": "rerun_request_not_found"}
            if isinstance(result, dict) and result.get("error") == "rerun_request_not_cancellable":
                return HTTPStatus.CONFLICT, {"ok": False, **result}
            return HTTPStatus.ACCEPTED, {"ok": True, "rerun_request": result}
        if parts == ["imports"]:
            return self._create_import(body, authorization)
        if parts == ["regravacoes"]:
            return self._create_regravacao(body, authorization)
        if parts == ["merges"] or (len(parts) == 3 and parts[0] == "merges" and parts[2] in ("confirm", "cancel")):
            return self._merge_post(parts, body, authorization)
        if len(parts) == 3 and parts[0] == "regravacoes" and parts[2] == "cancel":
            return self._cancel_regravacao(parts[1], authorization)
        if parts == ["run-presets"]:
            return self._save_run_preset(None, body, authorization)
        if len(parts) == 3 and parts[0] == "testcase-times" and parts[2] == "base":
            return self._testcase_time_base_post(parts[1], body, authorization)
        if parts == ["testcase-disabled"] or (len(parts) == 3 and parts[0] == "testcase-disabled" and parts[2] == "delete"):
            return self._testcase_disabled_post(parts, body, authorization)
        if len(parts) == 3 and parts[0] == "run-presets" and parts[2] == "delete":
            permission_error = self._permission_error(authorization, RODAGEM)
            if permission_error:
                return permission_error
            if not hasattr(self.repository, "delete_run_preset"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_run_presets"}
            current = self.repository.run_preset(parts[1])
            if not current:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": "run_preset_not_found"}
            if self.require_user_auth:
                try:
                    check_preset_permission(current, self._session_username(authorization), self._is_admin(authorization), delete=True)
                except RunPresetError as exc:
                    return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
            if not self.repository.delete_run_preset(parts[1]):
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": "run_preset_not_found"}
            return HTTPStatus.OK, {"ok": True}
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
                "GET /modules/latest-runs",
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
                "GET /runs/{id}/rerun-results",
                "GET /runs/{id}/unstable-cases",
                "GET /runs/{id}/ai-group-status",
                "GET /runs/{id}/ai-group-debug",
                "GET /failures/{id}/evidences",
                "GET /testcase-hierarchy?module=contabil",
                "GET /rerun-requests",
                "POST /rerun-requests",
                "POST /rerun-requests/{id}/cancel",
                "GET /run-presets",
                "POST /run-presets",
                "PATCH /run-presets/{id}",
                "POST /run-presets/{id}/delete",
                "GET /testcase-times/{node_id}",
                "POST /testcase-times/{node_id}/base",
                "POST /testcase-times/import",
                "GET /testcase-disabled?module=",
                "POST /testcase-disabled",
                "POST /testcase-disabled/{node_id}/delete",
                "GET /runs/{id}/regravacao",
                "GET /regravacoes?run_id=",
                "POST /regravacoes",
                "POST /regravacoes/{id}/cancel",
                "GET /bridge/regravacoes/requested",
                "GET /bridge/regravacoes/processing",
                "POST /bridge/regravacoes/{id}/claim",
                "POST /bridge/regravacoes/{id}/update",
                "GET /imports",
                "POST /imports",
                "GET /bridge/imports/requested",
                "GET /bridge/imports/processing",
                "POST /bridge/imports/{id}/claim",
                "POST /bridge/imports/{id}/update",
                "GET /merge/branches",
                "GET /merges",
                "GET /merges/{id}",
                "POST /merges",
                "POST /merges/{id}/confirm",
                "POST /merges/{id}/cancel",
                "GET /bridge/merges/pending",
                "GET /bridge/merges/processing",
                "POST /bridge/merges/{id}/claim",
                "POST /bridge/merges/{id}/update",
                "POST /bridge/merge/branches",
                "GET /bridge/files/{storage_path}",
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
        auth_error = self._user_auth_error(authorization)
        if auth_error:
            return auth_error
        if len(parts) == 2 and parts[0] == "run-presets":
            return self._save_run_preset(parts[1], body, authorization)
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path}

    def _permission_error(self, authorization: str | None, permission: str) -> tuple[int, Any] | None:
        """Acao que exige permissao do usuario (o admin pode tudo). Sem login exigido, libera."""
        if not self.require_user_auth:
            return None
        try:
            identity = self._validate_user_auth(authorization)
        except LocalAuthError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}
        if has_permission(identity, permission):
            return None
        return HTTPStatus.FORBIDDEN, {
            "ok": False,
            "error": "permission_required",
            "permission": permission,
            "message": DENIED_MESSAGES[permission],
        }

    def _module_error(self, authorization: str | None, modules: list[str], unknown: list[str] | None = None) -> tuple[int, Any] | None:
        """Acao em modulo(s): o usuario precisa ter acesso a todos (admin e "todos os modulos" passam sempre)."""
        if not self.require_user_auth:
            return None
        try:
            identity = self._validate_user_auth(authorization)
        except LocalAuthError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}
        if has_all_modules(identity):
            return None
        if unknown:
            return HTTPStatus.FORBIDDEN, {
                "ok": False,
                "error": "module_unknown",
                "codes": unknown,
                "message": "Nao foi possivel identificar o modulo de: " + ", ".join(unknown) + ". Peca a um administrador.",
            }
        denied = denied_modules(identity, modules)
        if not denied:
            return None
        return HTTPStatus.FORBIDDEN, {"ok": False, "error": "module_required", "modules": denied, "message": module_denied_message(denied)}

    def _viewer(self, authorization: str | None) -> dict[str, Any]:
        """Quem esta vendo, para filtrar as leituras por modulo. Sem login exigido, ve tudo."""
        if not self.require_user_auth:
            return {"role": "admin"}
        try:
            return self._validate_user_auth(authorization)
        except AuthenticationError:
            return {"modules": []}  # nao chega aqui: o login ja foi conferido antes

    def _rerun_branch_error(self, body: dict[str, Any]) -> tuple[int, Any] | None:
        """Branch do projeto na VM: vazio nao troca; "local" e o trunk; senao, so branch que existe no SVN.

        O Jenkins faz svn revert e svn switch com esse nome direto no comando da VM:
        nada fora da lista que o MergeBridge le do SVN pode passar.
        """
        branch = str(body.get("branch") or "").strip()
        if not branch:
            body["branch"] = ""
            return None
        if branch.lower() == "local":
            body["branch"] = "local"
            return None
        conhecidas = []
        if hasattr(self.repository, "merge_branches"):
            conhecidas = [
                str(b.get("name") or "") for b in self.repository.merge_branches().get("branches") or [] if b.get("kind") == "branch"
            ]
        if branch not in conhecidas:
            return HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "error": "invalid_branch",
                "message": f"A branch \"{branch}\" nao esta na lista de branches do SVN. Atualize a pagina e escolha de novo.",
            }
        body["branch"] = branch
        return None

    def _visible_regravacoes(self, viewer: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pedidos de regravacao: vale o modulo da rodagem de cada um (cada rodagem e consultada uma vez)."""
        if has_all_modules(viewer):
            return rows
        slugs: dict[str, str] = {}
        for row in rows:
            run_id = str(row.get("run_id") or "")
            if run_id not in slugs:
                run = self.repository.run(run_id) if run_id else None
                slugs[run_id] = str((run or {}).get("modulo_slug") or "")
        return [row for row in rows if can_see_module(viewer, slugs[str(row.get("run_id") or "")])]

    def _file_module_error(self, authorization: str | None, storage_path: str) -> tuple[int, Any] | None:
        """Arquivo de evidencia: <modulo>/<versao>/<rodagem>/<caso>/<tipo>/<arquivo>; vale o modulo da rodagem."""
        if not self.require_user_auth or has_all_modules(self._viewer(authorization)):
            return None
        parts = [part for part in unquote(storage_path).split("/") if part]
        if len(parts) < 3 or not self.repository.run(parts[2]):
            return HTTPStatus.NOT_FOUND, {"error": "file_not_found"}
        return self._run_module_error(authorization, parts[2])

    def _run_module_error(self, authorization: str | None, run_id: str) -> tuple[int, Any] | None:
        """Modulo da rodagem (ver e regravar). Rodagem inexistente segue para o erro normal de cada rota."""
        run = self.repository.run(run_id) if run_id and hasattr(self.repository, "run") else None
        if not run:
            return None
        slug = str(run.get("modulo_slug") or "")
        return self._module_error(authorization, [slug] if slug else [], [] if slug else [run_id])

    def _create_regravacao(self, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        if not hasattr(self.repository, "save_regravacao_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_regravacao"}
        permission_error = self._permission_error(authorization, REGRAVAR)
        if permission_error:
            return permission_error
        module_error = self._run_module_error(authorization, str(body.get("run_id") or ""))
        if module_error:
            return module_error
        try:
            row = build_regravacao_request(self.repository, body, self._session_username(authorization))
        except RegravacaoError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        return HTTPStatus.CREATED, self.repository.save_regravacao_request(row)

    def _cancel_regravacao(self, request_id: str, authorization: str | None) -> tuple[int, Any]:
        if not hasattr(self.repository, "regravacao_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_regravacao"}
        permission_error = self._permission_error(authorization, REGRAVAR)
        if permission_error:
            return permission_error
        current = self.repository.regravacao_request(request_id)
        if not current:
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": "regravacao_not_found"}
        module_error = self._run_module_error(authorization, str(current.get("run_id") or ""))
        if module_error:
            return module_error
        if (self.require_user_auth and not self._is_admin(authorization)
                and current.get("requested_by") != self._session_username(authorization)):
            return HTTPStatus.FORBIDDEN, {"ok": False, "error": "not_owner", "message": "So quem pediu (ou um administrador) pode cancelar."}
        if current.get("status") != "solicitado":
            return HTTPStatus.CONFLICT, {"ok": False, "error": "regravacao_not_cancellable", "message": "So pedidos ainda na fila podem ser cancelados."}
        row = self.repository.update_regravacao_request(request_id, {"status": "cancelado", "finished_at": regravacao_now()})
        return HTTPStatus.OK, {"ok": True, "regravacao": row}

    def _bridge_regravacao_post(self, request_id: str, action: str, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        auth_error = self._bridge_auth_error(authorization)
        if auth_error:
            return auth_error
        if not hasattr(self.repository, "claim_regravacao_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_regravacao"}
        if action == "claim":
            claimed = bool(self.repository.claim_regravacao_request(request_id))
            row = self.repository.regravacao_request(request_id) if claimed else None
            return HTTPStatus.OK, {"ok": True, "claimed": claimed, "regravacao": row}
        if action == "update":
            try:
                fields = bridge_update_fields(body)
            except RegravacaoError as exc:
                return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
            row = self.repository.update_regravacao_request(request_id, fields)
            return (HTTPStatus.OK, {"ok": True, "regravacao": row}) if row else (HTTPStatus.NOT_FOUND, {"ok": False, "error": "regravacao_not_found"})
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "bridge_action_not_found"}

    def _create_import(self, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        """Importar rodagem: quem pode solicitar rodagem informa a pasta; o RegravacaoBridge roda o .bat na D01."""
        if not hasattr(self.repository, "save_import_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_import"}
        permission_error = self._permission_error(authorization, RODAGEM)
        if permission_error:
            return permission_error
        try:
            row = build_import_request(self.repository, body, self._session_username(authorization))
        except ImportacaoError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        return HTTPStatus.CREATED, self.repository.save_import_request(row)

    def _bridge_import_post(self, request_id: str, action: str, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        auth_error = self._bridge_auth_error(authorization)
        if auth_error:
            return auth_error
        if not hasattr(self.repository, "claim_import_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_import"}
        if action == "claim":
            row = self.repository.claim_import_request(request_id)
            return HTTPStatus.OK, {"ok": True, "claimed": bool(row), "import": row}
        if action == "update":
            try:
                fields = import_bridge_update_fields(body)
            except (ImportacaoError, ValueError) as exc:
                return HTTPStatus.BAD_REQUEST, {"ok": False, "error": getattr(exc, "error", "invalid_body"), "message": str(exc)}
            row = self.repository.update_import_request(request_id, fields)
            return (HTTPStatus.OK, {"ok": True, "import": row}) if row else (HTTPStatus.NOT_FOUND, {"ok": False, "error": "import_not_found"})
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "bridge_action_not_found"}

    def _merge_post(self, parts: list[str], body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        """Merge entre branches: quem tem a permissao pede, confirma e cancela o seu (admin cancela qualquer um)."""
        if not hasattr(self.repository, "save_merge_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_merge"}
        permission_error = self._permission_error(authorization, MERGE)
        if permission_error:
            return permission_error
        username = self._session_username(authorization)
        try:
            if parts == ["merges"]:
                row = build_merge_request(self.repository, body, username)
                return HTTPStatus.CREATED, self.repository.save_merge_request(row)
            current = self.repository.merge_request(parts[1])
            if not current:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": "merge_not_found"}
            if parts[2] == "confirm":
                fields = merge_confirm_fields(current, body, username)
            else:
                check_merge_cancel(current, username, self._is_admin(authorization))
                fields = {"status": "cancelado", "finished_at": merge_now()}
        except MergeError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        return HTTPStatus.OK, {"ok": True, "merge": self.repository.update_merge_request(parts[1], fields)}

    def _testcase_disabled_post(self, parts: list[str], body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        """Marcar/desmarcar caso como desativado: quem pode solicitar rodagem no modulo do caso."""
        if not hasattr(self.repository, "save_testcase_disabled"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_testcase_disabled"}
        permission_error = self._permission_error(authorization, RODAGEM)
        if permission_error:
            return permission_error
        try:
            node_id = clean_node_id(parts[1] if len(parts) == 3 else body.get("node_id"))
            module = module_of_node(node_id)
            module_error = self._module_error(authorization, [module] if module else [], [] if module else [node_id])
            if module_error:
                return module_error
            atual = self.repository.testcase_disabled_item(node_id) if hasattr(self.repository, "testcase_disabled_item") else None
            if atual and atual.get("disabled_by") == MOD_XML_AUTOR:
                return HTTPStatus.CONFLICT, {
                    "ok": False,
                    "error": "testcase_disabled_by_mod_xml",
                    "message": "Este caso e desmarcado pelo mod_xml.py em toda rodagem. Para mudar, edite a lista no mod_xml.py.",
                }
            if len(parts) == 3:
                if not self.repository.delete_testcase_disabled(node_id):
                    return HTTPStatus.NOT_FOUND, {"ok": False, "error": "testcase_disabled_not_found"}
                return HTTPStatus.OK, {"ok": True}
            row = build_disabled_row({**body, "node_id": node_id}, self._session_username(authorization))
        except TestcaseDisabledError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        return HTTPStatus.CREATED, self.repository.save_testcase_disabled(row)

    def _testcase_time_base_post(self, node: str, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        """Trocar o tempo base de um caso: quem pode regravar arquivos, no modulo do caso."""
        if not hasattr(self.repository, "save_testcase_time_base"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_testcase_times"}
        permission_error = self._permission_error(authorization, REGRAVAR)
        if permission_error:
            return permission_error
        try:
            node_id = clean_node_id(node)
        except TestcaseDisabledError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        module = module_of_node(node_id)
        module_error = self._module_error(authorization, [module] if module else [], [] if module else [node_id])
        if module_error:
            return module_error
        segundos = body.get("segundos")
        if not isinstance(segundos, int) or isinstance(segundos, bool) or not 0 < segundos <= 24 * 3600:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_seconds", "message": "Informe o novo tempo base em segundos."}
        atual = self.repository.testcase_time_base(node_id) or {}
        row = {
            "id": node_id,
            "base_seconds": segundos,
            "origin": "substituida",
            "previous_seconds": atual.get("base_seconds"),
            "changed_by": self._session_username(authorization),
            "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.repository.save_testcase_time_base(row)
        return HTTPStatus.OK, self.repository.testcase_time_history(node_id)

    def _testcase_times_import(self, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        """Carga inicial (cli/agent_tc_importar_tempos.py): bases da planilha antiga e historico. Token das VMs."""
        auth_error = self._ingest_auth_error(authorization)
        if auth_error:
            return auth_error
        if not hasattr(self.repository, "import_testcase_times"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_testcase_times"}
        bases = body.get("bases", {})
        medicoes = body.get("medicoes", [])
        if not isinstance(bases, dict) or not isinstance(medicoes, list):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_import"}
        return HTTPStatus.OK, {"ok": True, **self.repository.import_testcase_times(bases, medicoes)}

    def _is_admin(self, authorization: str | None) -> bool:
        if not self.require_user_auth:
            return True
        try:
            return (self._validate_user_auth(authorization).get("role") or "") == "admin"
        except AuthenticationError:
            return False

    def _bridge_merge_post(self, parts: list[str], body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        auth_error = self._bridge_auth_error(authorization)
        if auth_error:
            return auth_error
        if not hasattr(self.repository, "claim_merge_request"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_merge"}
        try:
            if parts == ["bridge", "merge", "branches"]:
                return HTTPStatus.OK, self.repository.save_merge_branches(clean_branches(body))
            request_id, action = parts[2], parts[3]
            if action == "claim":
                claimed = bool(self.repository.claim_merge_request(request_id))
                row = self.repository.merge_request(request_id) if claimed else None
                return HTTPStatus.OK, {"ok": True, "claimed": claimed, "merge": row}
            if action == "update":
                row = self.repository.update_merge_request(request_id, merge_bridge_update_fields(body))
                return (HTTPStatus.OK, {"ok": True, "merge": row}) if row else (HTTPStatus.NOT_FOUND, {"ok": False, "error": "merge_not_found"})
        except MergeError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "bridge_action_not_found"}

    def _save_run_preset(self, preset_id: str | None, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        if not hasattr(self.repository, "save_run_preset"):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_run_presets"}
        permission_error = self._permission_error(authorization, RODAGEM)
        if permission_error:
            return permission_error
        current = None
        if preset_id is not None:
            current = self.repository.run_preset(preset_id)
            if not current:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": "run_preset_not_found"}
        try:
            if current and self.require_user_auth:
                check_preset_permission(current, self._session_username(authorization), self._is_admin(authorization), delete=False)
            row = build_run_preset(
                body,
                self.repository.run_presets(),
                current=current,
                username=self._session_username(authorization),
            )
        except RunPresetError as exc:
            return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
        saved = self.repository.save_run_preset(row)
        return (HTTPStatus.OK if current else HTTPStatus.CREATED), saved

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

    def _session_username(self, authorization: str | None) -> str | None:
        """Usuario da sessao atual, para registrar autoria sem confiar no corpo da requisicao."""
        if not self.require_user_auth:
            return None
        try:
            identity = self._validate_user_auth(authorization)
        except AuthenticationError:
            return None
        return identity.get("username") or None

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
        if not _token_matches(authorization, token):
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized_bridge"}
        return None

    def _ingest_auth_error(self, authorization: str | None) -> tuple[int, Any] | None:
        token = _env_value(self.env_path, "AGENT_TC_API_TOKEN") or _env_value(self.env_path, "AGENT_TC_BRIDGE_TOKEN")
        if not token:
            return HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "ingest_token_not_configured",
            }
        if not _token_matches(authorization, token):
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized_ingest"}
        return None

    def _run_child(self, run_id: str, child: str, query: dict[str, list[str]] | None = None) -> tuple[int, Any]:
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
        if child == "unstable-cases":
            if hasattr(self.repository, "unstable_cases"):
                return HTTPStatus.OK, self.repository.unstable_cases(run_id)
            return HTTPStatus.OK, {}
        if child == "rerun-results":
            if hasattr(self.repository, "rerun_results"):
                return HTTPStatus.OK, self.repository.rerun_results(run_id)
            return HTTPStatus.OK, []
        if child == "reexecutable-cases":
            if hasattr(self.repository, "reexecutable_cases"):
                return HTTPStatus.OK, self.repository.reexecutable_cases(run_id)
            return HTTPStatus.OK, []
        if child == "ai-group-status":
            if hasattr(self.repository, "ai_grouping_status"):
                return HTTPStatus.OK, self._ai_grouping_status(run_id)
            return HTTPStatus.OK, {"run_id": run_id, "status": "not_supported", "grouped": False}
        if child == "regravacao":
            if not hasattr(self.repository, "regravacao_requests"):
                return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_regravacao"}
            # ?branch=<url>: situacao dos arquivos para regravar em outra branch.
            try:
                destino = destino_da_branch(self.repository, _first(query or {}, "branch"))
            except RegravacaoError as exc:
                return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
            return HTTPStatus.OK, public_candidates(regravacao_candidates(self.repository, run_id, destino))
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
        staging_root = self._ingest_staging_root(run_id)
        with self._ingest_locks_guard:
            run_lock = self._ingest_locks.setdefault(run_id, threading.Lock())
        run_lock.acquire()
        try:
            staged = self._stage_ingest_files(payload, body.get("files") or [], staging_root)
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
        finally:
            # Os arquivos so ficam aqui ate o storage copiar; o volume de evidencias e a copia definitiva.
            shutil.rmtree(staging_root, ignore_errors=True)
            run_lock.release()

    def _ingest_staging_root(self, run_id: str) -> Path:
        # Pasta propria de cada envio: o rmtree de um envio nao apaga os arquivos de outro.
        return self.logs_root / "api_ingest_files" / (_safe_segment(run_id or "run") + "_" + uuid.uuid4().hex[:12])

    def _stage_ingest_files(self, payload: dict[str, Any], files: list[Any], staging_root: Path) -> int:
        by_id: dict[str, dict[str, Any]] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("id_evidencia") or "").strip()
            if evidence_id:
                by_id[evidence_id] = item

        staged = 0
        for evidence in payload.get("evidencias") or []:
            evidence_id = str(evidence.get("id_evidencia") or "").strip()
            item = by_id.get(evidence_id)
            if not item:
                # O caminho veio da VM: nunca ler arquivo do servidor por ele. Sem o arquivo enviado,
                # a importacao falha no storage (evidencia nao pode sumir com resposta 201).
                evidence["caminho_evidencia"] = ""
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

    def _ai_grouping_status(self, run_id: str) -> dict[str, Any]:
        """Status do agrupamento, liberando antes um job interrompido.

        A expiracao roda tambem na consulta: o dashboard desabilita o botao
        enquanto o status e "running", entao so o pedido nunca chegaria aqui.
        """
        current = self.repository.ai_grouping_status(run_id)
        if current.get("status") == "running" and hasattr(self.repository, "expire_stale_ai_jobs"):
            if self.repository.expire_stale_ai_jobs(run_id, _ai_job_stale_minutes(self.env_path)):
                current = self.repository.ai_grouping_status(run_id)
        return current

    def _ai_group(self, run_id: str, body: dict[str, Any], authorization: str | None) -> tuple[int, Any]:
        dry_run = body.get("dry_run", True)
        if self.require_ai_auth:
            try:
                self._validate_user_auth(authorization)
            except LocalAuthError as exc:
                return exc.status, {"ok": False, "error": exc.error, "message": str(exc)}
            except AuthenticationError as exc:
                return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized", "message": str(exc)}
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
        if not ai_input.get("falhas"):
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "error": "run_without_failures",
                "message": "A rodagem nao possui falhas para agrupar.",
            }
        if not all(hasattr(self.repository, name) for name in ("ai_grouping_status", "save_ai_job", "persist_ai_grouping")):
            return HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "repository_does_not_support_ai_grouping"}

        current = self._ai_grouping_status(run_id)
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
            usados = getattr(client, "used", None)
            if usados:
                job["provider"] = " + ".join(dict.fromkeys(provider for provider, _ in usados))
                job["model"] = " + ".join(dict.fromkeys(model for _, model in usados))
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
            if parsed.path.startswith("/bridge/files/"):
                auth_error = self.api._bridge_auth_error(self.headers.get("Authorization"))
                if auth_error:
                    self._send(*auth_error)
                    return
                self._send_file(parsed.path[len("/bridge/files/") :])
                return
            if parsed.path.startswith("/files/"):
                auth_error = self.api._user_auth_error(self.headers.get("Authorization"))
                auth_error = auth_error or self.api._file_module_error(
                    self.headers.get("Authorization"), parsed.path[len("/files/") :]
                )
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
    return _Server((host, port), Handler)


class _Server(ThreadingHTTPServer):
    # Fila de conexoes do padrao (5) estoura com o dashboard, as Bridges e as VMs juntos: o Caddy recebe recusa e devolve 502.
    request_queue_size = 128


DEFAULT_AI_JOB_STALE_MINUTES = 30


def _ai_job_stale_minutes(env_path: Path | None) -> int:
    try:
        minutes = int(_env_value(env_path, "AGENT_TC_AI_JOB_STALE_MINUTES") or DEFAULT_AI_JOB_STALE_MINUTES)
    except ValueError:
        return DEFAULT_AI_JOB_STALE_MINUTES
    return minutes if minutes > 0 else DEFAULT_AI_JOB_STALE_MINUTES


def _token_matches(authorization: str | None, token: str) -> bool:
    expected = ("Bearer " + token).encode("utf-8")
    received = (authorization or "").encode("utf-8")
    return hmac.compare_digest(received, expected)


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


def _rerun_test_cases(row: dict[str, Any]) -> Any:
    """Casos de teste de um pedido de rodagem (coluna ou config enviada)."""
    config = row.get("config_json") if isinstance(row.get("config_json"), dict) else {}
    return row.get("test_cases") or config.get("casos_teste")


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
        "AGENT_TC_AI_JOB_STALE_MINUTES",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values

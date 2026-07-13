from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .api_repository import OFFICIAL_MODULES, SLUG_BY_MODULE_ID
from .constants import MODULE_CODES_BY_ID
from .sqlite_repository import (
    MODULE_ID_BY_CODE,
    clean_ext,
    delay_api_row,
    evidence_api_row,
    file_role,
    file_type,
    group_api_row,
    hierarchy_api_row,
    import_summary,
    module_api_row,
    module_name,
    module_system,
    now_iso,
    occurrence_api_row,
    occurrence_type,
    parent_case_id,
    reexecutable_type,
    run_api_row,
    sha256_file,
    translate_node_type,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env"
DEFAULT_SCHEMA = "public"
DEFAULT_TABLE_PREFIX = "agent_tc_"
DEFAULT_BUCKET = "agent-tc-evidences"
SIGNED_URL_SECONDS = 60 * 60 * 24 * 7


class SupabaseHttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:500]}")


class SupabaseRepository:
    def __init__(
        self,
        env_path: str | Path | None = None,
        *,
        url: str | None = None,
        service_key: str | None = None,
        bucket: str | None = None,
        schema: str | None = None,
        table_prefix: str | None = None,
        dry_run: bool = False,
    ):
        env = read_env(env_path or DEFAULT_ENV)
        self.url = (url or env.get("SUPABASE_URL") or "").rstrip("/")
        self.service_key = service_key or env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SECRET_KEY") or ""
        self.bucket = bucket or env.get("SUPABASE_BUCKET") or DEFAULT_BUCKET
        self.schema = schema or env.get("SUPABASE_SCHEMA") or DEFAULT_SCHEMA
        self.table_prefix = table_prefix if table_prefix is not None else env.get("SUPABASE_TABLE_PREFIX", DEFAULT_TABLE_PREFIX)
        self.storage_public = parse_bool(env.get("SUPABASE_BUCKET_PUBLIC"), default=True)
        self.dry_run = dry_run
        self.plan: dict[str, Any] = {
            "dry_run": dry_run,
            "schema": self.schema,
            "table_prefix": self.table_prefix,
            "bucket": self.bucket,
            "upserts": {},
            "uploads": 0,
            "skipped_evidence": 0,
            "upload_errors": [],
            "deduplicated_rows": {},
        }
        if not self.url:
            raise ValueError("SUPABASE_URL nao configurada")
        if not self.service_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY nao configurada")

    def initialize(self) -> None:
        if self.dry_run:
            self.plan["initialize"] = "skipped_dry_run"
            return
        self.ensure_bucket()
        self.seed_modules()

    def seed_modules(self) -> None:
        now = now_iso()
        rows = []
        for module in OFFICIAL_MODULES:
            rows.append(
                {
                    "id": module["id_modulo"],
                    "slug": module["slug"],
                    "name": module["nome"],
                    "system": module_system(module["id_modulo"]),
                    "codes_json": list(MODULE_CODES_BY_ID.get(module["id_modulo"], ())),
                    "active": True,
                    "sort_order": module["ordem"],
                    "created_at": now,
                    "updated_at": now,
                }
            )
        self._upsert("modules", rows)

    def import_payload_file(self, payload_path: str | Path) -> dict[str, Any]:
        payload_path = Path(payload_path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        return self.import_payload(payload, source=str(payload_path))

    def import_payload(self, payload: dict[str, Any], source: str = "payload") -> dict[str, Any]:
        run = payload["rodagem"]
        module = payload["modulo"]
        module_id = str(module["id_modulo"])
        run_id = str(run["id_rodagem"])
        now = now_iso()
        payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        payload_sha = hashlib.sha256(payload_bytes).hexdigest()
        batch_id = "ing_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}|{payload_sha}").hex

        if not self.dry_run:
            self.ensure_bucket()
        self.seed_modules()

        run_row = {
            "id": run_id,
            "system": run.get("sistema") or module_system(module_id),
            "version": run.get("versao") or "",
            "vm_name": str(run.get("vm_name") or "").lower(),
            "module_id": module_id,
            "started_at": run.get("data_inicio") or now,
            "finished_at": None,
            "logs_path": run.get("caminho_logs") or "",
            "status": "processing" if not self.dry_run else "dry_run",
            "total_archives": run.get("total_archives") if run.get("total_archives") is not None else len(payload.get("falhas") or []),
            "total_occurrences": len(payload.get("falhas") or []),
            "total_executed": run.get("total_executed") if run.get("total_executed") is not None else run.get("total_analisados"),
            "total_ai_groups": len(payload.get("agrupamentos_shadow") or payload.get("agrupamentos") or []),
            "created_at": run.get("created_at") or now,
            "updated_at": now,
        }
        batch_row = {
            "id": batch_id,
            "run_id": run_id,
            "source": source,
            "payload_sha256": payload_sha,
            "status": "processing" if not self.dry_run else "dry_run",
            "summary_json": import_summary(payload),
            "created_at": now,
            "completed_at": None,
        }
        self._upsert("runs", [run_row])
        self._upsert("ingestion_batches", [batch_row])

        try:
            hierarchy_rows = self._import_hierarchy(payload)
            occurrence_by_case = self._import_occurrences(payload, hierarchy_rows)
            group_ids = self._import_ai_groups(payload)
            self._import_group_links(payload, group_ids)
            evidence_by_occurrence_name = self._import_evidence(payload)
            differences_written = self._import_report_differences(
                payload,
                occurrence_by_case,
                evidence_by_occurrence_name,
            )
            self._import_run_delays(payload)
            expected = {
                "occurrences": _unique_count(payload.get("falhas") or [], "id_falha"),
                "evidence_files": len(payload.get("evidencias") or []) - self.plan["skipped_evidence"],
                "report_differences": differences_written,
                "ai_groups": len(group_ids),
            }
            verification = self._verify_run_import(run_id, expected)
            run_row.update({"status": "analyzed", "updated_at": now_iso()})
            batch_row.update(
                {
                    "status": "completed" if not self.dry_run else "dry_run",
                    "summary_json": {
                        **import_summary(payload),
                        "erros_processamento": payload.get("erros_processamento") or [],
                        "verification": verification,
                    },
                    "completed_at": now_iso(),
                }
            )
            self._upsert("runs", [run_row])
            self._upsert("ingestion_batches", [batch_row])
        except Exception as exc:
            run_row.update({"status": "import_failed", "updated_at": now_iso()})
            batch_row.update(
                {
                    "status": "failed",
                    "summary_json": {
                        **import_summary(payload),
                        "error": str(exc)[:2000],
                        "erros_processamento": payload.get("erros_processamento") or [],
                    },
                    "completed_at": now_iso(),
                }
            )
            try:
                self._upsert("runs", [run_row])
                self._upsert("ingestion_batches", [batch_row])
            except Exception:
                pass
            raise

        return {
            "backend": "supabase",
                "schema": self.schema,
                "table_prefix": self.table_prefix,
            "bucket": self.bucket,
            "dry_run": self.dry_run,
            "run_id": run_id,
            "modules": len(OFFICIAL_MODULES),
            "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
            "occurrences": len(payload.get("falhas") or []),
            "evidence_files": len(payload.get("evidencias") or []) - self.plan["skipped_evidence"],
            "report_differences": len(payload.get("diferencas_relatorio") or []),
            "run_delays": len(payload.get("atrasos_rodagem") or []),
            "ai_groups": len(payload.get("agrupamentos_shadow") or payload.get("agrupamentos") or []),
            "plan": self.plan,
            "verification": verification,
        }

    def _verify_run_import(self, run_id: str, expected: dict[str, int]) -> dict[str, Any]:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "expected": expected, "actual": expected}
        actual = {
            "occurrences": len(self._select("occurrences", {"run_id": "eq." + run_id, "select": "id"})),
            "evidence_files": len(self._select("evidence_files", {"run_id": "eq." + run_id, "select": "id"})),
            "report_differences": len(self._select("report_differences", {"run_id": "eq." + run_id, "select": "id"})),
            "ai_groups": len(
                self._select(
                    "ai_groups",
                    {"run_id": "eq." + run_id, "ai_analysis_job_id": "is.null", "select": "id"},
                )
            ),
        }
        mismatches = {
            key: {"expected": expected[key], "actual": actual.get(key)}
            for key in expected
            if expected[key] != actual.get(key)
        }
        if mismatches:
            raise RuntimeError(
                "Verificacao pos-importacao falhou: "
                + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
            )
        return {"ok": True, "expected": expected, "actual": actual}

    def modules(self) -> list[dict[str, Any]]:
        rows = self._select("modules", {"order": "sort_order.asc,name.asc"})
        return [module_api_row(row) for row in rows]

    def runs(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        params = {"order": "started_at.desc"}
        if module_slug:
            module = self._module_by_slug(module_slug)
            if not module:
                return []
            params["module_id"] = "eq." + module["id"]
        rows = self._select("runs", params)
        return [run_api_row(self._with_module(row)) for row in rows]

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._select("runs", {"id": "eq." + run_id, "limit": "1"})
        if not rows:
            return None
        return run_api_row(self._with_module(rows[0]))

    def failures(self, run_id: str) -> list[dict[str, Any]]:
        links = self.group_links(run_id)
        group_by_occurrence = {
            occurrence_id: group_id
            for group_id, occurrence_ids in links.items()
            for occurrence_id in occurrence_ids
        }
        rows = self._select("occurrences", {"run_id": "eq." + run_id, "order": "testcase_node_id.asc"})
        out = []
        for row in rows:
            row = dict(row)
            row["group_id"] = group_by_occurrence.get(row["id"])
            out.append(occurrence_api_row(row))
        return out

    def evidences(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._select("evidence_files", {"run_id": "eq." + run_id, "order": "occurrence_id.asc,file_role.asc,original_name.asc"})
        return [evidence_api_row(row) for row in rows]

    def evidences_by_failure(self, failure_id: str) -> list[dict[str, Any]]:
        rows = self._select("evidence_files", {"occurrence_id": "eq." + failure_id, "order": "file_role.asc,original_name.asc"})
        return [evidence_api_row(row) for row in rows]

    def report_differences(self, run_id: str) -> list[dict[str, Any]]:
        return self._select(
            "report_differences",
            {"run_id": "eq." + run_id, "order": "testcase_node_id.asc,base_file_name.asc"},
        )

    def ai_grouping_status(self, run_id: str) -> dict[str, Any]:
        jobs = self._select(
            "ai_analysis_jobs",
            {"run_id": "eq." + run_id, "order": "created_at.desc", "limit": "1"},
        )
        real_groups = self._select(
            "ai_groups",
            {"run_id": "eq." + run_id, "ai_analysis_job_id": "not.is.null", "select": "id"},
        )
        latest = jobs[0] if jobs else None
        return {
            "run_id": run_id,
            "status": latest.get("status") if latest else "not_requested",
            "job_id": latest.get("id") if latest else None,
            "model": latest.get("model") if latest else None,
            "completed_at": latest.get("completed_at") if latest else None,
            "error_message": latest.get("error_message") if latest else None,
            "groups_count": len(real_groups),
            "grouped": bool(real_groups) and bool(latest and latest.get("status") == "completed"),
        }

    def ai_grouping_debug(self, run_id: str) -> dict[str, Any]:
        jobs = self._select(
            "ai_analysis_jobs",
            {"run_id": "eq." + run_id, "order": "created_at.desc", "limit": "1"},
        )
        latest = jobs[0] if jobs else None
        if not latest:
            return {"run_id": run_id, "job": None}
        response = latest.get("response_json") or {}
        request = latest.get("request_json") or {}
        return {
            "run_id": run_id,
            "job": {
                "id": latest.get("id"),
                "provider": latest.get("provider"),
                "model": latest.get("model"),
                "status": latest.get("status"),
                "error_message": latest.get("error_message"),
                "created_at": latest.get("created_at"),
                "completed_at": latest.get("completed_at"),
            },
            "request_summary": _ai_request_summary(request),
            "response_debug": _ai_response_debug(response),
        }

    def save_ai_job(self, row: dict[str, Any]) -> None:
        self._upsert("ai_analysis_jobs", [row])

    def persist_ai_grouping(self, run_id: str, job_id: str, rows: dict[str, list[dict[str, Any]]]) -> None:
        groups = rows.get("groups") or []
        links = rows.get("links") or []
        actions = rows.get("actions") or []
        try:
            self._upsert("ai_groups", groups)
            self._upsert("ai_group_occurrences", links, conflict="group_id,occurrence_id")
            self._upsert("recommended_actions", actions)
            self._rest_json(
                "PATCH",
                "/" + self._table("runs"),
                {"total_ai_groups": len(groups), "updated_at": now_iso()},
                query={"id": "eq." + run_id},
                extra_headers={"Prefer": "return=minimal"},
            )
            self._delete("ai_groups", {"run_id": "eq." + run_id, "ai_analysis_job_id": "is.null"})
        except Exception:
            self._delete("ai_groups", {"ai_analysis_job_id": "eq." + job_id})
            raise

    def groups(self, run_id: str) -> list[dict[str, Any]]:
        links = self.group_links(run_id)
        rows = self._select("ai_groups", {"run_id": "eq." + run_id, "order": "created_at.asc,id.asc"})
        out = []
        for row in rows:
            row = dict(row)
            row["quantity"] = len(links.get(row["id"], []))
            out.append(group_api_row(row))
        return out

    def group_links(self, run_id: str) -> dict[str, list[str]]:
        groups = self._select("ai_groups", {"run_id": "eq." + run_id})
        group_ids = [row["id"] for row in groups]
        if not group_ids:
            return {}
        rows = self._select(
            "ai_group_occurrences",
            {
                "group_id": "in.(" + ",".join(group_ids) + ")",
                "order": "group_id.asc,occurrence_id.asc",
            },
        )
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row["group_id"], []).append(row["occurrence_id"])
        return out

    def reexecutable_cases(self, run_id: str) -> list[dict[str, Any]]:
        failures = self.failures(run_id)
        groups_by_id = {group["id"]: group for group in self.groups(run_id)}
        out = []
        for failure in failures:
            group = groups_by_id.get(failure.get("fk_cluster") or "")
            out.append(
                {
                    "id_falha": failure["id"],
                    "id_caso_teste": failure["id_caso_teste"],
                    "nome_mds": failure["nome_mds"],
                    "grupo": failure["grupo"],
                    "arquivo_origem": failure["arquivo_origem"],
                    "cluster_id": group.get("id") if group else None,
                    "cluster_status": group.get("status") if group else None,
                    "cluster_titulo": group.get("title") if group else None,
                    "cluster_assinatura": group.get("technical_signature") if group else None,
                    "tipo_ocorrencia": reexecutable_type(group.get("status") if group else failure.get("status")),
                }
            )
        return out

    def next_steps(self, run_id: str) -> list[dict[str, Any]]:
        return self._select("recommended_actions", {"run_id": "eq." + run_id, "order": "created_at.asc,id.asc"})

    def performance(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._select("run_delays", {"run_id": "eq." + run_id, "order": "delay_seconds.desc,testcase_node_id.asc"})
        return [delay_api_row(row) for row in rows]

    def testcase_hierarchy(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        params = {"order": "module_code.asc,node_id.asc"}
        if module_slug:
            module = self._module_by_slug(module_slug)
            if not module:
                return []
            params["module_id"] = "eq." + module["id"]
        rows = self._select("testcase_hierarchy", params)
        return [hierarchy_api_row(row) for row in rows]

    def payload(self, run_id: str) -> dict[str, Any] | None:
        run = self.run(run_id)
        if not run:
            return None
        return {
            "modo": "supabase",
            "rodagem": run,
            "falhas": self.failures(run_id),
            "evidencias": self.evidences(run_id),
            "agrupamentos": self.groups(run_id),
            "testcase_hierarchy": self.testcase_hierarchy(run.get("modulo_slug")),
            "proximos_passos": self.next_steps(run_id),
            "atrasos_rodagem": self.performance(run_id),
        }

    def rerun_requests(self) -> list[dict[str, Any]]:
        return self._select("rerun_requests", {"order": "created_at.desc", "limit": "100"})

    def record_rerun_request(self, request: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        row = {
            "id": request.get("id") or str(uuid.uuid4()),
            "source_run_id": request.get("source_run_id") or request.get("fk_rodagem"),
            "vm_name": request.get("vm_name") or "",
            "version": request.get("version") or request.get("versao") or "",
            "module_id": request.get("module_id"),
            "test_cases": request.get("test_cases") or request.get("casos_teste") or "",
            "parallel": request.get("parallel") or request.get("paralelo"),
            "ct_desmarcar": request.get("ct_desmarcar"),
            "branch": request.get("branch"),
            "requested_by": request.get("requested_by") or request.get("solicitado_por"),
            "request_type": request.get("request_type") or request.get("tipo_solicitacao") or "manual",
            "configuration_mode": request.get("configuration_mode") or request.get("modo_configuracao") or "api",
            "config_json": request,
            "status": request.get("status") or "requested",
            "jenkins_queue_url": None,
            "jenkins_build_url": None,
            "jenkins_build_number": None,
            "execution_status": None,
            "execution_result": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        self._upsert("rerun_requests", [row])
        return row

    def ensure_bucket(self) -> None:
        if self.dry_run:
            return
        try:
            self._storage_json("GET", f"/bucket/{quote(self.bucket, safe='')}")
            return
        except SupabaseHttpError as exc:
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
        self.plan["uploads"] += 1
        if self.dry_run:
            return {
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
            "bucket": self.bucket,
            "storage_path": storage_path,
            "public_url": self.public_url(storage_path) if self.storage_public else "",
            "signed_url": signed_url,
            "signed_url_expires_at": signed_expires_at,
        }

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

    def _import_hierarchy(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        now = now_iso()
        rows = []
        out = {}
        for source in payload.get("testcase_hierarchy") or []:
            node_id = str(source.get("node_id") or "")
            module_code = str(source.get("modulo_codigo") or node_id.split(".", 1)[0])
            module_id = MODULE_ID_BY_CODE.get(module_code, "mod_geral")
            row = {
                "id": str(source.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, f"{source.get('sistema') or 'Unico'}|{node_id}")),
                "system": source.get("sistema") or module_system(module_id),
                "module_id": module_id,
                "module_code": module_code,
                "module_name": source.get("modulo_nome") or module_name(module_id),
                "node_id": node_id,
                "parent_node_id": source.get("parent_node_id"),
                "node_name": source.get("node_name") or "",
                "node_type": translate_node_type(source.get("node_type")),
                "full_path_ids_json": source.get("full_path_ids") or [],
                "full_path_names_json": source.get("full_path_names") or [],
                "full_path_label": source.get("full_path_label") or "",
                "script_name": source.get("script_name") or "",
                "procedure_name": source.get("procedure_name") or "",
                "mds_path": source.get("mds_path") or "",
                "created_at": source.get("created_at") or now,
                "updated_at": now,
            }
            rows.append(row)
            out[node_id] = row
        self._upsert("testcase_hierarchy", rows)
        return out

    def _import_occurrences(self, payload: dict[str, Any], hierarchy_rows: dict[str, dict[str, Any]]) -> dict[str, str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
        rows = []
        occurrence_by_case = {}
        for source in payload.get("falhas") or []:
            case_id = str(source.get("id_caso_teste") or "")
            parent_id = hierarchy_rows.get(case_id, {}).get("parent_node_id") or parent_case_id(case_id)
            status = source.get("tipo_detectado_python") or source.get("status") or "unknown"
            row = {
                "id": source["id_falha"],
                "run_id": run_id,
                "module_id": module_id,
                "testcase_node_id": case_id,
                "testcase_name": source.get("nome_mds") or "",
                "group_node_id": parent_id,
                "group_name": source.get("grupo") or "",
                "source_archive_name": source.get("arquivo_origem") or "",
                "source_archive_size_bytes": None,
                "occurrence_type": occurrence_type(status),
                "status": status,
                "error_message": source.get("erro_resumo") or "",
                "log_summary": source.get("erro_resumo") or source.get("descricao") or "",
                "technical_signature": source.get("procedure_name") or "",
                "created_at": source.get("created_at") or now,
                "updated_at": now,
            }
            rows.append(row)
            occurrence_by_case[case_id] = row["id"]
        self._upsert("occurrences", rows)
        return occurrence_by_case

    def _import_ai_groups(self, payload: dict[str, Any]) -> set[str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
        rows = []
        group_ids = set()
        for source in payload.get("agrupamentos") or payload.get("agrupamentos_shadow") or []:
            row = {
                "id": source["id_cluster"],
                "run_id": run_id,
                "module_id": module_id,
                "ai_analysis_job_id": None,
                "title": source.get("titulo_causa") or "",
                "technical_signature": source.get("assinatura_tecnica") or "",
                "classification": source.get("status") or "",
                "confidence": source.get("confianca"),
                "justification": source.get("justificativa") or source.get("raio_x_negocio") or "",
                "status": source.get("status") or "pending",
                "created_at": source.get("created_at") or now,
                "updated_at": now,
            }
            rows.append(row)
            group_ids.add(row["id"])
        self._upsert("ai_groups", rows)
        return group_ids

    def _import_group_links(self, payload: dict[str, Any], group_ids: set[str]) -> None:
        now = now_iso()
        rows = []
        for group in payload.get("agrupamentos") or payload.get("agrupamentos_shadow") or []:
            group_id = group.get("id_cluster")
            if group_id not in group_ids:
                continue
            for occurrence_id in group.get("falhas") or []:
                rows.append({"group_id": group_id, "occurrence_id": occurrence_id, "created_at": now})
        self._upsert("ai_group_occurrences", rows, conflict="group_id,occurrence_id")

    def _import_evidence(self, payload: dict[str, Any]) -> dict[tuple[str, str], str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
        rows = []
        by_occurrence_name = {}
        for source in payload.get("evidencias") or []:
            role = file_role(source.get("tipo_arquivo"), source.get("storage_path"))
            local_path = source.get("caminho_evidencia") or ""
            mime_type = source.get("mime_type") or "application/octet-stream"
            try:
                upload = self.upload_file(local_path, source.get("storage_path") or "", mime_type)
            except Exception as exc:
                self.plan["skipped_evidence"] += 1
                self.plan["upload_errors"].append(
                    {
                        "id_evidencia": source.get("id_evidencia"),
                        "path": local_path,
                        "error": str(exc),
                    }
                )
                continue
            row = {
                "id": source["id_evidencia"],
                "run_id": run_id,
                "occurrence_id": source.get("fk_falha"),
                "module_id": module_id,
                "file_role": role,
                "file_type": file_type(mime_type, source.get("extensao"), role),
                "original_name": source.get("nome_arquivo") or Path(local_path).name,
                "local_path": local_path,
                "storage_provider": "supabase",
                "storage_bucket": upload["bucket"],
                "storage_path": upload["storage_path"],
                "public_url": upload["public_url"],
                "signed_url": upload["signed_url"],
                "signed_url_expires_at": upload["signed_url_expires_at"],
                "mime_type": mime_type,
                "extension": clean_ext(source.get("extensao")),
                "size_bytes": source.get("tamanho_bytes"),
                "sha256": sha256_file(local_path),
                "upload_status": "dry_run" if self.dry_run else "uploaded",
                "created_at": source.get("created_at") or now,
                "updated_at": now,
            }
            rows.append(row)
            if row["occurrence_id"] and row["original_name"]:
                by_occurrence_name[(row["occurrence_id"], row["original_name"].lower())] = row["id"]
        self._upsert("evidence_files", rows)
        return by_occurrence_name

    def _import_report_differences(
        self,
        payload: dict[str, Any],
        occurrence_by_case: dict[str, str],
        evidence_by_occurrence_name: dict[tuple[str, str], str],
    ) -> int:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
        rows = []
        for source in payload.get("diferencas_relatorio") or []:
            case_id = str(source.get("id_caso_teste") or "")
            occurrence_id = source.get("fk_falha") or occurrence_by_case.get(case_id)
            if not occurrence_id:
                continue
            summary = source.get("resumo_diferenca") or {}
            base_name = source.get("nome_arquivo_base") or ""
            current_name = source.get("nome_arquivo_atual") or ""
            base_evidence_id = evidence_by_occurrence_name.get((occurrence_id, base_name.lower()))
            current_evidence_id = evidence_by_occurrence_name.get((occurrence_id, current_name.lower()))
            if not self.dry_run and (not base_evidence_id or not current_evidence_id):
                continue
            rows.append(
                {
                    "id": source["id_diferenca"],
                    "run_id": run_id,
                    "occurrence_id": occurrence_id,
                    "module_id": module_id,
                    "testcase_node_id": case_id,
                    "base_evidence_id": base_evidence_id,
                    "current_evidence_id": current_evidence_id,
                    "base_file_name": base_name,
                    "current_file_name": current_name,
                    "base_lines": summary.get("linhas_base"),
                    "current_lines": summary.get("linhas_atual"),
                    "changed_lines_estimate": summary.get("linhas_alteradas_estimadas"),
                    "summary_json": summary,
                    "created_at": source.get("created_at") or now,
                }
            )
        self._upsert("report_differences", rows)
        return len(rows)

    def _import_run_delays(self, payload: dict[str, Any]) -> set[str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        rows = []
        ids = set()
        for source in payload.get("atrasos_rodagem") or []:
            row = {
                "id": source["id_atraso"],
                "run_id": run_id,
                "module_id": module_id,
                "testcase_node_id": source.get("codigo_teste") or "",
                "testcase_name": source.get("nome_teste") or "",
                "expected_seconds": source.get("tempo_padrao_segundos") or 0,
                "actual_seconds": source.get("tempo_atual_segundos") or 0,
                "delay_seconds": source.get("delay_segundos") or 0,
                "status": source.get("status") or "mais_lento",
                "created_at": source.get("created_at") or now_iso(),
            }
            rows.append(row)
            ids.add(row["id"])
        self._upsert("run_delays", rows)
        return ids

    def _module_by_slug(self, slug: str) -> dict[str, Any] | None:
        rows = self._select("modules", {"slug": "eq." + slug, "limit": "1"})
        return rows[0] if rows else None

    def _with_module(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        module_id = row.get("module_id")
        module = self._select("modules", {"id": "eq." + str(module_id), "limit": "1"})
        if module:
            row["module_slug"] = module[0]["slug"]
            row["module_name"] = module[0]["name"]
        else:
            row["module_slug"] = SLUG_BY_MODULE_ID.get(str(module_id)) or ""
            row["module_name"] = module_name(str(module_id))
        return row

    def _upsert(self, table: str, rows: list[dict[str, Any]], conflict: str = "id") -> None:
        if not rows:
            return
        original_count = len(rows)
        rows = _deduplicate_rows(rows, conflict)
        removed = original_count - len(rows)
        if removed:
            self.plan["deduplicated_rows"][table] = self.plan["deduplicated_rows"].get(table, 0) + removed
        self.plan["upserts"][table] = self.plan["upserts"].get(table, 0) + len(rows)
        if self.dry_run:
            return
        for chunk in chunks(rows, 250):
            self._rest_json(
                "POST",
                "/" + self._table(table),
                chunk,
                query={"on_conflict": conflict},
                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )

    def _select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        return self._rest_json("GET", "/" + self._table(table), query=params or {})

    def _delete(self, table: str, params: dict[str, str]) -> None:
        if self.dry_run:
            return
        self._rest_json(
            "DELETE",
            "/" + self._table(table),
            query=params,
            extra_headers={"Prefer": "return=minimal"},
        )

    def _table(self, logical_name: str) -> str:
        return self.table_prefix + logical_name

    def _rest_json(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        *,
        query: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        headers = {
            "Accept": "application/json",
            "apikey": self.service_key,
            "Authorization": "Bearer " + self.service_key,
            "Accept-Profile": self.schema,
            "Content-Profile": self.schema,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        return self._request_json(method, self.url + "/rest/v1" + path, headers, data, query)

    def _storage_json(self, method: str, path: str, body: Any | None = None) -> Any:
        headers = {
            "Accept": "application/json",
            "apikey": self.service_key,
            "Authorization": "Bearer " + self.service_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        return self._request_json(method, self.url + "/storage/v1" + path, headers, data, None)

    def _storage_bytes(self, method: str, path: str, body: bytes, extra_headers: dict[str, str]) -> bytes:
        headers = {
            "apikey": self.service_key,
            "Authorization": "Bearer " + self.service_key,
            **extra_headers,
        }
        return self._request_bytes(method, self.url + "/storage/v1" + path, headers, body, None)

    def _request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: bytes | None,
        query: dict[str, str] | None,
    ) -> Any:
        response = self._request_bytes(method, url, headers, data, query)
        if not response:
            return None
        return json.loads(response.decode("utf-8"))

    def _request_bytes(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: bytes | None,
        query: dict[str, str] | None,
    ) -> bytes:
        if query:
            url += "?" + urlencode(query)
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SupabaseHttpError(method, url, exc.code, body) from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {url} -> {exc}") from exc


def read_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path(path)
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_BUCKET",
        "SUPABASE_BUCKET_PUBLIC",
        "SUPABASE_SCHEMA",
        "SUPABASE_TABLE_PREFIX",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _deduplicate_rows(rows: list[dict[str, Any]], conflict: str) -> list[dict[str, Any]]:
    keys = [key.strip() for key in conflict.split(",") if key.strip()]
    if not keys:
        return rows
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        identity = tuple(row.get(key) for key in keys)
        if identity not in by_key:
            order.append(identity)
        by_key[identity] = row
    return [by_key[identity] for identity in order]


def _unique_count(rows: list[dict[str, Any]], key: str) -> int:
    return len({str(row.get(key)) for row in rows if row.get(key) is not None})


def _ai_request_summary(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    return {
        "contract_version": request.get("contract_version"),
        "failures_count": len(request.get("falhas") or []),
        "metadata": request.get("metadata") if isinstance(request.get("metadata"), dict) else {},
    }


def _ai_response_debug(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"type": type(response).__name__}
    out = {
        "keys": sorted(str(key) for key in response.keys()),
        "error": response.get("error"),
        "raw_text_length": response.get("raw_text_length"),
    }
    preview = response.get("raw_text_preview")
    if isinstance(preview, str):
        out["raw_text_preview"] = preview[:4000]
    validated = response.get("validated")
    if isinstance(validated, dict):
        out["validated_clusters"] = len(validated.get("clusters") or [])
    return out

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .api_repository import OFFICIAL_MODULES, SLUG_BY_MODULE_ID, _node_sort_key
from .constants import MODULE_CODES_BY_ID
from .sqlite_repository import (
    MODULE_ID_BY_CODE,
    ai_job_is_stale,
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
    new_rerun_request_status,
    now_iso,
    occurrence_api_row,
    occurrence_type,
    parent_case_id,
    reexecutable_type,
    rerun_request_conflict,
    run_api_row,
    sha256_file,
    stale_ai_job_message,
    translate_node_type,
)
from .storage import DEFAULT_BUCKET, storage_from_env
from . import tempos_casos
from .mod_xml_desmarcados import motivo as mod_xml_motivo
from .testcase_disabled import MOD_XML_AUTOR, module_of_node


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env"
DEFAULT_SCHEMA = "public"
DEFAULT_TABLE_PREFIX = "agent_tc_"


class CanonicalRepository:
    backend_name = "canonical"

    def __init__(
        self,
        env_path: str | Path | None = None,
        *,
        bucket: str | None = None,
        schema: str | None = None,
        table_prefix: str | None = None,
        dry_run: bool = False,
    ):
        env = read_env(env_path or DEFAULT_ENV)
        self.bucket = bucket or env.get("AGENT_TC_STORAGE_BUCKET") or DEFAULT_BUCKET
        self.schema = schema or env.get("POSTGRES_SCHEMA") or DEFAULT_SCHEMA
        self.table_prefix = table_prefix if table_prefix is not None else env.get("POSTGRES_TABLE_PREFIX", DEFAULT_TABLE_PREFIX)
        self.dry_run = dry_run
        self.storage = storage_from_env(
            env,
            default_bucket=self.bucket,
            dry_run=dry_run,
            default_provider="local",
        )
        self.bucket = self.storage.bucket
        self.plan: dict[str, Any] = self._new_plan()

    def _new_plan(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "backend": self.backend_name,
            "schema": self.schema,
            "table_prefix": self.table_prefix,
            "bucket": self.bucket,
            "storage_provider": self.storage.provider,
            "upserts": {},
            "uploads": 0,
            "skipped_evidence": 0,
            "upload_errors": [],
            "deduplicated_rows": {},
            "stale_rows_deleted": {},
        }

    def _import_scope(self):
        """Escopo das escritas de uma importacao. Backends com transacao sobrescrevem."""
        return nullcontext()

    def _remove_stale_run_rows(
        self,
        run_id: str,
        *,
        occurrence_ids: set[str],
        evidence_ids: set[str],
        difference_ids: set[str],
        delay_ids: set[str],
        shadow_group_ids: set[str],
    ) -> set[str]:
        """Remove linhas desta rodagem que nao vieram na importacao atual.

        Uma reimportacao (por exemplo, com menos compactados) nao pode deixar
        ocorrencias e evidencias antigas no banco. Agrupamentos da IA sao
        preservados; seus vinculos com ocorrencias removidas caem por cascade.
        Retorna os storage_paths das evidencias removidas.
        """
        if self.dry_run:
            return set()
        base = {"run_id": "eq." + run_id}
        # Ordem respeita as FKs: diferencas apontam para evidencias.
        self._delete_stale_rows("report_differences", base, difference_ids)
        self._delete_stale_rows("run_delays", base, delay_ids)
        stale_evidence = self._delete_stale_rows("evidence_files", base, evidence_ids, extra_columns="storage_path")
        self._delete_stale_rows("ai_groups", {**base, "ai_analysis_job_id": "is.null"}, shadow_group_ids)
        self._delete_stale_rows("occurrences", base, occurrence_ids)
        return {str(row["storage_path"]) for row in stale_evidence if row.get("storage_path")}

    def _delete_stale_rows(
        self,
        table: str,
        params: dict[str, str],
        keep_ids: set[str],
        *,
        extra_columns: str = "",
    ) -> list[dict[str, Any]]:
        select = "id," + extra_columns if extra_columns else "id"
        rows = self._select_all(table, {**params, "select": select})
        stale = [row for row in rows if str(row.get("id")) not in keep_ids]
        if stale:
            self._delete_with_in(table, "id", [str(row["id"]) for row in stale])
            self.plan["stale_rows_deleted"][table] = len(stale)
        return stale

    def initialize(self) -> None:
        if self.dry_run:
            self.plan["initialize"] = "skipped_dry_run"
            return
        self.storage.initialize()
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
            self.storage.initialize()
        self.seed_modules()
        # O plano descreve somente esta importacao; a instancia do repositorio
        # e reutilizada pela API entre ingestoes.
        self.plan = self._new_plan()

        run_row = {
            "id": run_id,
            "system": run.get("sistema") or module_system(module_id),
            "version": run.get("versao") or "",
            "vm_name": str(run.get("vm_name") or "").lower(),
            "module_id": module_id,
            "started_at": run.get("data_inicio") or now,
            # Reprocessar a pasta depois nao muda o fim: vale o da primeira analise.
            "finished_at": self._fim_ja_gravado(run_id) or run.get("data_fim"),
            "logs_path": run.get("caminho_logs") or "",
            "status": "processing" if not self.dry_run else "dry_run",
            "total_archives": run.get("total_archives") if run.get("total_archives") is not None else len(payload.get("falhas") or []),
            "total_occurrences": len(payload.get("falhas") or []),
            "total_executed": run.get("total_executed") if run.get("total_executed") is not None else run.get("total_analisados"),
            "total_ai_groups": len(payload.get("agrupamentos_shadow") or payload.get("agrupamentos") or []),
            "repository_url": run.get("repositorio_url"),
            "repository_root": run.get("repositorio_raiz"),
            "repository_revision": run.get("repositorio_revisao"),
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
            with self._import_scope():
                hierarchy_rows = self._import_hierarchy(payload)
                occurrence_by_case = self._import_occurrences(payload, hierarchy_rows)
                group_ids = self._import_ai_groups(payload)
                self._import_group_links(payload, group_ids)
                evidence = self._import_evidence(payload)
                difference_ids = self._import_report_differences(
                    payload,
                    occurrence_by_case,
                    evidence["by_occurrence_name"],
                )
                delay_ids = self._import_run_delays(payload)
                self._import_testcase_times(payload)
                self._sync_mod_xml(payload)
                stale_storage_paths = self._remove_stale_run_rows(
                    run_id,
                    occurrence_ids={str(row["id_falha"]) for row in payload.get("falhas") or [] if row.get("id_falha")},
                    evidence_ids=evidence["ids"],
                    difference_ids=difference_ids,
                    delay_ids=delay_ids,
                    shadow_group_ids=group_ids,
                )
                expected = {
                    "occurrences": _unique_count(payload.get("falhas") or [], "id_falha"),
                    "evidence_files": len(evidence["ids"]),
                    "report_differences": len(difference_ids),
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
            # Arquivos so saem do storage depois que o banco confirmou a importacao.
            stale_files = self.storage.delete_paths(sorted(stale_storage_paths - evidence["storage_paths"]))
            self.plan["stale_storage_deleted"] = stale_files["deleted"]
            self.plan["stale_storage_errors"] = stale_files["errors"]
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
            "backend": self.backend_name,
            "schema": self.schema,
            "table_prefix": self.table_prefix,
            "bucket": self.bucket,
            "dry_run": self.dry_run,
            "run_id": run_id,
            "modules": len(OFFICIAL_MODULES),
            "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
            "occurrences": len(payload.get("falhas") or []),
            "evidence_files": len(evidence["ids"]),
            "report_differences": len(difference_ids),
            "run_delays": len(payload.get("atrasos_rodagem") or []),
            "ai_groups": len(payload.get("agrupamentos_shadow") or payload.get("agrupamentos") or []),
            "plan": self.plan,
            "verification": verification,
        }

    def purge_inactive_versions(
        self,
        *,
        retention_days: int = 30,
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=retention_days)
        cutoff_iso = cutoff.isoformat(timespec="seconds")
        result: dict[str, Any] = {
            "backend": self.backend_name,
            "schema": self.schema,
            "table_prefix": self.table_prefix,
            "bucket": self.bucket,
            "retention_days": retention_days,
            "cutoff": cutoff_iso,
            "dry_run": dry_run,
            "versions": [],
            "totals": {
                "versions": 0,
                "runs": 0,
                "evidence_files": 0,
                "storage_paths": 0,
                "storage_deleted": 0,
            },
            "storage_errors": [],
        }
        run_rows = self._select_all(
            "runs",
            {"select": "id,system,version,started_at", "order": "started_at.asc"},
        )
        by_version: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in run_rows:
            version = str(row.get("version") or "").strip()
            started_at = str(row.get("started_at") or "").strip()
            if not version or not started_at:
                continue
            by_version.setdefault((str(row.get("system") or ""), version), []).append(row)

        for (system, version), rows in sorted(by_version.items(), key=lambda item: max(str(row.get("started_at") or "") for row in item[1])):
            latest_started_at = max(str(row.get("started_at") or "") for row in rows)
            if latest_started_at >= cutoff_iso:
                continue
            run_ids = [str(row["id"]) for row in rows if row.get("id")]
            evidence_rows = self._select_where_in(
                "evidence_files",
                "run_id",
                run_ids,
                {"select": "id,storage_path"},
            )
            storage_paths = sorted({str(row.get("storage_path") or "") for row in evidence_rows if row.get("storage_path")})
            item = {
                "system": system,
                "version": version,
                "latest_started_at": latest_started_at,
                "runs": len(run_ids),
                "evidence_files": len(evidence_rows),
                "storage_paths": len(storage_paths),
                "run_ids": run_ids,
            }
            result["versions"].append(item)
            result["totals"]["versions"] += 1
            result["totals"]["runs"] += len(run_ids)
            result["totals"]["evidence_files"] += len(evidence_rows)
            result["totals"]["storage_paths"] += len(storage_paths)

        if dry_run or not result["versions"]:
            return result

        for item in result["versions"]:
            run_ids = item["run_ids"]
            evidence_rows = self._select_where_in(
                "evidence_files",
                "run_id",
                run_ids,
                {"select": "storage_path"},
            )
            storage_paths = sorted({str(row.get("storage_path") or "") for row in evidence_rows if row.get("storage_path")})
            deleted = self.storage.delete_paths(storage_paths)
            result["totals"]["storage_deleted"] += deleted["deleted"]
            result["storage_errors"].extend(deleted["errors"])
            if deleted["errors"]:
                raise RuntimeError(
                    "Falha ao limpar bucket antes de apagar banco: "
                    + json.dumps(deleted["errors"], ensure_ascii=False)[:2000]
                )
            self._patch_with_in("rerun_requests", "source_run_id", run_ids, {"source_run_id": None})
            self._delete_with_in("report_differences", "run_id", run_ids)
            self._delete_with_in("recommended_actions", "run_id", run_ids)
            group_rows = self._select_where_in("ai_groups", "run_id", run_ids, {"select": "id"})
            group_ids = [str(row["id"]) for row in group_rows if row.get("id")]
            self._delete_with_in("ai_group_occurrences", "group_id", group_ids)
            self._delete_with_in("ai_groups", "run_id", run_ids)
            self._delete_with_in("ai_analysis_jobs", "run_id", run_ids)
            self._delete_with_in("run_delays", "run_id", run_ids)
            self._delete_with_in("evidence_files", "run_id", run_ids)
            self._delete_with_in("ingestion_batches", "run_id", run_ids)
            self._delete_with_in("occurrences", "run_id", run_ids)
            self._delete_with_in("runs", "id", run_ids)
        return result

    def _verify_run_import(self, run_id: str, expected: dict[str, int]) -> dict[str, Any]:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "expected": expected, "actual": expected}
        actual = {
            "occurrences": len(self._select_all("occurrences", {"run_id": "eq." + run_id, "select": "id"})),
            "evidence_files": len(self._select_all("evidence_files", {"run_id": "eq." + run_id, "select": "id"})),
            "report_differences": len(self._select_all("report_differences", {"run_id": "eq." + run_id, "select": "id"})),
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
        rows = self._select_all("runs", params)
        modules_by_id = self._modules_by_id()
        errors = self._processing_errors_by_run()
        return [
            {**run_api_row(self._with_module(row, modules_by_id)), "erros_processamento": errors.get(str(row["id"]), [])}
            for row in rows
        ]

    def _processing_errors_by_run(self, run_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Erros de processamento da ultima importacao concluida de cada rodagem (RAR ausente, ilegivel...)."""
        params = {"select": "run_id,summary_json", "status": "eq.completed", "order": "created_at.asc"}
        if run_id:
            params["run_id"] = "eq." + run_id
        # Ordem crescente: a importacao mais recente sobrescreve as anteriores.
        return {
            str(row["run_id"]): list((row.get("summary_json") or {}).get("erros_processamento") or [])
            for row in self._select_all("ingestion_batches", params)
        }

    def latest_runs_by_module(self) -> list[dict[str, Any]]:
        """Ultima rodagem de cada modulo (null quando o modulo ainda nao tem rodagem).

        Atende a visao geral do dashboard sem baixar o historico inteiro de cada modulo.
        """
        modules = self._select("modules", {"order": "sort_order.asc,name.asc"})
        modules_by_id = {str(row["id"]): row for row in modules}
        out = []
        for module in modules:
            rows = self._select(
                "runs",
                {"module_id": "eq." + str(module["id"]), "order": "started_at.desc", "limit": "1"},
            )
            latest = run_api_row(self._with_module(rows[0], modules_by_id)) if rows else None
            out.append({"modulo": module_api_row(module), "rodagem": latest})
        return out

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._select("runs", {"id": "eq." + run_id, "limit": "1"})
        if not rows:
            return None
        return {**run_api_row(self._with_module(rows[0])), "erros_processamento": self._processing_errors_by_run(run_id).get(run_id, [])}

    def failures(self, run_id: str) -> list[dict[str, Any]]:
        links = self.group_links(run_id)
        group_by_occurrence = {
            occurrence_id: group_id
            for group_id, occurrence_ids in links.items()
            for occurrence_id in occurrence_ids
        }
        rows = self._select_all("occurrences", {"run_id": "eq." + run_id, "order": "testcase_node_id.asc"})
        out = []
        for row in rows:
            row = dict(row)
            row["group_id"] = group_by_occurrence.get(row["id"])
            out.append(occurrence_api_row(row))
        return out

    def evidences(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._select_all("evidence_files", {"run_id": "eq." + run_id, "order": "occurrence_id.asc,file_role.asc,original_name.asc"})
        return [evidence_api_row(row) for row in rows]

    def evidences_by_failure(self, failure_id: str) -> list[dict[str, Any]]:
        rows = self._select_all("evidence_files", {"occurrence_id": "eq." + failure_id, "order": "file_role.asc,original_name.asc"})
        return [evidence_api_row(row) for row in rows]

    def report_differences(self, run_id: str) -> list[dict[str, Any]]:
        return self._select_all(
            "report_differences",
            {"run_id": "eq." + run_id, "order": "testcase_node_id.asc,base_file_name.asc"},
        )

    def ai_grouping_status(self, run_id: str) -> dict[str, Any]:
        jobs = self._select(
            "ai_analysis_jobs",
            {"run_id": "eq." + run_id, "order": "created_at.desc", "limit": "1"},
        )
        real_groups = self._select_all(
            "ai_groups",
            {"run_id": "eq." + run_id, "ai_analysis_job_id": "not.is.null", "select": "id"},
        )
        latest = jobs[0] if jobs else None
        return {
            "run_id": run_id,
            "status": latest.get("status") if latest else "not_requested",
            "job_id": latest.get("id") if latest else None,
            "model": latest.get("model") if latest else None,
            "created_at": latest.get("created_at") if latest else None,
            "completed_at": latest.get("completed_at") if latest else None,
            "error_message": latest.get("error_message") if latest else None,
            "groups_count": len(real_groups),
            "grouped": bool(real_groups) and bool(latest and latest.get("status") == "completed"),
        }

    def expire_stale_ai_jobs(self, run_id: str, older_than_minutes: int) -> int:
        jobs = self._select(
            "ai_analysis_jobs",
            {"run_id": "eq." + run_id, "status": "eq.running", "select": "id,created_at"},
        )
        expired = 0
        for job in jobs:
            if not ai_job_is_stale(job.get("created_at"), older_than_minutes):
                continue
            self._rest_json(
                "PATCH",
                "/" + self._table("ai_analysis_jobs"),
                {
                    "status": "failed",
                    "error_message": stale_ai_job_message(older_than_minutes),
                    "completed_at": now_iso(),
                },
                query={"id": "eq." + str(job["id"]), "status": "eq.running"},
                extra_headers={"Prefer": "return=minimal"},
            )
            expired += 1
        return expired

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
        rows = self._select_all("ai_groups", {"run_id": "eq." + run_id, "order": "created_at.asc,id.asc"})
        out = []
        for row in rows:
            row = dict(row)
            row["quantity"] = len(links.get(row["id"], []))
            out.append(group_api_row(row))
        return out

    def group_links(self, run_id: str) -> dict[str, list[str]]:
        groups = self._select_all("ai_groups", {"run_id": "eq." + run_id})
        group_ids = [row["id"] for row in groups]
        if not group_ids:
            return {}
        rows = self._select_all(
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

    def rerun_results(self, run_id: str) -> list[dict[str, Any]]:
        """Reexecucoes pedidas a partir desta rodagem e o que cada caso deu nelas (passou, quebrou de novo...)."""
        out = []
        for request in self._select_all("rerun_requests", {"source_run_id": "eq." + run_id, "order": "created_at.asc"}):
            status = str(request.get("execution_status") or request.get("status") or "").lower()
            casos = [c for c in _case_ids(request.get("test_cases")) if c != "5.0"]  # [5.0] e a base do Financeiro
            rerun_run = self._run_of_rerun(request) if status.startswith("finalizado") else None
            if not _rerun_request_is_terminal(request):
                resultado = dict.fromkeys(casos, {"resultado": "aguardando"})
            elif not status.startswith("finalizado"):
                resultado = dict.fromkeys(casos, {"resultado": "nao_rodou"})
            elif not rerun_run:
                resultado = dict.fromkeys(casos, {"resultado": "aguardando_importacao"})
            else:
                tipos = {
                    _clean_case_id(row.get("testcase_node_id")): row.get("occurrence_type")
                    for row in self._select_all("occurrences", {"run_id": "eq." + rerun_run, "select": "testcase_node_id,occurrence_type"})
                }
                medidos = {
                    _clean_case_id(row.get("node_id"))
                    for row in self._select_all("testcase_times", {"run_id": "eq." + rerun_run, "select": "node_id"})
                }
                resultado = {
                    c: {"resultado": "quebrou_de_novo", "tipo": tipos[c]} if c in tipos
                    else {"resultado": "passou" if c in medidos else "nao_rodou"}
                    for c in casos
                }
            out.append({
                "pedido_id": request.get("id"),
                "criado_em": request.get("created_at"),
                "vm_name": request.get("vm_name"),
                "status": status,
                "rodagem_id": rerun_run,
                "casos": resultado,
            })
        return out

    def unstable_cases(self, run_id: str, window: int = 8) -> dict[str, dict[str, Any]]:
        """Casos com falha nesta rodagem que alternam entre falhar e passar nas ultimas rodagens da mesma VM e modulo.

        Instavel: nas rodagens em que o caso rodou, falhou pelo menos 2 vezes, passou pelo menos 2 e trocou
        de resultado pelo menos 3 vezes (quebrou e foi corrigido uma vez so nao conta).
        """
        rows = self._select("runs", {"id": "eq." + run_id, "limit": "1"})
        if not rows:
            return {}
        atual = rows[0]
        vm = str(atual.get("vm_name") or "")
        inicio = _parse_dt(atual.get("started_at"))
        if not vm or not inicio:
            return {}
        anteriores = [
            r for r in self._select_all(
                "runs",
                {"module_id": "eq." + str(atual["module_id"]), "vm_name": f"in.({vm.lower()},{vm.upper()})",
                 "select": "id,started_at", "order": "started_at.desc"},
            )
            if (_parse_dt(r.get("started_at")) or inicio) <= inicio
        ][:window]
        ids = [str(r["id"]) for r in anteriores]
        falhas: dict[str, set[str]] = {}
        for row in self._select_where_in("occurrences", "run_id", ids, {"select": "run_id,testcase_node_id"}):
            falhas.setdefault(str(row["run_id"]), set()).add(_clean_case_id(row.get("testcase_node_id")))
        casos = falhas.get(run_id, set()) - {""}
        if not casos:
            return {}
        medidos: dict[str, set[str]] = {}
        for row in self._select_where_in("testcase_times", "run_id", ids, {"select": "run_id,node_id"}):
            medidos.setdefault(str(row["run_id"]), set()).add(_clean_case_id(row.get("node_id")))
        out = {}
        for caso in casos:
            # Da mais antiga para a mais nova; rodagem em que o caso nao rodou fica de fora.
            historico = [
                "falhou" if caso in falhas.get(rid, ()) else "passou"
                for rid in reversed(ids)
                if caso in falhas.get(rid, ()) or caso in medidos.get(rid, ())
            ]
            trocas = sum(1 for a, b in zip(historico, historico[1:]) if a != b)
            if historico.count("falhou") >= 2 and historico.count("passou") >= 2 and trocas >= 3:
                out[caso] = {"historico": historico, "falhou": historico.count("falhou"), "passou": historico.count("passou")}
        return out

    def _run_of_rerun(self, request: dict[str, Any]) -> str | None:
        """Rodagem gerada pela reexecucao: a primeira da VM que comecou entre o pedido e o fim do build no Jenkins."""
        inicio, fim = _parse_dt(request.get("created_at")), _parse_dt(request.get("updated_at"))
        vm = str(request.get("vm_name") or "")
        if not inicio or not fim or not vm:
            return None
        inicio -= timedelta(minutes=10)  # relogio da VM e da D01 podem diferir um pouco
        rows = self._select_all("runs", {"vm_name": f"in.({vm.lower()},{vm.upper()})", "select": "id,started_at", "order": "started_at.asc"})
        for row in rows:
            started = _parse_dt(row.get("started_at"))
            if started and inicio <= started <= fim:
                return str(row["id"])
        return None

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
        return self._select_all("recommended_actions", {"run_id": "eq." + run_id, "order": "created_at.asc,id.asc"})

    def performance(self, run_id: str) -> list[dict[str, Any]]:
        medicoes = self._select_all("testcase_times", {"run_id": "eq." + run_id})
        if not medicoes:
            # Rodagem anterior aos tempos no banco: o que veio do C:\Tempos TC.
            rows = self._select_all("run_delays", {"run_id": "eq." + run_id, "order": "delay_seconds.desc,testcase_node_id.asc"})
            return [delay_api_row(row) for row in rows]
        casos = sorted({str(m["node_id"]) for m in medicoes})
        bases = {str(row["id"]): row for row in self._select_where_in("testcase_time_bases", "id", casos)}
        historico = self._ultimas_medicoes(casos, max(tempos_casos.instante(m["measured_at"]) for m in medicoes))
        nomes = {
            str(row["node_id"]): str(row["node_name"] or "")
            for row in self._select_where_in("testcase_hierarchy", "node_id", casos, {"select": "node_id,node_name"})
        }
        return tempos_casos.linhas_performance(run_id, medicoes, bases, historico, nomes)

    def _ultimas_medicoes(self, casos: list[str], ate: Any) -> list[dict[str, Any]]:
        """Medicoes dos casos ate a rodagem (a tendencia usa as ultimas). O Postgres limita no SQL."""
        return self._select_where_in("testcase_times", "node_id", casos, {"select": "node_id,measured_at,seconds"})

    def testcase_time_history(self, node_id: str) -> dict[str, Any]:
        medicoes = self._select_all("testcase_times", {"node_id": "eq." + node_id})
        rodagens = self._select_all("runs", {"select": "id,version,started_at"})
        return tempos_casos.historico_do_caso(node_id, medicoes, self.testcase_time_base(node_id), rodagens)

    def testcase_time_base(self, node_id: str) -> dict[str, Any] | None:
        rows = self._select("testcase_time_bases", {"id": "eq." + node_id, "limit": "1"})
        return rows[0] if rows else None

    def save_testcase_time_base(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("testcase_time_bases", [row])
        return self.testcase_time_base(str(row["id"])) or row

    def import_testcase_times(self, bases: dict[str, int], medicoes: list[dict[str, Any]]) -> dict[str, int]:
        """Carga inicial: base da planilha antiga (so onde ainda nao ha base) e o historico de medicoes."""
        agora = now_iso()
        rows = tempos_casos.linhas_medicao(medicoes, None, agora)
        self._upsert("testcase_times", rows)
        casos = sorted(set(bases) | {r["node_id"] for r in rows})
        existentes = {str(r["id"]) for r in self._select_where_in("testcase_time_bases", "id", casos, {"select": "id"})}
        da_planilha = [
            {"id": caso, "base_seconds": int(segundos), "origin": "planilha", "changed_at": agora}
            for caso, segundos in bases.items() if caso not in existentes and int(segundos) > 0
        ]
        self._upsert("testcase_time_bases", da_planilha)
        novas = tempos_casos.bases_novas(rows, existentes | {r["id"] for r in da_planilha}, agora)
        self._upsert("testcase_time_bases", novas)
        return {"medicoes": len(rows), "bases_planilha": len(da_planilha), "bases_primeira_medicao": len(novas)}

    def testcase_hierarchy(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        params = {"order": "module_code.asc,node_id.asc"}
        if module_slug:
            module = self._module_by_slug(module_slug)
            if not module:
                return []
            params["module_id"] = "eq." + module["id"]
        rows = sorted(
            self._select_all("testcase_hierarchy", params),
            key=lambda row: (
                _node_sort_key(str(row.get("module_code") or "")),
                _node_sort_key(str(row.get("node_id") or "")),
            ),
        )
        return [hierarchy_api_row(row) for row in rows]

    def payload(self, run_id: str) -> dict[str, Any] | None:
        run = self.run(run_id)
        if not run:
            return None
        return {
            "modo": self.backend_name,
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

    def regravacao_requests(self, run_id: str | None = None) -> list[dict[str, Any]]:
        params = {"order": "created_at.desc", "limit": "200"}
        if run_id:
            params["run_id"] = "eq." + run_id
        return self._select("regravacao_requests", params)

    def regravacao_request(self, request_id: str) -> dict[str, Any] | None:
        rows = self._select("regravacao_requests", {"id": "eq." + request_id, "limit": "1"})
        return rows[0] if rows else None

    def save_regravacao_request(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("regravacao_requests", [row])
        return row

    def bridge_requested_regravacoes(self) -> list[dict[str, Any]]:
        return self._select("regravacao_requests", {"status": "eq.solicitado", "order": "created_at.asc"})

    def claim_regravacao_request(self, request_id: str) -> bool:
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("regravacao_requests"),
            {"status": "processando", "updated_at": now_iso()},
            query={"id": "eq." + str(request_id), "status": "eq.solicitado"},
            extra_headers={"Prefer": "return=representation"},
        )
        return bool(updated)

    def update_regravacao_request(self, request_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("regravacao_requests"),
            {**fields, "updated_at": now_iso()},
            query={"id": "eq." + str(request_id)},
            extra_headers={"Prefer": "return=representation"},
        )
        return updated[0] if updated else None

    # ---- importacao de rodagem pelo dashboard (executada pelo RegravacaoBridge)

    def import_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self._select("import_requests", {"status": "eq." + status, "order": "created_at.asc"})
        return self._select("import_requests", {"order": "created_at.desc", "limit": "50"})

    def save_import_request(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("import_requests", [row])
        return row

    def claim_import_request(self, request_id: str) -> dict[str, Any] | None:
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("import_requests"),
            {"status": "processando", "updated_at": now_iso()},
            query={"id": "eq." + str(request_id), "status": "eq.solicitado"},
            extra_headers={"Prefer": "return=representation"},
        )
        return updated[0] if updated else None

    def update_import_request(self, request_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("import_requests"),
            {**fields, "updated_at": now_iso()},
            query={"id": "eq." + str(request_id)},
            extra_headers={"Prefer": "return=representation"},
        )
        return updated[0] if updated else None

    # ---- merge entre branches do TC (executado pelo MergeBridge)

    def merge_requests(self) -> list[dict[str, Any]]:
        return self._select("merge_requests", {"order": "created_at.desc", "limit": "100"})

    def merge_request(self, request_id: str) -> dict[str, Any] | None:
        rows = self._select("merge_requests", {"id": "eq." + request_id, "limit": "1"})
        return rows[0] if rows else None

    def save_merge_request(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("merge_requests", [row])
        return row

    def merge_requests_by_status(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        return self._select("merge_requests", {"status": "in.(" + ",".join(statuses) + ")", "order": "created_at.asc"})

    def claim_merge_request(self, request_id: str) -> bool:
        # Previa e execucao sao pegas do mesmo jeito: so quem troca o status leva o pedido.
        for current, claimed in (("previa_solicitada", "previa_processando"), ("solicitado", "processando")):
            updated = self._rest_json(
                "PATCH",
                "/" + self._table("merge_requests"),
                {"status": claimed, "updated_at": now_iso()},
                query={"id": "eq." + str(request_id), "status": "eq." + current},
                extra_headers={"Prefer": "return=representation"},
            )
            if updated:
                return True
        return False

    def update_merge_request(self, request_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("merge_requests"),
            {**fields, "updated_at": now_iso()},
            query={"id": "eq." + str(request_id)},
            extra_headers={"Prefer": "return=representation"},
        )
        return updated[0] if updated else None

    def merge_branches(self) -> dict[str, Any]:
        rows = self._select("svn_branch_cache", {"id": "eq.atual", "limit": "1"})
        if not rows:
            return {"branches": [], "updated_at": None}
        return {"branches": rows[0].get("branches_json") or [], "updated_at": rows[0].get("updated_at")}

    def save_merge_branches(self, branches: list[dict[str, Any]]) -> dict[str, Any]:
        row = {"id": "atual", "branches_json": branches, "updated_at": now_iso()}
        self._upsert("svn_branch_cache", [row])
        return {"branches": branches, "updated_at": row["updated_at"]}

    def run_presets(self) -> list[dict[str, Any]]:
        return self._select("run_presets", {"order": "name.asc"})

    def run_preset(self, preset_id: str) -> dict[str, Any] | None:
        rows = self._select("run_presets", {"id": "eq." + preset_id, "limit": "1"})
        return rows[0] if rows else None

    def save_run_preset(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("run_presets", [row])
        return row

    def delete_run_preset(self, preset_id: str) -> bool:
        if not self.run_preset(preset_id):
            return False
        self._delete("run_presets", {"id": "eq." + preset_id})
        return True

    # ---- casos de teste desativados no dashboard (so visual)

    def testcase_disabled(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        params = {"order": "node_id.asc"}
        if module_slug:
            params["module_slug"] = "eq." + module_slug
        return self._select("testcase_disabled", params)

    def testcase_disabled_item(self, node_id: str) -> dict[str, Any] | None:
        rows = self._select("testcase_disabled", {"id": "eq." + node_id, "limit": "1"})
        return rows[0] if rows else None

    def save_testcase_disabled(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("testcase_disabled", [row])
        return row

    def delete_testcase_disabled(self, node_id: str) -> bool:
        if not self.testcase_disabled_item(node_id):
            return False
        self._delete("testcase_disabled", {"id": "eq." + node_id})
        return True

    def bridge_requested_rerun_requests(self) -> list[dict[str, Any]]:
        return self._select(
            "rerun_requests",
            {
                "status": "in.(requested,solicitado)",
                "order": "created_at.asc",
            },
        )

    def bridge_active_rerun_requests(self, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        statuses = statuses or [
            "enviado_jenkins",
            "na_fila",
            "rodando",
            "processando",
            "erro_monitoramento",
            "cancelando",
        ]
        return self._select(
            "rerun_requests",
            {
                "execution_status": "in.(" + ",".join(statuses) + ")",
                "order": "created_at.desc",
                "limit": "50",
            },
        )

    def bridge_cancel_requested_rerun_requests(self, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        statuses = statuses or ["cancel_requested", "cancelamento_solicitado"]
        return self._select(
            "rerun_requests",
            {
                "status": "in.(" + ",".join(statuses) + ")",
                "order": "updated_at.asc",
            },
        )

    def bridge_claim_rerun_request(self, request_id: str) -> bool:
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("rerun_requests"),
            {
                "status": "processando",
                "execution_status": "processando",
                "updated_at": now_iso(),
            },
            query={
                "id": "eq." + str(request_id),
                "status": "in.(requested,solicitado)",
            },
            extra_headers={"Prefer": "return=representation"},
        )
        return bool(updated)

    def bridge_update_rerun_request(self, request_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        body = dict(fields)
        body["updated_at"] = now_iso()
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("rerun_requests"),
            body,
            query={"id": "eq." + str(request_id)},
            extra_headers={"Prefer": "return=representation"},
        )
        return updated[0] if updated else None

    def record_rerun_request(self, request: dict[str, Any], *, requested_by: str | None = None) -> dict[str, Any]:
        request_id = str(request.get("id") or "").strip() or str(uuid.uuid4())
        if self._select("rerun_requests", {"id": "eq." + request_id, "select": "id", "limit": "1"}):
            return rerun_request_conflict(request_id)
        now = now_iso()
        row = {
            "id": request_id,
            "source_run_id": request.get("source_run_id") or request.get("fk_rodagem"),
            "vm_name": request.get("vm_name") or "",
            "version": request.get("version") or request.get("versao") or "",
            "module_id": request.get("module_id"),
            "test_cases": request.get("test_cases") or request.get("casos_teste") or "",
            "parallel": request.get("parallel") or request.get("paralelo"),
            "ct_desmarcar": request.get("ct_desmarcar"),
            "branch": request.get("branch"),
            # Usuario da sessao (definido pela API) prevalece sobre o que veio no corpo.
            "requested_by": requested_by or request.get("requested_by") or request.get("solicitado_por"),
            "request_type": request.get("request_type") or request.get("tipo_solicitacao") or "manual",
            "configuration_mode": request.get("configuration_mode") or request.get("modo_configuracao") or "api",
            "config_json": request,
            "status": new_rerun_request_status(request.get("status")),
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

    def rerun_request(self, request_id: str) -> dict[str, Any] | None:
        rows = self._select("rerun_requests", {"id": "eq." + request_id, "limit": "1"})
        return rows[0] if rows else None

    def cancel_rerun_request(self, request_id: str, request: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self._select("rerun_requests", {"id": "eq." + request_id, "limit": "1"})
        if not rows:
            return None
        current = rows[0]
        if _rerun_request_is_terminal(current):
            return {
                "error": "rerun_request_not_cancellable",
                "id": request_id,
                "status": current.get("status"),
                "execution_status": current.get("execution_status"),
                "message": "Esta rodagem Jenkins ja esta finalizada ou cancelada.",
            }
        reason = (request or {}).get("reason") or (request or {}).get("motivo") or "Cancelamento solicitado pelo dashboard."
        row = {
            "status": "cancel_requested",
            "execution_status": "cancel_requested",
            "error_message": str(reason)[:1000],
            "updated_at": now_iso(),
        }
        updated = self._rest_json(
            "PATCH",
            "/" + self._table("rerun_requests"),
            row,
            query={"id": "eq." + request_id},
            extra_headers={"Prefer": "return=representation"},
        )
        return updated[0] if updated else None

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
                "description": str(source.get("description") or "").strip(),
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
                "testcase_description": source.get("descricao") or "",
                "group_node_id": parent_id,
                "group_name": source.get("grupo") or "",
                "source_archive_name": source.get("arquivo_origem") or "",
                "source_archive_size_bytes": None,
                "occurrence_type": occurrence_type(status),
                "status": status,
                "error_message": source.get("erro_resumo") or "",
                "log_summary": source.get("erro_resumo") or "",
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

    def _import_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
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
                upload = self.storage.upload_file(local_path, source.get("storage_path") or "", mime_type)
            except Exception as exc:
                # Evidencia que nao sobe cancela a importacao (rollback): pular deixava a rodagem sem o
                # arquivo e, ao reprocessar, apagava a copia boa que ja estava gravada.
                raise RuntimeError(
                    f"Evidencia {source.get('id_evidencia')} ({source.get('nome_arquivo') or local_path}) nao foi gravada: {exc}"
                ) from exc
            row = {
                "id": source["id_evidencia"],
                "run_id": run_id,
                "occurrence_id": source.get("fk_falha"),
                "module_id": module_id,
                "file_role": role,
                "file_type": file_type(mime_type, source.get("extensao"), role),
                "original_name": source.get("nome_arquivo") or Path(local_path).name,
                "local_path": local_path,
                "storage_provider": self.storage.provider,
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
        return {
            "by_occurrence_name": by_occurrence_name,
            "ids": {str(row["id"]) for row in rows},
            "storage_paths": {str(row["storage_path"]) for row in rows if row.get("storage_path")},
        }

    def _import_report_differences(
        self,
        payload: dict[str, Any],
        occurrence_by_case: dict[str, str],
        evidence_by_occurrence_name: dict[tuple[str, str], str],
    ) -> set[str]:
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
        return {str(row["id"]) for row in rows}

    def _sync_mod_xml(self, payload: dict[str, Any]) -> None:
        """Desativados pelo mod_xml.py: entram os da lista, saem os que foram tirados dela.
        Os marcados a mao nao mudam. Sem a lista no payload (VM antiga, arquivo nao lido), nada muda."""
        lista = payload.get("casos_desmarcados_mod_xml")
        if not isinstance(lista, dict) or not isinstance(lista.get("casos"), list):
            return
        casos = [str(c) for c in lista["casos"]]
        atuais = {str(r["id"]) for r in self._select_all("testcase_disabled", {"disabled_by": "eq." + MOD_XML_AUTOR, "select": "id"})}
        sairam = sorted(atuais - set(casos))
        if sairam:
            self._delete_with_in("testcase_disabled", "id", sairam)
        existentes = {str(r["id"]) for r in self._select_where_in("testcase_disabled", "id", casos, {"select": "id"})} if casos else set()
        agora = now_iso()
        texto = mod_xml_motivo(lista.get("exceto_vms") or [])
        self._upsert("testcase_disabled", [
            {"id": c, "node_id": c, "module_slug": module_of_node(c) or None, "reason": texto,
             "disabled_by": MOD_XML_AUTOR, "disabled_at": agora}
            for c in casos if c not in existentes
        ])
        # Quem ja estava na lista ganha o texto novo (ex.: mudou a VM de excecao); marcado a mao nao muda.
        self._patch_with_in("testcase_disabled", "id", [c for c in casos if c in atuais], {"reason": texto})

    def _fim_ja_gravado(self, run_id: str) -> Any:
        rows = self._select("runs", {"id": "eq." + run_id, "select": "finished_at", "limit": "1"})
        return rows[0].get("finished_at") if rows else None

    def _import_testcase_times(self, payload: dict[str, Any]) -> None:
        """Tempos dos casos desta rodagem; caso ainda sem base ganha a primeira medicao como base."""
        rows = tempos_casos.linhas_medicao(payload.get("tempos_casos") or [], payload["rodagem"]["id_rodagem"], now_iso())
        if not rows:
            return
        self._upsert("testcase_times", rows)
        casos = sorted({r["node_id"] for r in rows})
        existentes = {str(r["id"]) for r in self._select_where_in("testcase_time_bases", "id", casos, {"select": "id"})}
        self._upsert("testcase_time_bases", tempos_casos.bases_novas(rows, existentes, now_iso()))

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

    def _modules_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(row["id"]): row for row in self._select("modules", {"select": "id,slug,name"})}

    def _with_module(
        self,
        row: dict[str, Any],
        modules_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Acrescenta slug/nome do modulo. Listas passam modules_by_id para nao consultar por linha."""
        row = dict(row)
        module_id = row.get("module_id")
        if modules_by_id is None:
            found = self._select("modules", {"id": "eq." + str(module_id), "limit": "1"})
            module = found[0] if found else None
        else:
            module = modules_by_id.get(str(module_id))
        if module:
            row["module_slug"] = module["slug"]
            row["module_name"] = module["name"]
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

    def _select_all(
        self,
        table: str,
        params: dict[str, str] | None = None,
        *,
        chunk_size: int = 1000,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = dict(params or {})
            query["limit"] = str(chunk_size)
            query["offset"] = str(offset)
            rows = self._select(table, query)
            if not rows:
                break
            out.extend(rows)
            if len(rows) < chunk_size:
                break
            offset += chunk_size
        return out

    def _select_where_in(
        self,
        table: str,
        column: str,
        values: list[str],
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in chunks(values, 250):
            if not chunk:
                continue
            query = dict(params or {})
            query[column] = "in.(" + ",".join(chunk) + ")"
            out.extend(self._select_all(table, query))
        return out

    def _delete(self, table: str, params: dict[str, str]) -> None:
        if self.dry_run:
            return
        self._rest_json(
            "DELETE",
            "/" + self._table(table),
            query=params,
            extra_headers={"Prefer": "return=minimal"},
        )

    def _delete_with_in(self, table: str, column: str, values: list[str]) -> None:
        for chunk in chunks(values, 250):
            if chunk:
                self._delete(table, {column: "in.(" + ",".join(chunk) + ")"})

    def _patch_with_in(self, table: str, column: str, values: list[str], body: dict[str, Any]) -> None:
        if self.dry_run:
            return
        for chunk in chunks(values, 250):
            if not chunk:
                continue
            self._rest_json(
                "PATCH",
                "/" + self._table(table),
                body,
                query={column: "in.(" + ",".join(chunk) + ")"},
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
        raise NotImplementedError(f"{method} {path} nao implementado em {self.__class__.__name__}")


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
        "AGENT_TC_STORAGE",
        "AGENT_TC_STORAGE_BUCKET",
        "AGENT_TC_STORAGE_ROOT",
        "AGENT_TC_PUBLIC_BASE_URL",
        "STORAGE_PROVIDER",
        "LOCAL_STORAGE_ROOT",
        "LOCAL_STORAGE_PUBLIC_BASE_URL",
        "POSTGRES_DSN",
        "DATABASE_URL",
        "POSTGRES_SCHEMA",
        "POSTGRES_TABLE_PREFIX",
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


def _rerun_request_is_terminal(row: dict[str, Any]) -> bool:
    terminal = {
        "cancelado",
        "finalizado",
        "finalizado_sucesso",
        "finalizado_falha",
        "finalizado_ocorrencias",
        "erro",
        "erro_envio",
    }
    status = str(row.get("status") or "").lower()
    execution_status = str(row.get("execution_status") or "").lower()
    return status in terminal or execution_status in terminal


def _clean_case_id(value: Any) -> str:
    return str(value or "").strip().strip("[]").strip()


def _case_ids(test_cases: Any) -> list[str]:
    """"[9.1.4.1.3], [9.4.1.1.1]" -> ["9.1.4.1.3", "9.4.1.1.1"] (sem repetir)."""
    return list(dict.fromkeys(c for c in (_clean_case_id(item) for item in str(test_cases or "").split(",")) if c))


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

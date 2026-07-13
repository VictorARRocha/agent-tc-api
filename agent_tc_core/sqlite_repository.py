from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api_repository import OFFICIAL_MODULES, SLUG_BY_MODULE_ID, slugify
from .constants import MODULE_CODES_BY_ID
from .utils import ascii_lower


ROOT = Path(__file__).resolve().parents[1]
SQLITE_SCHEMA = ROOT / "database" / "sqlite" / "001_initial.sql"


MODULE_ID_BY_CODE = {
    code: module_id
    for module_id, codes in MODULE_CODES_BY_ID.items()
    for code in codes
}


class SQLiteRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.executescript(SQLITE_SCHEMA.read_text(encoding="utf-8"))
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN total_executed INTEGER")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            self.seed_modules(conn)
        finally:
            conn.close()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def seed_modules(self, conn: sqlite3.Connection | None = None) -> None:
        close = conn is None
        conn = conn or self.connect()
        try:
            now = now_iso()
            for module in OFFICIAL_MODULES:
                row = {
                    "id": module["id_modulo"],
                    "slug": module["slug"],
                    "name": module["nome"],
                    "system": module_system(module["id_modulo"]),
                    "codes_json": json.dumps(
                        list(MODULE_CODES_BY_ID.get(module["id_modulo"], ())),
                        ensure_ascii=False,
                    ),
                    "active": 1,
                    "sort_order": module["ordem"],
                    "created_at": now,
                    "updated_at": now,
                }
                upsert(conn, "modules", row, "id")
            conn.commit()
        finally:
            if close:
                conn.close()

    def import_payload_file(self, payload_path: str | Path) -> dict[str, Any]:
        payload_path = Path(payload_path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        return self.import_payload(payload, source=str(payload_path))

    def import_payload(self, payload: dict[str, Any], source: str = "payload") -> dict[str, Any]:
        self.initialize()
        run = payload["rodagem"]
        module = payload["modulo"]
        module_id = str(module["id_modulo"])
        run_id = str(run["id_rodagem"])
        now = now_iso()
        payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        batch_id = "ing_" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{run_id}|{hashlib.sha256(payload_bytes).hexdigest()}",
        ).hex

        run_row = {
            "id": run_id,
            "system": run.get("sistema") or module_system(module_id),
            "version": run.get("versao") or "",
            "vm_name": str(run.get("vm_name") or "").lower(),
            "module_id": module_id,
            "started_at": run.get("data_inicio") or now,
            "finished_at": None,
            "logs_path": run.get("caminho_logs") or "",
            "status": "processing",
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
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "status": "processing",
            "summary_json": json.dumps(import_summary(payload), ensure_ascii=False),
            "created_at": now,
            "completed_at": None,
        }
        conn = self.connect()
        try:
            self.seed_modules(conn)
            upsert(conn, "runs", run_row, "id")
            upsert(conn, "ingestion_batches", batch_row, "id")

            hierarchy_rows = self._import_hierarchy(conn, payload)
            occurrence_by_case = self._import_occurrences(conn, payload, hierarchy_rows)
            group_ids = self._import_ai_groups(conn, payload)
            self._import_group_links(conn, payload, group_ids)
            evidence_by_occurrence_name = self._import_evidence(conn, payload)
            differences_written = self._import_report_differences(
                conn,
                payload,
                occurrence_by_case,
                evidence_by_occurrence_name,
            )
            expected = {
                "occurrences": _unique_count(payload.get("falhas") or [], "id_falha"),
                "evidence_files": _unique_count(payload.get("evidencias") or [], "id_evidencia"),
                "report_differences": differences_written,
                "ai_groups": len(group_ids),
            }
            actual = {
                "occurrences": conn.execute("SELECT COUNT(*) FROM occurrences WHERE run_id = ?", (run_id,)).fetchone()[0],
                "evidence_files": conn.execute("SELECT COUNT(*) FROM evidence_files WHERE run_id = ?", (run_id,)).fetchone()[0],
                "report_differences": conn.execute("SELECT COUNT(*) FROM report_differences WHERE run_id = ?", (run_id,)).fetchone()[0],
                "ai_groups": conn.execute("SELECT COUNT(*) FROM ai_groups WHERE run_id = ? AND ai_analysis_job_id IS NULL", (run_id,)).fetchone()[0],
            }
            mismatches = {key: {"expected": expected[key], "actual": actual[key]} for key in expected if expected[key] != actual[key]}
            if mismatches:
                raise RuntimeError("Verificacao pos-importacao falhou: " + json.dumps(mismatches, ensure_ascii=False, sort_keys=True))
            verification = {"ok": True, "expected": expected, "actual": actual}
            run_row.update({"status": "analyzed", "updated_at": now_iso()})
            batch_row.update(
                {
                    "status": "completed",
                    "summary_json": json.dumps(
                        {
                            **import_summary(payload),
                            "erros_processamento": payload.get("erros_processamento") or [],
                            "verification": verification,
                        },
                        ensure_ascii=False,
                    ),
                    "completed_at": now_iso(),
                }
            )
            upsert(conn, "runs", run_row, "id")
            upsert(conn, "ingestion_batches", batch_row, "id")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            run_row.update({"status": "import_failed", "updated_at": now_iso()})
            batch_row.update(
                {
                    "status": "failed",
                    "summary_json": json.dumps(
                        {**import_summary(payload), "error": str(exc)[:2000]},
                        ensure_ascii=False,
                    ),
                    "completed_at": now_iso(),
                }
            )
            upsert(conn, "runs", run_row, "id")
            upsert(conn, "ingestion_batches", batch_row, "id")
            conn.commit()
            raise
        finally:
            conn.close()

        return {
            "db_path": str(self.db_path),
            "run_id": run_id,
            "modules": len(OFFICIAL_MODULES),
            "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
            "occurrences": len(payload.get("falhas") or []),
            "evidence_files": len(payload.get("evidencias") or []),
            "report_differences": len(payload.get("diferencas_relatorio") or []),
            "ai_groups": len(payload.get("agrupamentos_shadow") or payload.get("agrupamentos") or []),
            "verification": verification,
        }

    def modules(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM modules ORDER BY sort_order, name").fetchall()
        return [module_api_row(row) for row in rows]

    def runs(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT r.*, m.slug AS module_slug, m.name AS module_name
            FROM runs r
            JOIN modules m ON m.id = r.module_id
        """
        params: list[Any] = []
        if module_slug:
            sql += " WHERE m.slug = ?"
            params.append(module_slug)
        sql += " ORDER BY r.started_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [run_api_row(row) for row in rows]

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*, m.slug AS module_slug, m.name AS module_name
                FROM runs r
                JOIN modules m ON m.id = r.module_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
        return run_api_row(row) if row else None

    def failures(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT o.*,
                       (
                         SELECT ago.group_id
                         FROM ai_group_occurrences ago
                         WHERE ago.occurrence_id = o.id
                         ORDER BY ago.group_id
                         LIMIT 1
                       ) AS group_id
                FROM occurrences o
                WHERE o.run_id = ?
                ORDER BY o.testcase_node_id
                """,
                (run_id,),
            ).fetchall()
        return [occurrence_api_row(row) for row in rows]

    def evidences(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence_files WHERE run_id = ? ORDER BY occurrence_id, file_role, original_name",
                (run_id,),
            ).fetchall()
        return [evidence_api_row(row) for row in rows]

    def evidences_by_failure(self, failure_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence_files WHERE occurrence_id = ? ORDER BY file_role, original_name",
                (failure_id,),
            ).fetchall()
        return [evidence_api_row(row) for row in rows]

    def report_differences(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM report_differences WHERE run_id = ? ORDER BY testcase_node_id, base_file_name",
                (run_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["summary_json"] = json.loads(item.get("summary_json") or "{}")
            except json.JSONDecodeError:
                item["summary_json"] = {}
            out.append(item)
        return out

    def ai_grouping_status(self, run_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            latest = conn.execute(
                "SELECT * FROM ai_analysis_jobs WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            groups_count = conn.execute(
                "SELECT COUNT(*) FROM ai_groups WHERE run_id = ? AND ai_analysis_job_id IS NOT NULL",
                (run_id,),
            ).fetchone()[0]
        return {
            "run_id": run_id,
            "status": latest["status"] if latest else "not_requested",
            "job_id": latest["id"] if latest else None,
            "model": latest["model"] if latest else None,
            "completed_at": latest["completed_at"] if latest else None,
            "error_message": latest["error_message"] if latest else None,
            "groups_count": groups_count,
            "grouped": bool(groups_count) and bool(latest and latest["status"] == "completed"),
        }

    def ai_grouping_debug(self, run_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            latest = conn.execute(
                "SELECT * FROM ai_analysis_jobs WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not latest:
            return {"run_id": run_id, "job": None}
        request = json_value(latest["request_json"], {})
        response = json_value(latest["response_json"], {})
        return {
            "run_id": run_id,
            "job": {
                "id": latest["id"],
                "provider": latest["provider"],
                "model": latest["model"],
                "status": latest["status"],
                "error_message": latest["error_message"],
                "created_at": latest["created_at"],
                "completed_at": latest["completed_at"],
            },
            "request_summary": _ai_request_summary(request),
            "response_debug": _ai_response_debug(response),
        }

    def save_ai_job(self, row: dict[str, Any]) -> None:
        stored = dict(row)
        stored["request_json"] = json.dumps(stored.get("request_json") or {}, ensure_ascii=False)
        stored["response_json"] = json.dumps(stored.get("response_json") or {}, ensure_ascii=False)
        with self.connect() as conn:
            upsert(conn, "ai_analysis_jobs", stored, "id")
            conn.commit()

    def persist_ai_grouping(self, run_id: str, job_id: str, rows: dict[str, list[dict[str, Any]]]) -> None:
        groups = rows.get("groups") or []
        with self.connect() as conn:
            for row in groups:
                upsert(conn, "ai_groups", row, "id")
            for row in rows.get("links") or []:
                conn.execute(
                    "INSERT OR IGNORE INTO ai_group_occurrences(group_id, occurrence_id, created_at) VALUES (?, ?, ?)",
                    (row["group_id"], row["occurrence_id"], row["created_at"]),
                )
            for row in rows.get("actions") or []:
                upsert(conn, "recommended_actions", row, "id")
            conn.execute(
                "DELETE FROM ai_groups WHERE run_id = ? AND ai_analysis_job_id IS NULL",
                (run_id,),
            )
            conn.execute(
                "UPDATE runs SET total_ai_groups = ?, updated_at = ? WHERE id = ?",
                (len(groups), now_iso(), run_id),
            )
            conn.commit()

    def groups(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT g.*, COUNT(ago.occurrence_id) AS quantity
                FROM ai_groups g
                LEFT JOIN ai_group_occurrences ago ON ago.group_id = g.id
                WHERE g.run_id = ?
                GROUP BY g.id
                ORDER BY g.created_at, g.id
                """,
                (run_id,),
            ).fetchall()
        return [group_api_row(row) for row in rows]

    def group_links(self, run_id: str) -> dict[str, list[str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ago.group_id, ago.occurrence_id
                FROM ai_group_occurrences ago
                JOIN ai_groups g ON g.id = ago.group_id
                WHERE g.run_id = ?
                ORDER BY ago.group_id, ago.occurrence_id
                """,
                (run_id,),
            ).fetchall()
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row["group_id"], []).append(row["occurrence_id"])
        return out

    def reexecutable_cases(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT o.id AS id_falha,
                       o.testcase_node_id AS id_caso_teste,
                       o.testcase_name AS nome_mds,
                       o.group_name AS grupo,
                       o.source_archive_name AS arquivo_origem,
                       g.id AS cluster_id,
                       g.status AS cluster_status,
                       g.title AS cluster_titulo,
                       g.technical_signature AS cluster_assinatura
                FROM occurrences o
                LEFT JOIN ai_group_occurrences ago ON ago.occurrence_id = o.id
                LEFT JOIN ai_groups g ON g.id = ago.group_id
                WHERE o.run_id = ?
                ORDER BY o.testcase_node_id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "tipo_ocorrencia": reexecutable_type(row["cluster_status"]),
            }
            for row in rows
        ]

    def next_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommended_actions WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def performance(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_delays WHERE run_id = ? ORDER BY delay_seconds DESC, testcase_node_id",
                (run_id,),
            ).fetchall()
        return [delay_api_row(row) for row in rows]

    def testcase_hierarchy(self, module_slug: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT h.*, m.slug AS module_slug
            FROM testcase_hierarchy h
            JOIN modules m ON m.id = h.module_id
        """
        params: list[Any] = []
        if module_slug:
            sql += " WHERE m.slug = ?"
            params.append(module_slug)
        sql += " ORDER BY h.module_code, h.node_id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [hierarchy_api_row(row) for row in rows]

    def payload(self, run_id: str) -> dict[str, Any] | None:
        run = self.run(run_id)
        if not run:
            return None
        return {
            "modo": "sqlite",
            "rodagem": run,
            "falhas": self.failures(run_id),
            "evidencias": self.evidences(run_id),
            "agrupamentos": self.groups(run_id),
            "testcase_hierarchy": self.testcase_hierarchy(run.get("modulo_slug")),
            "proximos_passos": self.next_steps(run_id),
            "atrasos_rodagem": self.performance(run_id),
        }

    def rerun_requests(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM rerun_requests ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def record_rerun_request(self, request: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
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
            "config_json": json.dumps(request, ensure_ascii=False),
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
        with self.connect() as conn:
            upsert(conn, "rerun_requests", row, "id")
            conn.commit()
        return row

    def _import_hierarchy(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        now = now_iso()
        out = {}
        for source in payload.get("testcase_hierarchy") or []:
            node_id = str(source.get("node_id") or "")
            module_code = str(source.get("modulo_codigo") or node_id.split(".", 1)[0])
            module_id = MODULE_ID_BY_CODE.get(module_code, "mod_geral")
            node_type = translate_node_type(source.get("node_type"))
            row = {
                "id": str(source.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, f"{source.get('sistema') or 'Unico'}|{node_id}")),
                "system": source.get("sistema") or module_system(module_id),
                "module_id": module_id,
                "module_code": module_code,
                "module_name": source.get("modulo_nome") or module_name(module_id),
                "node_id": node_id,
                "parent_node_id": source.get("parent_node_id"),
                "node_name": source.get("node_name") or "",
                "node_type": node_type,
                "full_path_ids_json": json.dumps(source.get("full_path_ids") or [], ensure_ascii=False),
                "full_path_names_json": json.dumps(source.get("full_path_names") or [], ensure_ascii=False),
                "full_path_label": source.get("full_path_label") or "",
                "script_name": source.get("script_name") or "",
                "procedure_name": source.get("procedure_name") or "",
                "mds_path": source.get("mds_path") or "",
                "created_at": source.get("created_at") or now,
                "updated_at": now,
            }
            upsert(conn, "testcase_hierarchy", row, "id")
            out[node_id] = row
        return out

    def _import_occurrences(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        hierarchy_rows: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
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
            upsert(conn, "occurrences", row, "id")
            occurrence_by_case[case_id] = row["id"]
        return occurrence_by_case

    def _import_ai_groups(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> set[str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
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
            upsert(conn, "ai_groups", row, "id")
            group_ids.add(row["id"])
        return group_ids

    def _import_group_links(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        group_ids: set[str],
    ) -> None:
        now = now_iso()
        for group in payload.get("agrupamentos") or payload.get("agrupamentos_shadow") or []:
            group_id = group.get("id_cluster")
            if group_id not in group_ids:
                continue
            for occurrence_id in group.get("falhas") or []:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ai_group_occurrences(group_id, occurrence_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (group_id, occurrence_id, now),
                )

    def _import_evidence(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> dict[tuple[str, str], str]:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
        by_occurrence_name = {}
        for source in payload.get("evidencias") or []:
            role = file_role(source.get("tipo_arquivo"), source.get("storage_path"))
            local_path = source.get("caminho_evidencia") or ""
            sha = sha256_file(local_path)
            upload_status = "uploaded" if source.get("public_url") or source.get("signed_url") else "pending"
            provider = "supabase" if source.get("bucket") else "local"
            row = {
                "id": source["id_evidencia"],
                "run_id": run_id,
                "occurrence_id": source.get("fk_falha"),
                "module_id": module_id,
                "file_role": role,
                "file_type": file_type(source.get("mime_type"), source.get("extensao"), role),
                "original_name": source.get("nome_arquivo") or Path(local_path).name,
                "local_path": local_path,
                "storage_provider": provider,
                "storage_bucket": source.get("bucket") or "",
                "storage_path": source.get("storage_path") or "",
                "public_url": source.get("public_url") or "",
                "signed_url": source.get("signed_url") or "",
                "signed_url_expires_at": source.get("url_expira_em"),
                "mime_type": source.get("mime_type") or "",
                "extension": clean_ext(source.get("extensao")),
                "size_bytes": source.get("tamanho_bytes"),
                "sha256": sha,
                "upload_status": upload_status,
                "created_at": source.get("created_at") or now,
                "updated_at": now,
            }
            upsert(conn, "evidence_files", row, "id")
            if row["occurrence_id"] and row["original_name"]:
                by_occurrence_name[(row["occurrence_id"], row["original_name"].lower())] = row["id"]
        return by_occurrence_name

    def _import_report_differences(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        occurrence_by_case: dict[str, str],
        evidence_by_occurrence_name: dict[tuple[str, str], str],
    ) -> int:
        run_id = payload["rodagem"]["id_rodagem"]
        module_id = payload["modulo"]["id_modulo"]
        now = now_iso()
        written = 0
        for source in payload.get("diferencas_relatorio") or []:
            case_id = str(source.get("id_caso_teste") or "")
            occurrence_id = source.get("fk_falha") or occurrence_by_case.get(case_id)
            if not occurrence_id:
                continue
            summary = source.get("resumo_diferenca") or {}
            base_name = source.get("nome_arquivo_base") or ""
            current_name = source.get("nome_arquivo_atual") or ""
            row = {
                "id": source["id_diferenca"],
                "run_id": run_id,
                "occurrence_id": occurrence_id,
                "module_id": module_id,
                "testcase_node_id": case_id,
                "base_evidence_id": evidence_by_occurrence_name.get((occurrence_id, base_name.lower())),
                "current_evidence_id": evidence_by_occurrence_name.get((occurrence_id, current_name.lower())),
                "base_file_name": base_name,
                "current_file_name": current_name,
                "base_lines": summary.get("linhas_base"),
                "current_lines": summary.get("linhas_atual"),
                "changed_lines_estimate": summary.get("linhas_alteradas_estimadas"),
                "summary_json": json.dumps(summary, ensure_ascii=False),
                "created_at": source.get("created_at") or now,
            }
            upsert(conn, "report_differences", row, "id")
            written += 1
        return written


def upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], pk: str) -> None:
    columns = list(row)
    placeholders = ", ".join(["?"] * len(columns))
    update_columns = [column for column in columns if column != pk]
    updates = ", ".join([f"{column}=excluded.{column}" for column in update_columns])
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[column] for column in columns])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def module_system(module_id: str) -> str:
    if module_id == "mod_suprema":
        return "Suprema"
    if module_id == "mod_practice":
        return "Practice"
    return "Unico"


def module_name(module_id: str) -> str:
    for module in OFFICIAL_MODULES:
        if module["id_modulo"] == module_id:
            return module["nome"]
    return "Geral"


def module_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "id_modulo": data["id"],
        "slug": data["slug"],
        "nome": data["name"],
        "name": data["name"],
        "system": data["system"],
        "codes": json_value(data["codes_json"], []),
        "active": bool(data["active"]),
        "ordem": data["sort_order"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


def run_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    module_slug = data.get("module_slug") or SLUG_BY_MODULE_ID.get(data["module_id"]) or ""
    total_executed = data.get("total_executed")
    total_analisados = total_executed if total_executed is not None else data["total_occurrences"]
    return {
        **data,
        "id_rodagem": data["id"],
        "sistema": data["system"],
        "versao": data["version"],
        "data_inicio": data["started_at"],
        "caminho_logs": data["logs_path"],
        "total_falhas": data["total_occurrences"],
        "total_clusters": data["total_ai_groups"],
        "fk_modulo": data["module_id"],
        "vm_name": data["vm_name"],
        "modulo": {"id_modulo": data["module_id"], "nome": data["module_name"]},
        "modulo_id": None,
        "modulo_slug": module_slug,
        "ambiente": None,
        "origem": None,
        "ferramenta_analise": "python",
        "data_inicio_rodagem": data["started_at"],
        "data_fim_rodagem": data["finished_at"],
        "data_analise": data["started_at"] or data["created_at"],
        "branch": None,
        "versao_sistema": data["version"],
        "maquina": data["vm_name"],
        "responsavel": None,
        "pasta_origem": data["logs_path"],
        "status_geral": data["status"],
        "status_label": data["status"],
        "status_cor": None,
        "score_saude": None,
        "diagnostico_curto": None,
        "diagnostico_detalhado": None,
        "conclusao_geral": None,
        "total_compactados": data["total_archives"],
        "total_analisados": total_analisados,
        "total_automacao": 0,
        "total_massa_dados": 0,
        "total_ambiente": 0,
        "total_possivel_funcional": 0,
        "total_inconclusivo": 0,
        "total_alta": 0,
        "total_media": 0,
        "total_baixa": 0,
        "json_original": None,
        "falhas_count": data["total_occurrences"],
    }


def occurrence_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    module_slug = SLUG_BY_MODULE_ID.get(data["module_id"]) or ""
    description = data["log_summary"] or data["error_message"] or data["testcase_name"]
    return {
        **data,
        "id_falha": data["id"],
        "fk_cluster": data.get("group_id"),
        "fk_modulo": data["module_id"],
        "id_caso_teste": data["testcase_node_id"],
        "nome_mds": data["testcase_name"],
        "grupo": data["group_name"],
        "descricao": description,
        "arquivo_origem": data["source_archive_name"],
        "tipo_ocorrencia": data["occurrence_type"],
        "tipo_detectado_python": data["status"],
        "rodagem_id": data["run_id"],
        "modulo_slug": module_slug,
        "ordem_prioridade": None,
        "arquivo_zip": data["source_archive_name"],
        "arquivo_txt": None,
        "arquivo_print": None,
        "caso_identificado": bool(data["testcase_node_id"]),
        "caso_teste_provavel": data["testcase_name"],
        "subgrupo": None,
        "rotina_funcional": data["group_name"],
        "descricao_caso": description,
        "confianca_associacao": None,
        "erro_titulo": data["testcase_name"],
        "erro_principal": data["error_message"] or description,
        "mensagem_principal": data["error_message"] or description,
        "trecho_relevante": None,
        "call_stack_resumido": data["error_message"],
        "tipo_tecnico": data["occurrence_type"],
        "formulario_ou_tela": None,
        "componente": None,
        "classificacao": data["occurrence_type"],
        "classificacao_label": data["status"],
        "severidade": None,
        "confianca": None,
        "status_analise": data["status"],
        "cor": None,
        "fato_observado": description,
        "hipotese_principal": data["technical_signature"],
        "analise_tecnica": data["technical_signature"],
        "analise_funcional": None,
        "impacto_possivel": None,
        "primeira_acao_recomendada": None,
        "informacoes_faltantes": None,
        "tags": None,
    }


def evidence_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    module_slug = SLUG_BY_MODULE_ID.get(data["module_id"]) or ""
    is_image = (data["mime_type"] or "").lower().startswith("image/") or data["extension"] in {
        "png",
        "jpg",
        "jpeg",
        "webp",
        "bmp",
        "gif",
    }
    return {
        **data,
        "id_evidencia": data["id"],
        "fk_falha": data["occurrence_id"],
        "falha_id": data["occurrence_id"],
        "tipo_arquivo": data["file_role"],
        "tipo": data["file_role"],
        "nome_arquivo": data["original_name"],
        "bucket": data["storage_bucket"],
        "extensao": data["extension"],
        "tamanho_bytes": data["size_bytes"],
        "caminho_evidencia": data["local_path"],
        "rodagem_id": data["run_id"],
        "modulo_slug": module_slug,
        "conteudo_texto": None,
        "print_util": is_image,
        "imagem_descricao": None,
        "url_expira_em": data["signed_url_expires_at"],
    }


def group_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    module_slug = SLUG_BY_MODULE_ID.get(data["module_id"]) or ""
    is_shadow_group = data.get("ai_analysis_job_id") is None
    grouping_source = "shadow" if is_shadow_group else "ai"
    return {
        **data,
        "is_shadow_group": is_shadow_group,
        "grouping_source": grouping_source,
        "agrupamento_origem": grouping_source,
        "agrupamento_ia": not is_shadow_group,
        "id_cluster": data["id"],
        "fk_rodagem": data["run_id"],
        "titulo_causa": data["title"],
        "assinatura_tecnica": data["technical_signature"],
        "raio_x_negocio": data["justification"],
        "quantidade": data["quantity"],
        "rodagem_id": data["run_id"],
        "modulo_slug": module_slug,
        "tipo": data["status"],
        "titulo": data["title"],
        "descricao": data["technical_signature"],
        "classificacao_predominante": data["classification"],
        "severidade_predominante": None,
        "arquivos_relacionados": None,
        "acao_recomendada": data["justification"],
    }


def hierarchy_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    return {
        **data,
        "modulo_codigo": data["module_code"],
        "modulo_nome": data["module_name"],
        "node_type": "grupo" if data["node_type"] == "group" else "caso",
        "full_path_ids": json_value(data["full_path_ids_json"], []),
        "full_path_names": json_value(data["full_path_names_json"], []),
    }


def delay_api_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    return {
        **data,
        "id_atraso": data["id"],
        "fk_rodagem": data["run_id"],
        "codigo_teste": data["testcase_node_id"],
        "nome_teste": data["testcase_name"],
        "tempo_padrao": seconds_to_hms(data["expected_seconds"]),
        "tempo_atual": seconds_to_hms(data["actual_seconds"]),
        "delay_detectado": seconds_to_hms(data["delay_seconds"]),
    }


def import_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "falhas": len(payload.get("falhas") or []),
        "evidencias": len(payload.get("evidencias") or []),
        "diferencas": len(payload.get("diferencas_relatorio") or []),
        "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
        "erros_processamento": len(payload.get("erros_processamento") or []),
    }


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


def occurrence_type(status: object) -> str:
    text = ascii_lower(status)
    has_break = "quebra" in text
    has_diff = "diferenca" in text or "comparacao" in text
    if has_break and has_diff:
        return "test_break_with_difference"
    if has_diff:
        return "report_difference"
    if has_break:
        return "test_break"
    if "sem classificacao" in text or "incompleta" in text:
        return "incomplete_evidence"
    return "unknown"


def reexecutable_type(status: object) -> str:
    text = ascii_lower(status)
    has_break = "quebra" in text
    has_diff = "diferenca" in text or "comparacao" in text
    if has_break and has_diff:
        return "quebra_diferenca"
    if has_diff:
        return "diferenca"
    if has_break:
        return "quebra"
    return "outro"


def file_role(tipo: object, storage_path: object) -> str:
    text = ascii_lower(f"{tipo or ''} {storage_path or ''}")
    if "original" in text or text.endswith(".rar") or text.endswith(".zip"):
        return "archive_original"
    if "comparacao/base" in text or "comparacao_base" in text:
        return "comparison_base"
    if "comparacao/atual" in text or "comparacao_atual" in text:
        return "comparison_current"
    if "print" in text:
        return "screen_print"
    if "imagem" in text or "image" in text:
        return "error_image"
    if "texto" in text or "informacaoerro" in text:
        return "error_text"
    return "other"


def file_type(mime: object, extension: object, role: str) -> str:
    mime_text = str(mime or "").lower()
    ext = clean_ext(extension)
    if role.startswith("comparison"):
        return "comparison"
    if mime_text.startswith("image/") or ext in {"png", "jpg", "jpeg", "bmp", "gif", "webp"}:
        return "image"
    if mime_text.startswith("text/") or ext in {"txt", "log", "csv", "xml", "html", "htm"}:
        return "text"
    if ext in {"rar", "zip", "7z"}:
        return "archive"
    return "other"


def clean_ext(value: object) -> str:
    return str(value or "").lower().lstrip(".")


def translate_node_type(value: object) -> str:
    text = ascii_lower(value)
    if text in {"grupo", "group", "folder"}:
        return "group"
    return "case"


def parent_case_id(case_id: str) -> str | None:
    if "." not in case_id:
        return None
    return case_id.rsplit(".", 1)[0]


def sha256_file(path_value: object) -> str | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seconds_to_hms(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    neg = seconds < 0
    seconds = abs(int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return ("-" if neg else "") + f"{h:02d}:{m:02d}:{s:02d}"


def json_value(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    return json.loads(value)

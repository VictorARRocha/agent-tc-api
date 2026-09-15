from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .storage import DEFAULT_BUCKET, storage_from_env
from .supabase_repository import (
    DEFAULT_ENV,
    DEFAULT_SCHEMA,
    DEFAULT_TABLE_PREFIX,
    SupabaseRepository,
    _deduplicate_rows,
    chunks,
    parse_bool,
    read_env,
)


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_MIGRATIONS = ROOT / "database" / "postgres"
POSTGRES_SCHEMA = POSTGRES_MIGRATIONS / "001_initial.sql"


class PostgresRepository(SupabaseRepository):
    """Postgres local usando o mesmo contrato de dados do SupabaseRepository."""

    def __init__(
        self,
        env_path: str | Path | None = None,
        *,
        dsn: str | None = None,
        schema: str | None = None,
        table_prefix: str | None = None,
        bucket: str | None = None,
        dry_run: bool = False,
    ):
        env = read_env(env_path or DEFAULT_ENV)
        self.env_path = Path(env_path or DEFAULT_ENV)
        self.dsn = dsn or env.get("POSTGRES_DSN") or env.get("DATABASE_URL") or ""
        self.schema = schema or env.get("POSTGRES_SCHEMA") or env.get("SUPABASE_SCHEMA") or DEFAULT_SCHEMA
        self.table_prefix = table_prefix if table_prefix is not None else env.get("POSTGRES_TABLE_PREFIX", env.get("SUPABASE_TABLE_PREFIX", DEFAULT_TABLE_PREFIX))
        self.storage_public = parse_bool(env.get("SUPABASE_BUCKET_PUBLIC"), default=True)
        self.dry_run = dry_run
        self.storage = storage_from_env(
            env,
            supabase_url=(env.get("SUPABASE_URL") or "").rstrip("/"),
            supabase_service_key=env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SECRET_KEY") or "",
            default_bucket=bucket or env.get("AGENT_TC_STORAGE_BUCKET") or env.get("SUPABASE_BUCKET") or DEFAULT_BUCKET,
            storage_public=self.storage_public,
            dry_run=dry_run,
            default_provider="local",
        )
        self.bucket = self.storage.bucket
        self.plan: dict[str, Any] = {
            "dry_run": dry_run,
            "backend": "postgres",
            "schema": self.schema,
            "table_prefix": self.table_prefix,
            "bucket": self.bucket,
            "storage_provider": self.storage.provider,
            "upserts": {},
            "uploads": 0,
            "skipped_evidence": 0,
            "upload_errors": [],
            "deduplicated_rows": {},
        }
        if not self.dsn:
            raise ValueError("POSTGRES_DSN ou DATABASE_URL nao configurado para --backend postgres")

    def initialize(self) -> None:
        if self.dry_run:
            self.plan["initialize"] = "skipped_dry_run"
            return
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                for sql in self._schema_sqls():
                    cursor.execute(sql)
            conn.commit()
        finally:
            conn.close()
        self.storage.initialize()
        self.seed_modules()

    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "Dependencia ausente para Postgres local. Instale com: py -3 -m pip install \"psycopg[binary]>=3.2,<4\""
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def import_payload(self, payload: dict[str, Any], source: str = "payload") -> dict[str, Any]:
        self.initialize()
        result = super().import_payload(payload, source)
        result["backend"] = "postgres"
        result["schema"] = self.schema
        result["table_prefix"] = self.table_prefix
        return result

    def payload(self, run_id: str) -> dict[str, Any] | None:
        payload = super().payload(run_id)
        if payload:
            payload["modo"] = "postgres"
        return payload

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

        conflict_columns = [column.strip() for column in conflict.split(",") if column.strip()]
        for chunk in chunks(rows, 250):
            columns = sorted({key for row in chunk for key in row})
            placeholders = ", ".join(["%s"] * len(columns))
            column_sql = ", ".join(_ident(column) for column in columns)
            update_columns = [column for column in columns if column not in conflict_columns]
            if update_columns:
                updates = ", ".join(f"{_ident(column)} = EXCLUDED.{_ident(column)}" for column in update_columns)
                conflict_sql = f"DO UPDATE SET {updates}"
            else:
                conflict_sql = "DO NOTHING"
            sql = (
                f"INSERT INTO {self._qualified_table(table)} ({column_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({', '.join(_ident(column) for column in conflict_columns)}) {conflict_sql}"
            )
            values = [tuple(_pg_value(row.get(column)) for column in columns) for row in chunk]
            conn = self.connect()
            try:
                with conn.cursor() as cursor:
                    cursor.executemany(sql, values)
                conn.commit()
            finally:
                conn.close()

    def _select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        select_columns = _select_columns(params.get("select"))
        sql = f"SELECT {select_columns} FROM {self._qualified_table(table)}"
        where_sql, values = _where_from_params(params)
        if where_sql:
            sql += " WHERE " + where_sql
        order_sql = _order_sql(params.get("order"))
        if order_sql:
            sql += " ORDER BY " + order_sql
        if params.get("limit"):
            sql += " LIMIT %s"
            values.append(int(params["limit"]))
        if params.get("offset"):
            sql += " OFFSET %s"
            values.append(int(params["offset"]))

        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, values)
                return [_json_ready_row(dict(row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _select_all(
        self,
        table: str,
        params: dict[str, str] | None = None,
        *,
        chunk_size: int = 1000,
    ) -> list[dict[str, Any]]:
        return super()._select_all(table, params, chunk_size=chunk_size)

    def _delete(self, table: str, params: dict[str, str]) -> None:
        if self.dry_run:
            return
        where_sql, values = _where_from_params(params)
        if not where_sql:
            raise ValueError("DELETE sem filtro bloqueado no PostgresRepository")
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._qualified_table(table)} WHERE {where_sql}", values)
            conn.commit()
        finally:
            conn.close()

    def _patch_with_in(self, table: str, column: str, values: list[str], body: dict[str, Any]) -> None:
        if self.dry_run:
            return
        for chunk in chunks(values, 250):
            if not chunk:
                continue
            self._update(table, body, {column: "in.(" + ",".join(chunk) + ")"})

    def _rest_json(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        *,
        query: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        table = path.strip("/").removeprefix(self.table_prefix)
        if method == "PATCH":
            return self._update(table, body or {}, query or {}, returning="representation" in (extra_headers or {}).get("Prefer", ""))
        if method == "DELETE":
            self._delete(table, query or {})
            return None
        if method == "GET":
            return self._select(table, query or {})
        raise NotImplementedError(f"Metodo {method} nao suportado pelo PostgresRepository")

    def _update(self, table: str, body: dict[str, Any], params: dict[str, str], *, returning: bool = False) -> list[dict[str, Any]] | None:
        if not body:
            return [] if returning else None
        where_sql, values = _where_from_params(params)
        if not where_sql:
            raise ValueError("UPDATE sem filtro bloqueado no PostgresRepository")
        columns = list(body)
        set_sql = ", ".join(f"{_ident(column)} = %s" for column in columns)
        sql = f"UPDATE {self._qualified_table(table)} SET {set_sql} WHERE {where_sql}"
        update_values = [_pg_value(body[column]) for column in columns]
        if returning:
            sql += " RETURNING *"
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, update_values + values)
                rows = [_json_ready_row(dict(row)) for row in cursor.fetchall()] if returning else None
            conn.commit()
            return rows
        finally:
            conn.close()

    def auth_user_count(self) -> int:
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT count(*) AS count FROM {self._qualified_table('app_users')}")
                row = cursor.fetchone()
                return int(row["count"] if row else 0)
        finally:
            conn.close()

    def auth_user_by_username(self, username_normalized: str) -> dict[str, Any] | None:
        rows = self._select("app_users", {"username_normalized": "eq." + username_normalized, "limit": "1"})
        return rows[0] if rows else None

    def auth_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        rows = self._select("app_users", {"id": "eq." + user_id, "limit": "1"})
        return rows[0] if rows else None

    def auth_create_user(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("app_users", [row], conflict="id")
        return self.auth_user_by_id(str(row["id"])) or row

    def auth_update_user(self, user_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        rows = self._update("app_users", fields, {"id": "eq." + user_id}, returning=True)
        return rows[0] if rows else None

    def auth_list_users(self) -> list[dict[str, Any]]:
        return self._select("app_users", {"order": "created_at.desc"})

    def auth_create_session(self, row: dict[str, Any]) -> dict[str, Any]:
        self._upsert("auth_sessions", [row], conflict="id")
        return row

    def auth_session_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        rows = self._select("auth_sessions", {"token_hash": "eq." + token_hash, "limit": "1"})
        return rows[0] if rows else None

    def auth_revoke_session(self, token_hash: str, revoked_at: str) -> None:
        self._update("auth_sessions", {"revoked_at": revoked_at}, {"token_hash": "eq." + token_hash})

    def auth_log_admin_action(self, row: dict[str, Any]) -> None:
        self._upsert("admin_audit_log", [row], conflict="id")

    def _table(self, logical_name: str) -> str:
        return self.table_prefix + logical_name

    def _qualified_table(self, logical_name: str) -> str:
        return f"{_ident(self.schema)}.{_ident(self._table(logical_name))}"

    def _schema_sql(self) -> str:
        return "\n\n".join(self._schema_sqls())

    def _schema_sqls(self) -> list[str]:
        return [self._transform_schema_sql(path.read_text(encoding="utf-8")) for path in sorted(POSTGRES_MIGRATIONS.glob("*.sql"))]

    def _transform_schema_sql(self, sql: str) -> str:
        sql = re.sub(
            r"CREATE SCHEMA IF NOT EXISTS\s+public;",
            f"CREATE SCHEMA IF NOT EXISTS {_ident(self.schema)};",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bpublic\.agent_tc_([a-zA-Z0-9_]+)",
            lambda match: f"{_ident(self.schema)}.{_ident(self.table_prefix + match.group(1))}",
            sql,
        )
        sql = re.sub(
            r"\bidx_agent_tc_([a-zA-Z0-9_]+)",
            lambda match: _ident(_index_name(self.table_prefix, match.group(1))),
            sql,
        )
        return sql


def _pg_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value


def _json_ready_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat(timespec="seconds")
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _select_columns(select: str | None) -> str:
    if not select or select == "*":
        return "*"
    columns = [column.strip() for column in select.split(",") if column.strip()]
    if not columns:
        return "*"
    return ", ".join(_ident(column) for column in columns)


def _where_from_params(params: dict[str, str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    for column, raw in params.items():
        if column in {"select", "order", "limit", "offset"}:
            continue
        if raw.startswith("eq."):
            clauses.append(f"{_ident(column)} = %s")
            values.append(raw[3:])
        elif raw.startswith("in.(") and raw.endswith(")"):
            items = [item for item in raw[4:-1].split(",") if item != ""]
            if not items:
                clauses.append("false")
            else:
                clauses.append(f"{_ident(column)} = ANY(%s)")
                values.append(items)
        elif raw == "is.null":
            clauses.append(f"{_ident(column)} IS NULL")
        elif raw == "not.is.null":
            clauses.append(f"{_ident(column)} IS NOT NULL")
        else:
            raise NotImplementedError(f"Filtro PostgREST nao suportado no PostgresRepository: {column}={raw}")
    return " AND ".join(clauses), values


def _order_sql(order: str | None) -> str:
    if not order:
        return ""
    parts: list[str] = []
    for item in order.split(","):
        tokens = [token for token in item.strip().split(".") if token]
        if not tokens:
            continue
        direction = "DESC" if len(tokens) > 1 and tokens[1].lower() == "desc" else "ASC"
        parts.append(f"{_ident(tokens[0])} {direction}")
    return ", ".join(parts)


def _ident(value: str) -> str:
    if not value or not value.replace("_", "").isalnum():
        raise ValueError(f"Identificador SQL invalido: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def _index_name(table_prefix: str, suffix: str) -> str:
    prefix = table_prefix.rstrip("_")
    if prefix:
        return f"idx_{prefix}_{suffix}"
    return f"idx_{suffix}"

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_tc_core.postgres_repository import PostgresRepository
from agent_tc_core.sqlite_repository import SQLiteRepository
from agent_tc_core.supabase_repository import SupabaseHttpError, SupabaseRepository


DEFAULT_DB = PROJECT_ROOT / "data" / "agent_tc.sqlite"
DEFAULT_ENV = PROJECT_ROOT / ".env"


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent TC database utilities")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Caminho do banco SQLite.")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Arquivo .env para Supabase.")
    parser.add_argument("--supabase-schema", default="public", help="Schema usado no Supabase.")
    parser.add_argument("--supabase-table-prefix", default="agent_tc_", help="Prefixo das tabelas no Supabase.")
    parser.add_argument("--postgres-dsn", help="DSN PostgreSQL usado quando --backend postgres.")
    parser.add_argument("--postgres-schema", default="public", help="Schema usado quando --backend postgres.")
    parser.add_argument("--postgres-table-prefix", default="agent_tc_", help="Prefixo das tabelas quando --backend postgres.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-sqlite", help="Cria/atualiza o banco SQLite local.")
    init_supabase = sub.add_parser("init-supabase", help="Valida Supabase, cria bucket e semeia modulos.")
    add_supabase_args(init_supabase)
    init_postgres = sub.add_parser("init-postgres", help="Cria/atualiza o banco PostgreSQL local e semeia modulos.")
    add_postgres_args(init_postgres)

    import_parser = sub.add_parser("import-payload", help="Importa shadow_payload.json.")
    import_parser.add_argument("--payload", required=True)
    import_parser.add_argument("--backend", choices=["sqlite", "supabase", "postgres"], default="sqlite")
    import_parser.add_argument("--dry-run", action="store_true", help="Planeja sem escrever no Supabase.")
    add_postgres_args(import_parser)
    add_supabase_args(import_parser, include_env=False)

    sub.add_parser("summary", help="Mostra contagens principais.")
    summary_supabase = sub.add_parser("summary-supabase", help="Mostra contagens principais no Supabase.")
    add_supabase_args(summary_supabase)
    summary_postgres = sub.add_parser("summary-postgres", help="Mostra contagens principais no PostgreSQL local.")
    add_postgres_args(summary_postgres)

    args = parser.parse_args()

    if args.command == "init-sqlite":
        repo = SQLiteRepository(args.db)
        repo.initialize()
        print(json.dumps({"ok": True, "db_path": str(Path(args.db).resolve())}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "init-supabase":
        repo = SupabaseRepository(
            env_path=args.env,
            schema=args.supabase_schema,
            table_prefix=args.supabase_table_prefix,
        )
        repo.initialize()
        print(json.dumps({"ok": True, "backend": "supabase", "schema": args.supabase_schema}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "init-postgres":
        repo = PostgresRepository(
            env_path=args.env,
            dsn=args.postgres_dsn,
            schema=args.postgres_schema,
            table_prefix=args.postgres_table_prefix,
        )
        repo.initialize()
        print(json.dumps({"ok": True, "backend": "postgres", "schema": args.postgres_schema}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "import-payload":
        if args.backend == "supabase":
            repo = SupabaseRepository(
                env_path=args.env,
                schema=args.supabase_schema,
                table_prefix=args.supabase_table_prefix,
                dry_run=args.dry_run,
            )
        elif args.backend == "postgres":
            repo = PostgresRepository(
                env_path=args.env,
                dsn=args.postgres_dsn,
                schema=args.postgres_schema,
                table_prefix=args.postgres_table_prefix,
                dry_run=args.dry_run,
            )
        else:
            repo = SQLiteRepository(args.db)
        result = repo.import_payload_file(args.payload)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summary":
        repo = SQLiteRepository(args.db)
        repo.initialize()
        summary = {
            "db_path": str(Path(args.db).resolve()),
            "modules": len(repo.modules()),
            "runs": len(repo.runs()),
            "testcase_hierarchy": len(repo.testcase_hierarchy()),
        }
        runs = repo.runs()
        if runs:
            latest = runs[0]
            summary["latest_run"] = latest["id"]
            summary["latest_run_occurrences"] = len(repo.failures(latest["id"]))
            summary["latest_run_evidence_files"] = len(repo.evidences(latest["id"]))
            summary["latest_run_ai_groups"] = len(repo.groups(latest["id"]))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summary-postgres":
        repo = PostgresRepository(
            env_path=args.env,
            dsn=args.postgres_dsn,
            schema=args.postgres_schema,
            table_prefix=args.postgres_table_prefix,
        )
        runs = repo.runs()
        summary = {
            "backend": "postgres",
            "schema": args.postgres_schema,
            "table_prefix": args.postgres_table_prefix,
            "modules": len(repo.modules()),
            "runs": len(runs),
            "testcase_hierarchy": len(repo.testcase_hierarchy()),
        }
        if runs:
            latest = runs[0]
            summary["latest_run"] = latest["id"]
            summary["latest_run_occurrences"] = len(repo.failures(latest["id"]))
            summary["latest_run_evidence_files"] = len(repo.evidences(latest["id"]))
            summary["latest_run_ai_groups"] = len(repo.groups(latest["id"]))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summary-supabase":
        repo = SupabaseRepository(
            env_path=args.env,
            schema=args.supabase_schema,
            table_prefix=args.supabase_table_prefix,
        )
        runs = repo.runs()
        summary = {
            "backend": "supabase",
            "schema": args.supabase_schema,
            "table_prefix": args.supabase_table_prefix,
            "modules": len(repo.modules()),
            "runs": len(runs),
            "testcase_hierarchy": len(repo.testcase_hierarchy()),
        }
        if runs:
            latest = runs[0]
            summary["latest_run"] = latest["id"]
            summary["latest_run_occurrences"] = len(repo.failures(latest["id"]))
            summary["latest_run_evidence_files"] = len(repo.evidences(latest["id"]))
            summary["latest_run_ai_groups"] = len(repo.groups(latest["id"]))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    parser.error("Comando invalido")
    return 2


def add_postgres_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Arquivo .env para Postgres.")
    parser.add_argument("--postgres-dsn", help="DSN PostgreSQL usado quando --backend postgres.")
    parser.add_argument("--postgres-schema", default="public", help="Schema usado quando --backend postgres.")
    parser.add_argument("--postgres-table-prefix", default="agent_tc_", help="Prefixo das tabelas quando --backend postgres.")


def add_supabase_args(parser: argparse.ArgumentParser, *, include_env: bool = True) -> None:
    if include_env:
        parser.add_argument("--env", default=str(DEFAULT_ENV), help="Arquivo .env para Supabase.")
    parser.add_argument("--supabase-schema", default="public", help="Schema usado no Supabase.")
    parser.add_argument("--supabase-table-prefix", default="agent_tc_", help="Prefixo das tabelas no Supabase.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SupabaseHttpError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "supabase_http_error",
                    "status": exc.status,
                    "message": exc.body,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_tc_core.postgres_repository import PostgresRepository
from agent_tc_core.sqlite_repository import SQLiteRepository
from agent_tc_core.supabase_repository import SupabaseHttpError, SupabaseRepository


DEFAULT_DB = PROJECT_ROOT / "data" / "agent_tc.sqlite"
DEFAULT_ENV = PROJECT_ROOT / ".env"


def default_backend() -> str:
    backend = os.getenv("AGENT_TC_BACKEND") or "postgres"
    return backend if backend in {"sqlite", "supabase", "postgres"} else "postgres"


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent TC maintenance routines")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Caminho do banco SQLite.")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Arquivo .env do backend.")
    parser.add_argument("--supabase-schema", default="public", help="Schema usado no Supabase.")
    parser.add_argument("--supabase-table-prefix", default="agent_tc_", help="Prefixo das tabelas no Supabase.")
    parser.add_argument("--postgres-dsn", help="DSN PostgreSQL usado quando --backend postgres.")
    parser.add_argument("--postgres-schema", default="public", help="Schema usado quando --backend postgres.")
    parser.add_argument("--postgres-table-prefix", default="agent_tc_", help="Prefixo das tabelas quando --backend postgres.")
    sub = parser.add_subparsers(dest="command", required=True)

    purge = sub.add_parser(
        "purge-inactive-versions",
        help="Remove rodagens de versões que não rodam há N dias.",
    )
    purge.add_argument("--db", default=str(DEFAULT_DB), help="Caminho do banco SQLite.")
    purge.add_argument("--env", default=str(DEFAULT_ENV), help="Arquivo .env do backend.")
    purge.add_argument("--supabase-schema", default="public", help="Schema usado no Supabase.")
    purge.add_argument("--supabase-table-prefix", default="agent_tc_", help="Prefixo das tabelas no Supabase.")
    purge.add_argument("--postgres-dsn", help="DSN PostgreSQL usado quando --backend postgres.")
    purge.add_argument("--postgres-schema", default="public", help="Schema usado quando --backend postgres.")
    purge.add_argument("--postgres-table-prefix", default="agent_tc_", help="Prefixo das tabelas quando --backend postgres.")
    purge.add_argument("--backend", choices=["sqlite", "supabase", "postgres"], default=default_backend())
    purge.add_argument("--retention-days", type=int, default=30)
    purge.add_argument(
        "--apply",
        action="store_true",
        help="Executa a limpeza. Sem esta flag, roda em dry-run.",
    )
    purge.add_argument(
        "--now",
        help="Data/hora UTC ISO para teste controlado. Ex: 2026-08-17T12:00:00+00:00",
    )

    args = parser.parse_args()

    if args.command == "purge-inactive-versions":
        if args.retention_days < 1:
            raise ValueError("--retention-days deve ser maior que zero")
        now = parse_now(args.now)
        dry_run = not args.apply
        if args.backend == "sqlite":
            repo = SQLiteRepository(args.db)
        elif args.backend == "postgres":
            repo = PostgresRepository(
                env_path=args.env,
                dsn=args.postgres_dsn,
                schema=args.postgres_schema,
                table_prefix=args.postgres_table_prefix,
            )
        else:
            repo = SupabaseRepository(
                env_path=args.env,
                schema=args.supabase_schema,
                table_prefix=args.supabase_table_prefix,
                dry_run=dry_run,
            )
        result = repo.purge_inactive_versions(
            retention_days=args.retention_days,
            dry_run=dry_run,
            now=now,
        )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0

    parser.error("Comando invalido")
    return 2


def parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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

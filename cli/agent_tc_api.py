from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_tc_core.api_server import make_server
from agent_tc_core.sqlite_repository import SQLiteRepository
from agent_tc_core.supabase_repository import SupabaseRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent TC local API")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument(
        "--logs-root",
        default=str(PROJECT_ROOT / "logs_shadow"),
        help="Pasta onde ficam os shadow_payload.json.",
    )
    parser.add_argument(
        "--backend",
        choices=["local-json", "sqlite", "supabase"],
        default="local-json",
        help="Fonte de dados da API.",
    )
    parser.add_argument(
        "--sqlite-db",
        default=str(PROJECT_ROOT / "data" / "agent_tc.sqlite"),
        help="Banco SQLite usado quando --backend sqlite.",
    )
    parser.add_argument(
        "--env",
        default=str(PROJECT_ROOT / ".env"),
        help="Arquivo .env usado quando --backend supabase.",
    )
    parser.add_argument(
        "--supabase-schema",
        default="public",
        help="Schema PostgREST usado quando --backend supabase.",
    )
    parser.add_argument(
        "--supabase-table-prefix",
        default="agent_tc_",
        help="Prefixo das tabelas canonicas no Supabase.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Bloqueia POST /analyze e POST /rerun-requests para deploy publico.",
    )
    args = parser.parse_args()

    repository = None
    if args.backend == "sqlite":
        repository = SQLiteRepository(args.sqlite_db)
        repository.initialize()
    elif args.backend == "supabase":
        repository = SupabaseRepository(
            env_path=args.env,
            schema=args.supabase_schema,
            table_prefix=args.supabase_table_prefix,
        )
        repository.initialize()

    server = make_server(
        args.host,
        args.port,
        args.logs_root,
        repository=repository,
        read_only=args.read_only,
        env_path=args.env,
    )
    print(f"Agent TC API em http://{args.host}:{args.port}")
    print(f"backend={args.backend}")
    print(f"read_only={args.read_only}")
    if args.backend == "sqlite":
        print(f"sqlite_db={args.sqlite_db}")
    if args.backend == "supabase":
        print(f"supabase_schema={args.supabase_schema}")
        print(f"supabase_table_prefix={args.supabase_table_prefix}")
        print(f"env={args.env}")
    print(f"logs_root={args.logs_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Encerrando Agent TC API...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

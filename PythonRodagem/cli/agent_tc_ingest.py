from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_tc_core.api_repository import RemoteApiRepository
from agent_tc_core.pipeline import run_shadow_pipeline
from agent_tc_core.postgres_repository import PostgresRepository
from agent_tc_core.sqlite_repository import SQLiteRepository


def default_backend() -> str:
    return os.getenv("AGENT_TC_BACKEND") or "api"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analisa uma rodagem e persiste no backend escolhido.")
    parser.add_argument("--run-folder", required=True, help="Pasta completa da rodagem.")
    parser.add_argument(
        "--mds",
        required=True,
        action="append",
        help="Caminho de um .mds. Pode ser repetido ou conter caminhos separados por ponto e virgula.",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "logs_shadow"),
        help="Pasta para relatorios/payloads gerados pelo Python.",
    )
    parser.add_argument("--vm", help="Nome da VM. Se omitido, infere pelo caminho da rodagem.")
    parser.add_argument("--times-folder", help="Pasta opcional com arquivos Tempos *.txt.")
    parser.add_argument("--project-suite", help="Caminho opcional do ProjectSuite .pjs.")
    parser.add_argument("--backend", choices=["sqlite", "postgres", "api"], default=default_backend())
    parser.add_argument("--env", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--sqlite-db", default=str(PROJECT_ROOT / "data" / "agent_tc.sqlite"))
    parser.add_argument("--postgres-dsn", help="DSN PostgreSQL usado quando --backend postgres.")
    parser.add_argument("--dry-run", action="store_true", help="Analisa e planeja envio sem gravar no backend.")
    args = parser.parse_args()

    report_dir, payload = run_shadow_pipeline(
        run_folder=args.run_folder,
        mds_path=args.mds,
        output_root=args.output_root,
        vm_name=args.vm,
        times_folder=args.times_folder,
        project_suite_path=args.project_suite,
    )

    if args.backend == "sqlite":
        repository = SQLiteRepository(args.sqlite_db)
    elif args.backend == "postgres":
        repository = PostgresRepository(env_path=args.env, dsn=args.postgres_dsn, dry_run=args.dry_run)
    elif args.backend == "api":
        repository = RemoteApiRepository(env_path=args.env, dry_run=args.dry_run)

    result = repository.import_payload(
        payload,
        source=str(Path(report_dir) / "shadow_payload.json"),
    )

    print(
        json.dumps(
            {
                "ok": True,
                "report_dir": str(report_dir),
                "rodagem": payload.get("rodagem"),
                "falhas": len(payload.get("falhas") or []),
                "evidencias": len(payload.get("evidencias") or []),
                "diferencas": len(payload.get("diferencas_relatorio") or []),
                "testcase_hierarchy": len(payload.get("testcase_hierarchy") or []),
                "atrasos_rodagem": len(payload.get("atrasos_rodagem") or []),
                "import_result": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

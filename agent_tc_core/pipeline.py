from __future__ import annotations

from pathlib import Path

from .config import parse_run_context
from .extractor import extract_archive, find_extractor, inventory_archives
from .mds import MdsIndex
from .parser import analyze_failure
from .payload import build_shadow_payload
from .reporter import write_shadow_reports


def run_shadow_pipeline(
    *,
    run_folder: str | Path,
    mds_path: str | Path,
    output_root: str | Path,
    vm_name: str | None = None,
) -> tuple[Path, dict[str, object]]:
    context = parse_run_context(
        run_folder=run_folder,
        mds_path=mds_path,
        output_root=output_root,
        vm_name=vm_name,
    )

    if not context.run_folder.exists():
        raise FileNotFoundError("Pasta de rodagem nao encontrada: " + str(context.run_folder))
    if not context.mds_path.exists():
        raise FileNotFoundError("Unico.mds nao encontrado: " + str(context.mds_path))

    extractor = find_extractor()
    archives = inventory_archives(context.run_folder)
    if not archives:
        raise RuntimeError("Nenhum RAR/ZIP encontrado em: " + str(context.run_folder))

    analysis_dir = context.output_root / f"shadow_analysis_{context.id_rodagem}"
    report_dir = context.output_root / f"shadow_reports_{context.id_rodagem}"
    mds = MdsIndex(context.mds_path)

    failures = []
    for archive in archives:
        extracted = extract_archive(archive, analysis_dir, extractor)
        failures.append(
            analyze_failure(
                archive=archive,
                extracted_dir=extracted,
                case_info=mds.case_info(archive.id_caso_teste),
            )
        )

    payload = build_shadow_payload(context, failures, mds.hierarchy_rows())
    write_shadow_reports(report_dir, payload)
    return report_dir, payload

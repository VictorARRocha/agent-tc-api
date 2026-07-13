from __future__ import annotations

import re
from pathlib import Path

from .config import parse_run_context
from .extractor import ArchiveExtractionError, extract_archive, find_extractor, inventory_archives
from .mds import MdsIndex
from .parser import analyze_failure
from .payload import build_shadow_payload
from .reporter import write_shadow_reports


TOTAL_TESTS_FILE = "TotalTestesRodados.txt"


def read_total_tests_from_run_folder(run_folder: Path) -> int | None:
    total_file = run_folder / TOTAL_TESTS_FILE
    if not total_file.exists():
        return None

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = total_file.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None

    match = re.search(r"\d+", text)
    if not match:
        return None
    return int(match.group(0))


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
    processing_errors = []
    for archive in archives:
        try:
            extracted = extract_archive(archive, analysis_dir, extractor)
        except ArchiveExtractionError as exc:
            has_partial_files = exc.target.exists() and any(
                path.is_file() for path in exc.target.rglob("*")
            )
            processing_errors.append(
                {
                    "arquivo": archive.nome_arquivo,
                    "etapa": "extracao",
                    "codigo": exc.returncode,
                    "parcial_utilizavel": has_partial_files,
                    "mensagem": str(exc)[-2000:],
                }
            )
            if not has_partial_files:
                continue
            extracted = exc.target
        except Exception as exc:
            processing_errors.append(
                {
                    "arquivo": archive.nome_arquivo,
                    "etapa": "extracao",
                    "codigo": None,
                    "parcial_utilizavel": False,
                    "mensagem": str(exc)[-2000:],
                }
            )
            continue
        try:
            failures.append(
                analyze_failure(
                    archive=archive,
                    extracted_dir=extracted,
                    case_info=mds.case_info(archive.id_caso_teste),
                )
            )
        except Exception as exc:
            processing_errors.append(
                {
                    "arquivo": archive.nome_arquivo,
                    "etapa": "analise",
                    "codigo": None,
                    "parcial_utilizavel": False,
                    "mensagem": str(exc)[-2000:],
                }
            )

    total_executed = read_total_tests_from_run_folder(context.run_folder)
    if total_executed is None:
        total_executed = mds.project_variable_int("wpSomaCasosExecutados")
    payload = build_shadow_payload(
        context,
        failures,
        mds.hierarchy_rows(),
        total_executed=total_executed,
        total_archives=len(archives),
        processing_errors=processing_errors,
    )
    write_shadow_reports(report_dir, payload)
    return report_dir, payload

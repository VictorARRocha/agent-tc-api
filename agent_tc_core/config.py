from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .utils import safe_token


@dataclass(frozen=True)
class RunContext:
    run_folder: Path
    mds_path: Path
    output_root: Path
    vm_name: str
    versao: str
    versao_safe: str
    data_inicio: str
    stamp: str
    id_rodagem: str


RUN_NAME_RE = re.compile(
    r"^(?P<version>.+?)\s+(?P<date>\d{2}_\d{2}_\d{4})\s+(?P<time>\d{2}_\d{2}_\d{2})$"
)
DATE_ONLY_RUN_NAME_RE = re.compile(
    r"^(?P<date>\d{2}_\d{2}_\d{4})\s+(?P<time>\d{2}_\d{2}_\d{2})$"
)


def infer_vm_from_path(run_folder: Path) -> str:
    parent = run_folder.parent.name
    return parent or "VM_DESCONHECIDA"


def infer_version_from_mds_path(mds_path: str | Path) -> str:
    raw = str(mds_path).lower()
    if "practice" in raw:
        return "PRACTICE"
    if "suprema" in raw:
        return "SUPREMA"
    return "SEM_VERSAO"


def parse_run_context(
    *,
    run_folder: str | Path,
    mds_path: str | Path,
    output_root: str | Path,
    vm_name: str | None = None,
) -> RunContext:
    folder = Path(run_folder)
    match = RUN_NAME_RE.match(folder.name)
    if match:
        version = match.group("version").strip()
    else:
        match = DATE_ONLY_RUN_NAME_RE.match(folder.name)
        version = infer_version_from_mds_path(mds_path) if match else ""
    if not match:
        raise ValueError(
            "Nome da pasta deve seguir 'VERSAO dd_MM_yyyy HH_mm_ss' ou 'dd_MM_yyyy HH_mm_ss': " + folder.name
        )

    parsed = datetime.strptime(
        match.group("date") + " " + match.group("time"), "%d_%m_%Y %H_%M_%S"
    )
    parsed = parsed.replace(tzinfo=timezone(timedelta(hours=-3)))
    stamp = parsed.strftime("%Y%m%d_%H%M%S")
    vm = (vm_name or infer_vm_from_path(folder)).strip().lower()
    id_rodagem = f"rod_{safe_token(vm)}_{safe_token(version)}_{stamp}"

    return RunContext(
        run_folder=folder,
        mds_path=Path(str(mds_path).split(";", 1)[0].strip().strip('"')),
        output_root=Path(output_root),
        vm_name=vm,
        versao=version,
        versao_safe=safe_token(version),
        data_inicio=parsed.isoformat(timespec="seconds"),
        stamp=stamp,
        id_rodagem=id_rodagem,
    )

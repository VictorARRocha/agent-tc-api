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
    r"^(?:(?P<prefix>.*?)\s+)?(?P<date>\d{2}_\d{2}_\d{4})[\s_]+(?P<time>\d{2}_\d{2}_\d{2})$"
)
LEGACY_VERSION_RE = re.compile(r"\b\d+(?:\.\d+)+[A-Za-z]?\b")


def infer_vm_from_path(run_folder: Path) -> str:
    parent = run_folder.parent.name
    return parent or "VM_DESCONHECIDA"


def infer_version_from_mds_path(mds_path: str | Path) -> str:
    raw = str(mds_path).lower()
    if "practice" in raw:
        return "PRACTICE"
    if "suprema" in raw or "integracoes" in raw or "integrações" in raw:
        return "SUPREMA"
    return "SEM_VERSAO"


def is_legacy_system_context(mds_path: str | Path) -> bool:
    raw = str(mds_path).lower()
    return (
        "practice" in raw
        or "suprema" in raw
        or "integracoes" in raw
        or "integrações" in raw
    )


def normalize_run_version(raw_version: str, mds_path: str | Path) -> str:
    version = raw_version.strip()
    if not version:
        return infer_version_from_mds_path(mds_path)
    if is_legacy_system_context(mds_path):
        matches = LEGACY_VERSION_RE.findall(version)
        if matches:
            return matches[-1]
    return version


PRACTICE_INDIVIDUAL = "practice base individual"
PRACTICE_AGRUPADA = "practice base agrupada"
# A Base Agrupada roda antes da Individual na mesma rodagem; uma Agrupada mais velha que isso e de outra.
JANELA_PRACTICE = timedelta(hours=24)


def _inicio_da_pasta(nome: str) -> tuple[str, datetime] | None:
    match = RUN_NAME_RE.match(nome)
    if not match:
        return None
    inicio = datetime.strptime(match.group("date") + " " + match.group("time"), "%d_%m_%Y %H_%M_%S")
    return (match.group("prefix") or "").strip(), inicio


def pasta_agrupada_do_practice(run_folder: str | Path) -> Path | None:
    """Pasta da Base Agrupada da mesma rodagem do Practice, a partir da pasta da Base Individual.

    Uma rodagem do Practice roda os dois projetos e cada um cria a sua pasta, mas so o fim da
    Individual chama o AgenteTC. A Agrupada e a mais recente da mesma versao, iniciada ate 24 h antes.
    """
    pasta = Path(run_folder)
    atual = _inicio_da_pasta(pasta.name)
    if not atual or not atual[0].lower().startswith(PRACTICE_INDIVIDUAL):
        return None
    versao = atual[0][len(PRACTICE_INDIVIDUAL):].strip().lower()
    melhor: tuple[datetime, Path] | None = None
    try:
        irmas = list(pasta.parent.iterdir())
    except OSError:
        return None
    for irma in irmas:
        dados = _inicio_da_pasta(irma.name) if irma.is_dir() else None
        if not dados or not dados[0].lower().startswith(PRACTICE_AGRUPADA):
            continue
        if dados[0][len(PRACTICE_AGRUPADA):].strip().lower() != versao:
            continue
        if not (atual[1] - JANELA_PRACTICE <= dados[1] <= atual[1]):
            continue
        if melhor is None or dados[1] > melhor[0]:
            melhor = (dados[1], irma)
    return melhor[1] if melhor else None


def parse_run_context(
    *,
    run_folder: str | Path,
    mds_path: str | Path,
    output_root: str | Path,
    vm_name: str | None = None,
) -> RunContext:
    folder = Path(run_folder)
    match = RUN_NAME_RE.match(folder.name)
    if not match:
        raise ValueError(
            "Nome da pasta deve seguir 'VERSAO dd_MM_yyyy HH_mm_ss' ou 'dd_MM_yyyy HH_mm_ss': " + folder.name
        )
    version = normalize_run_version(match.group("prefix") or "", mds_path)

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

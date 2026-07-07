from __future__ import annotations

import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .constants import ARCHIVE_EXTENSIONS
from .utils import safe_token


@dataclass(frozen=True)
class ArchiveInfo:
    path: Path
    nome_arquivo: str
    extensao: str
    tamanho_bytes: int
    modificado_em: str
    id_caso_teste: str
    id_valido: bool


def find_extractor() -> Path | None:
    for path in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files\WinRAR\WinRAR.exe"),
    ):
        if path.exists():
            return path
    for command in ("7z.exe", "7z", "UnRAR.exe", "unrar", "WinRAR.exe"):
        found = shutil.which(command)
        if found:
            return Path(found)
    return None


def extract_case_id(name: str) -> tuple[str, bool]:
    prefix = Path(name).stem.split("-", 1)[0].strip()
    parts = prefix.split(".")
    if parts and all(part.isdigit() for part in parts):
        return prefix, True
    return "ID invalido", False


def inventory_archives(run_folder: Path) -> list[ArchiveInfo]:
    items: list[ArchiveInfo] = []
    for path in sorted(run_folder.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in ARCHIVE_EXTENSIONS:
            continue
        case_id, valid = extract_case_id(path.name)
        stat = path.stat()
        items.append(
            ArchiveInfo(
                path=path,
                nome_arquivo=path.name,
                extensao=path.suffix.lower(),
                tamanho_bytes=stat.st_size,
                modificado_em=datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
                id_caso_teste=case_id,
                id_valido=valid,
            )
        )
    return items


def extract_archive(archive: ArchiveInfo, analysis_dir: Path, extractor: Path | None) -> Path:
    analysis_root = analysis_dir.resolve()
    target = (analysis_root / safe_token(Path(archive.nome_arquivo).stem)).resolve()
    if target != analysis_root and analysis_root not in target.parents:
        raise RuntimeError("Diretorio de extracao fora da pasta de analise: " + str(target))
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    if archive.extensao == ".zip":
        with zipfile.ZipFile(archive.path) as zf:
            zf.extractall(target)
        return target

    if extractor is None:
        raise RuntimeError("Nenhuma ferramenta de extracao RAR encontrada.")

    exe = extractor.name.lower()
    if "7z" in exe:
        command = [str(extractor), "x", "-y", str(archive.path), f"-o{target}"]
    elif "unrar" in exe:
        command = [str(extractor), "x", "-y", str(archive.path), str(target) + "\\"]
    else:
        command = [str(extractor), "x", "-ibck", "-y", str(archive.path), str(target) + "\\"]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Falha ao extrair {archive.nome_arquivo}: codigo {result.returncode}; "
            + result.stdout[-1500:]
        )
    return target

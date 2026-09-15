from __future__ import annotations

import difflib
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    BASE_MARKERS,
    CURRENT_MARKERS,
    ERROR_FILE_NAMES,
    IMAGE_EXTENSIONS,
    STATUS_AMBOS,
    STATUS_DIFERENCA,
    STATUS_QUEBRA,
    STATUS_SEM_SINAL,
    TEXT_EXTENSIONS,
)
from .extractor import ArchiveInfo
from .mds import CaseInfo
from .utils import read_text_fallback, short_text


@dataclass(frozen=True)
class EvidenceCandidate:
    path: Path
    tipo_arquivo: str
    role: str
    resumo: str


@dataclass(frozen=True)
class Comparison:
    key: str
    base: Path
    atual: Path
    resumo: dict[str, object]


@dataclass(frozen=True)
class FailureAnalysis:
    archive: ArchiveInfo
    case_info: CaseInfo
    extracted_dir: Path
    status: str
    erro_resumo: str
    comparisons: list[Comparison]
    evidences: list[EvidenceCandidate]


def _comparison_side(path: Path) -> tuple[str | None, str | None]:
    stem = path.stem.lower()
    for marker in BASE_MARKERS:
        if stem.endswith(marker):
            return stem[: -len(marker)], "base"
    for marker in CURRENT_MARKERS:
        if stem.endswith(marker):
            return stem[: -len(marker)], "atual"
    return None, None


def find_comparisons(files: list[Path]) -> list[Comparison]:
    grouped: dict[str, dict[str, Path]] = {}
    for path in files:
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        key, side = _comparison_side(path)
        if not key or not side:
            continue
        grouped.setdefault(key, {})[side] = path

    comparisons: list[Comparison] = []
    for key, pair in sorted(grouped.items()):
        if "base" in pair and "atual" in pair:
            comparisons.append(
                Comparison(
                    key=key,
                    base=pair["base"],
                    atual=pair["atual"],
                    resumo=summarize_comparison(pair["base"], pair["atual"]),
                )
            )
    return comparisons


def summarize_comparison(base: Path, atual: Path) -> dict[str, object]:
    base_text, base_encoding = read_text_fallback(base)
    atual_text, atual_encoding = read_text_fallback(atual)
    base_lines = base_text.splitlines()
    atual_lines = atual_text.splitlines()
    diff = list(
        difflib.unified_diff(
            base_lines[:5000],
            atual_lines[:5000],
            fromfile=base.name,
            tofile=atual.name,
            n=1,
            lineterm="",
        )
    )
    changed = [
        line
        for line in diff
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    return {
        "nome_base": base.name,
        "nome_atual": atual.name,
        "bytes_base": base.stat().st_size,
        "bytes_atual": atual.stat().st_size,
        "linhas_base": len(base_lines),
        "linhas_atual": len(atual_lines),
        "encoding_base": base_encoding,
        "encoding_atual": atual_encoding,
        "hash_base": hashlib.sha256(base.read_bytes()).hexdigest(),
        "hash_atual": hashlib.sha256(atual.read_bytes()).hexdigest(),
        "tem_diferenca": base.read_bytes() != atual.read_bytes(),
        "linhas_alteradas_estimadas": len(changed),
        "amostra_diff": "\n".join(diff[:80])[:8000],
    }


def analyze_failure(
    *,
    archive: ArchiveInfo,
    extracted_dir: Path,
    case_info: CaseInfo,
) -> FailureAnalysis:
    files = [path for path in extracted_dir.rglob("*") if path.is_file()]
    info_errors = [path for path in files if path.name.lower() in ERROR_FILE_NAMES]
    comparisons = find_comparisons(files)
    has_info_error = bool(info_errors)
    has_comparison = bool(comparisons)

    if has_info_error and has_comparison:
        status = STATUS_AMBOS
    elif has_info_error:
        status = STATUS_QUEBRA
    elif has_comparison:
        status = STATUS_DIFERENCA
    else:
        status = STATUS_SEM_SINAL

    erro_resumo = ""
    if info_errors:
        text, _ = read_text_fallback(info_errors[0], limit=20000)
        erro_resumo = short_text(text, 1200)

    evidences: list[EvidenceCandidate] = [
        EvidenceCandidate(
            path=archive.path,
            tipo_arquivo="original",
            role="originais",
            resumo="Arquivo compactado original da falha.",
        )
    ]
    for path in files:
        lower = path.name.lower()
        suffix = path.suffix.lower()
        if lower in ERROR_FILE_NAMES:
            text, _ = read_text_fallback(path, limit=5000)
            evidences.append(
                EvidenceCandidate(
                    path=path,
                    tipo_arquivo="texto",
                    role="textos",
                    resumo=short_text(text, 1200),
                )
            )
        elif suffix in IMAGE_EXTENSIONS:
            is_print = "printstelas" in str(path.parent).lower()
            evidences.append(
                EvidenceCandidate(
                    path=path,
                    tipo_arquivo="print" if is_print else "imagem",
                    role="imagens",
                    resumo="Print do teste." if is_print else "Imagem de erro ou evidencia visual.",
                )
            )

    for comparison in comparisons:
        evidences.append(
            EvidenceCandidate(
                path=comparison.base,
                tipo_arquivo="comparacao_base",
                role="comparacao/base",
                resumo="Arquivo base/antigo do par de comparacao.",
            )
        )
        evidences.append(
            EvidenceCandidate(
                path=comparison.atual,
                tipo_arquivo="comparacao_atual",
                role="comparacao/atual",
                resumo="Arquivo atual/gerado do par de comparacao.",
            )
        )

    return FailureAnalysis(
        archive=archive,
        case_info=case_info,
        extracted_dir=extracted_dir,
        status=status,
        erro_resumo=erro_resumo,
        comparisons=comparisons,
        evidences=evidences,
    )


def mime_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def safe_token(value: object) -> str:
    text = str(value).strip()
    text = re.sub(r"[^A-Za-z0-9.]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "NA"


def ascii_lower(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    return normalized.encode("ascii", "ignore").decode("ascii").lower()


def read_text_fallback(path: Path, limit: int | None = None) -> tuple[str, str]:
    data = path.read_bytes()
    if limit is not None:
        data = data[:limit]
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def short_text(value: str, limit: int = 1200) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


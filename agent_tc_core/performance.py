from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RunContext


TIME_LINE_RE = re.compile(
    r"^(?P<case>\d+(?:\.\d+)*)\s+-\s+(?P<delta>\d{2}:\d{2}:\d{2})\s+MAIS\s+(?P<kind>LENTO|R.PIDO)\s+-+\s+"
    r"Planilha:\s+(?P<expected>\d{2}:\d{2}:\d{2})\s+\|\s+Atual:\s+(?P<actual>\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelayRow:
    testcase_node_id: str
    expected_seconds: int
    actual_seconds: int
    delay_seconds: int
    status: str
    source_file: str


def parse_times_folder(times_folder: str | Path | None) -> list[DelayRow]:
    if not times_folder:
        return []
    folder = Path(times_folder)
    if not folder.exists() or not folder.is_dir():
        return []

    rows: list[DelayRow] = []
    for path in sorted(folder.glob("*.txt")):
        rows.extend(parse_times_file(path))
    return rows


def parse_times_file(path: str | Path) -> list[DelayRow]:
    path = Path(path)
    text = _read_text(path)
    rows: list[DelayRow] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = TIME_LINE_RE.match(line)
        if not match:
            continue
        expected = hms_to_seconds(match.group("expected"))
        actual = hms_to_seconds(match.group("actual"))
        delta = hms_to_seconds(match.group("delta"))
        is_fast = "PIDO" in match.group("kind").upper()
        status = "mais_rapido" if is_fast else "mais_lento"
        delay = -delta if is_fast else delta
        if status == "mais_lento" and actual <= expected:
            continue
        if status == "mais_rapido" and actual >= expected:
            continue
        rows.append(
            DelayRow(
                testcase_node_id=match.group("case"),
                expected_seconds=expected,
                actual_seconds=actual,
                delay_seconds=delay,
                status=status,
                source_file=path.name,
            )
        )
    return rows


def build_delay_payload_rows(
    *,
    run: RunContext,
    module_id: str,
    delay_rows: list[DelayRow],
    hierarchy_by_node: dict[str, dict[str, Any]],
    module_codes: set[str],
    created_at: str,
) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for delay in delay_rows:
        module_code = delay.testcase_node_id.split(".", 1)[0]
        if module_codes and module_code not in module_codes:
            continue
        if delay.testcase_node_id in seen:
            continue
        seen.add(delay.testcase_node_id)
        hierarchy = hierarchy_by_node.get(delay.testcase_node_id) or {}
        row_id = "delay_" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{run.id_rodagem}|{delay.testcase_node_id}",
        ).hex
        out.append(
            {
                "id_atraso": row_id,
                "fk_rodagem": run.id_rodagem,
                "fk_modulo": module_id,
                "codigo_teste": delay.testcase_node_id,
                "nome_teste": hierarchy.get("node_name") or "",
                "tempo_padrao_segundos": delay.expected_seconds,
                "tempo_atual_segundos": delay.actual_seconds,
                "delay_segundos": delay.delay_seconds,
                "status": delay.status,
                "arquivo_origem": delay.source_file,
                "created_at": created_at,
            }
        )
    return sorted(out, key=lambda row: (-abs(int(row["delay_segundos"])), str(row["codigo_teste"])))


def hms_to_seconds(value: str) -> int:
    hours, minutes, seconds = [int(part) for part in value.strip().split(":")]
    return hours * 3600 + minutes * 60 + seconds


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")

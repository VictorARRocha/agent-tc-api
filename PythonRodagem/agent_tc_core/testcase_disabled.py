"""Casos de teste marcados como desativados no dashboard (so visual: o nome aparece riscado na lista).

Quem pode solicitar rodagem no modulo do caso marca e desmarca; o motivo e opcional.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from .permissions import modules_of_test_cases

MAX_REASON_LENGTH = 300
_NODE_ID = re.compile(r"^\d+(?:\.\d+)*$")


class TestcaseDisabledError(ValueError):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error


def clean_node_id(value: Any) -> str:
    """"[2.1.3]" ou "2.1.3" -> "2.1.3"; invalido -> erro 400."""
    node_id = str(value or "").strip().strip("[]").strip()
    if not _NODE_ID.match(node_id):
        raise TestcaseDisabledError(HTTPStatus.BAD_REQUEST, "invalid_node_id", "Informe o numero do caso de teste (ex.: 2.1.3).")
    return node_id


# Autor dos desativados que vem do mod_xml.py (lista fixa no codigo): nao se mexe neles pelo dashboard.
MOD_XML_AUTOR = "mod_xml"


def module_of_node(node_id: str) -> str:
    """Modulo do caso pelo codigo (2.1.3 -> fiscal); rotina geral ou codigo desconhecido -> ""."""
    modules, _unknown = modules_of_test_cases(f"[{node_id}]")
    return modules[0] if modules else ""


def build_disabled_row(body: dict[str, Any], username: str | None) -> dict[str, Any]:
    node_id = clean_node_id(body.get("node_id"))
    reason = " ".join(str(body.get("motivo") or body.get("reason") or "").split())
    if len(reason) > MAX_REASON_LENGTH:
        raise TestcaseDisabledError(HTTPStatus.BAD_REQUEST, "reason_too_long", f"O motivo pode ter no maximo {MAX_REASON_LENGTH} caracteres.")
    return {
        "id": node_id,
        "node_id": node_id,
        "module_slug": module_of_node(node_id),
        "reason": reason,
        "disabled_by": username,
        "disabled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

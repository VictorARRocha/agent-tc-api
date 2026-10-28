"""Importar rodagem pelo dashboard.

O usuario informa a pasta da rodagem (log compactado, ArquivosCompactados\\<VM>\\<rodagem>);
o RegravacaoBridge (D01) pega o pedido e roda o run_agent_tc_python.bat com a VM e a pasta.
Aqui ficam as regras: que pasta e aceita, a montagem do pedido e as transicoes de status.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from .regravacao import RegravacaoError, destino_da_branch

DEFAULT_LOGS_BASE = r"S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados"
ACTIVE_STATUSES = ("solicitado", "processando")
BRIDGE_STATUSES = ("processando", "concluido", "erro")
# A pasta vai como argumento de um .bat: caracteres que o cmd interpreta ficam de fora.
UNSAFE_CHARS = re.compile(r'["%!^&|<>\x00-\x1f]')


class ImportacaoError(ValueError):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def logs_base() -> str:
    return (os.environ.get("AGENT_TC_LOGS_BASE") or DEFAULT_LOGS_BASE).strip().rstrip("\\/")


def pasta_da_rodagem(value: Any, base: str) -> tuple[str, str]:
    """(pasta normalizada, VM). So aceita <base>\\<VM>\\<rodagem>, sem nada que o cmd interprete."""
    text = str(value or "").strip().strip('"').strip().replace("/", "\\").rstrip("\\")
    if not text or UNSAFE_CHARS.search(text):
        raise ImportacaoError(HTTPStatus.BAD_REQUEST, "invalid_folder", "Informe o caminho da pasta da rodagem, sem aspas nem caracteres especiais.")
    prefix = base.rstrip("\\") + "\\"
    if not text.lower().startswith(prefix.lower()):
        raise ImportacaoError(HTTPStatus.BAD_REQUEST, "folder_outside_logs", "A pasta precisa estar dentro de " + base + ".")
    parts = text[len(prefix):].split("\\")
    if len(parts) != 2 or any(part.strip() in ("", ".", "..") for part in parts):
        raise ImportacaoError(HTTPStatus.BAD_REQUEST, "invalid_folder", "Informe a pasta da rodagem: " + prefix + "<VM>\\<rodagem>.")
    vm, nome = (part.strip() for part in parts)
    return prefix + vm + "\\" + nome, vm


def build_import_request(repository: Any, body: dict[str, Any], username: str | None, base: str | None = None) -> dict[str, Any]:
    pasta, vm = pasta_da_rodagem(body.get("pasta") or body.get("run_folder"), base or logs_base())
    for row in repository.import_requests():
        if row.get("status") in ACTIVE_STATUSES and str(row.get("run_folder") or "").lower() == pasta.lower():
            raise ImportacaoError(HTTPStatus.CONFLICT, "import_already_requested", "Esta pasta ja esta na fila de importacao.")
    # A D01 nao rodou a rodagem: a branch do TC dela (svn info) nao e a da VM. Vazio = a da D01.
    try:
        branch_url = destino_da_branch(repository, body.get("branch_url"))
    except RegravacaoError as exc:
        raise ImportacaoError(exc.status, exc.error, str(exc)) from exc
    now = now_iso()
    return {
        "id": "imp_" + uuid.uuid4().hex[:16],
        "status": "solicitado",
        "requested_by": username,
        "run_folder": pasta,
        "vm": vm,
        "branch_url": branch_url,
        "exit_code": None,
        "output": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }


def bridge_update_fields(body: dict[str, Any]) -> dict[str, Any]:
    status = str(body.get("status") or "")
    if status not in BRIDGE_STATUSES:
        raise ImportacaoError(HTTPStatus.BAD_REQUEST, "invalid_status", "Status invalido para o Bridge.")
    fields: dict[str, Any] = {"status": status}
    if body.get("exit_code") is not None:
        fields["exit_code"] = int(body["exit_code"])
    if body.get("output") is not None:
        fields["output"] = str(body["output"])[-8000:]
    if body.get("error_message") is not None:
        fields["error_message"] = str(body["error_message"])[:2000]
    if status in ("concluido", "erro"):
        fields["finished_at"] = now_iso()
    return fields

"""Merge entre branches do projeto do TC pelo dashboard.

O dashboard escolhe origem e destino; o MergeBridge (D01) faz a previa (dry-run: revisoes
que vao entrar, arquivos e conflitos) e, depois da confirmacao, o merge e o commit. Conflito
nunca e gravado: o pedido termina como "conflito" e a tela orienta o merge manual.

Fluxo de status (o dashboard pede o merge direto; a previa continua aceita):
  (direto) ---------------------------------------> solicitado
  previa_solicitada -> previa_processando -> previa_pronta | sem_alteracoes | conflito | erro
  previa_pronta --(confirmar)--> solicitado -> processando -> concluido | sem_alteracoes | conflito | erro
  previa_solicitada | previa_pronta | solicitado --(cancelar)--> cancelado
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

ACTIVE_STATUSES = ("previa_solicitada", "previa_processando", "solicitado", "processando")
PENDING_STATUSES = ("previa_solicitada", "solicitado")  # o Bridge pega
PROCESSING_STATUSES = ("previa_processando", "processando")  # o Bridge retoma se cair no meio
CANCELLABLE_STATUSES = ("previa_solicitada", "previa_pronta", "solicitado")
TERMINAL_STATUSES = ("sem_alteracoes", "conflito", "concluido", "erro", "cancelado")
BRIDGE_STATUSES = ("previa_pronta", "sem_alteracoes", "conflito", "concluido", "erro")
MAX_MESSAGE_LENGTH = 4000
MAX_BRANCHES = 2000


class MergeError(ValueError):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _branch_index(repository: Any) -> dict[str, dict[str, Any]]:
    cache = repository.merge_branches() or {}
    return {str(b.get("url")): b for b in cache.get("branches") or [] if b.get("url")}


def build_merge_request(repository: Any, body: dict[str, Any], username: str | None) -> dict[str, Any]:
    source_url = str(body.get("source_url") or "").strip()
    target_url = str(body.get("target_url") or "").strip()
    if not source_url or not target_url:
        raise MergeError(HTTPStatus.BAD_REQUEST, "branches_required", "Escolha a origem e o destino.")
    if source_url == target_url:
        raise MergeError(HTTPStatus.BAD_REQUEST, "same_branch", "Origem e destino precisam ser diferentes.")
    branches = _branch_index(repository)
    if not branches:
        raise MergeError(HTTPStatus.SERVICE_UNAVAILABLE, "branches_unavailable",
                         "A lista de branches ainda nao foi enviada pelo MergeBridge da D01.")
    source, target = branches.get(source_url), branches.get(target_url)
    if not source or not target:
        raise MergeError(HTTPStatus.BAD_REQUEST, "unknown_branch", "Branch fora da lista do repositorio do TC.")
    for row in repository.merge_requests():
        if (row.get("status") in ACTIVE_STATUSES and row.get("source_url") == source_url
                and row.get("target_url") == target_url):
            raise MergeError(HTTPStatus.CONFLICT, "merge_in_progress",
                             "Ja existe um merge em andamento com essa origem e esse destino.")
    direto = body.get("direto") is True
    if direto:
        # Sem previa: o Bridge faz o merge da origem no estado atual; conflito barra so este pedido.
        _check_trunk_confirmation(target.get("kind"), target.get("name"), body)
        mensagem = _clean_message(body)
    now = now_iso()
    return {
        "id": str(uuid.uuid4()),
        "status": "solicitado" if direto else "previa_solicitada",
        "requested_by": username,
        "source_url": source_url,
        "source_name": source.get("name"),
        "target_url": target_url,
        "target_name": target.get("name"),
        "target_kind": target.get("kind") or "branch",
        "commit_message": mensagem if direto else None,
        "preview_json": {},
        "result_json": {},
        "svn_revision": None,
        "error_message": None,
        "confirmed_by": username if direto else None,
        "confirmed_at": now if direto else None,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }


def confirm_fields(row: dict[str, Any], body: dict[str, Any], username: str | None) -> dict[str, Any]:
    if row.get("status") != "previa_pronta":
        raise MergeError(HTTPStatus.CONFLICT, "not_confirmable", "So e possivel confirmar uma previa pronta e sem conflito.")
    _check_trunk_confirmation(row.get("target_kind"), row.get("target_name"), body)
    return {
        "status": "solicitado",
        # Vazia = o Bridge usa a mensagem padrao da previa.
        "commit_message": _clean_message(body),
        "confirmed_by": username,
        "confirmed_at": now_iso(),
    }


def _check_trunk_confirmation(kind: Any, name: Any, body: dict[str, Any]) -> None:
    if kind == "trunk" and body.get("confirma_trunk") is not True:
        raise MergeError(HTTPStatus.BAD_REQUEST, "trunk_confirmation_required",
                         f"Confirme que o merge vai alterar a {name or 'principal'}.")


def _clean_message(body: dict[str, Any]) -> str | None:
    mensagem = body.get("mensagem")
    if mensagem is not None and not isinstance(mensagem, str):
        raise MergeError(HTTPStatus.BAD_REQUEST, "invalid_message", "A mensagem do commit deve ser texto.")
    mensagem = (mensagem or "").strip()
    if len(mensagem) > MAX_MESSAGE_LENGTH:
        raise MergeError(HTTPStatus.BAD_REQUEST, "message_too_long", f"A mensagem pode ter no maximo {MAX_MESSAGE_LENGTH} caracteres.")
    return mensagem or None


def check_cancel(row: dict[str, Any], username: str | None, is_admin: bool) -> None:
    if row.get("status") not in CANCELLABLE_STATUSES:
        raise MergeError(HTTPStatus.CONFLICT, "not_cancellable", "Este merge nao pode mais ser cancelado.")
    if not is_admin and username != row.get("requested_by"):
        raise MergeError(HTTPStatus.FORBIDDEN, "not_owner", "So quem pediu (ou um administrador) pode cancelar.")


def bridge_update_fields(body: dict[str, Any]) -> dict[str, Any]:
    status = str(body.get("status") or "")
    if status not in BRIDGE_STATUSES:
        raise MergeError(HTTPStatus.BAD_REQUEST, "invalid_status", "Status invalido para o Bridge.")
    fields: dict[str, Any] = {"status": status}
    for key in ("preview_json", "result_json"):
        if isinstance(body.get(key), dict):
            fields[key] = body[key]
    if body.get("svn_revision") is not None:
        fields["svn_revision"] = str(body["svn_revision"])[:50]
    if body.get("error_message") is not None:
        fields["error_message"] = str(body["error_message"])[:2000]
    if status in TERMINAL_STATUSES:
        fields["finished_at"] = now_iso()
    return fields


def clean_branches(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista de branches enviada pelo Bridge (svn ls do repositorio do TC)."""
    raw = body.get("branches")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_BRANCHES:
        raise MergeError(HTTPStatus.BAD_REQUEST, "invalid_branches", "Lista de branches invalida.")
    out = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("url") or not item.get("name"):
            raise MergeError(HTTPStatus.BAD_REQUEST, "invalid_branches", "Cada branch precisa de nome e URL.")
        out.append({
            "name": str(item["name"])[:300],
            "url": str(item["url"])[:2000],
            "kind": "trunk" if item.get("kind") == "trunk" else "branch",
            "last_revision": str(item.get("last_revision") or "")[:20] or None,
            "last_author": str(item.get("last_author") or "")[:200] or None,
            "last_date": str(item.get("last_date") or "")[:40] or None,
        })
    return out

"""Regravacao de bases (Stores\\Files) pelo dashboard.

O dashboard lista as diferencas de uma rodagem e pede a regravacao das escolhidas; o
RegravacaoBridge (D01) pega o pedido, confere se a base no SVN ainda e a da rodagem
(hash) e faz o commit. Aqui ficam as regras: o que pode ser regravado, a montagem do
pedido e as transicoes de status.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

ACTIVE_STATUSES = ("solicitado", "processando")
BRIDGE_STATUSES = ("processando", "concluido", "erro")
# O Bridge manda os arquivos ao svnmucc por arquivo de argumentos (sem limite de linha de comando).
MAX_ITEMS = 2000
# Cabe a mensagem padrao de MAX_ITEMS arquivos (uma linha por arquivo).
MAX_MESSAGE_LENGTH = 200_000

# Motivos por que uma diferenca nao pode ser regravada (mostrados no dashboard).
MOTIVOS = {
    "sem_manifesto": "Rodagem sem o Regravacao.txt (anterior a regravacao pelo dashboard).",
    "caminho_nao_confirmado": "O caminho do script nao e o arquivo do checkpoint; corrigir o script.",
    "caminho_invalido": "Caminho da base fora de Stores\\Files.",
    "sem_origem_svn": "A rodagem nao registrou a URL do SVN da VM.",
    "sem_hash": "A diferenca nao tem o hash da base para conferir no SVN.",
    "sem_evidencia_atual": "O arquivo atual nao foi encontrado nas evidencias.",
    "sem_diferenca": "Base e atual sao iguais.",
    "em_andamento": "Ja existe um pedido em andamento com este arquivo.",
    "ja_regravado": "Ja regravado por um pedido concluido.",
    "conflito_svn": "A base mudou no SVN depois da rodagem; regrave manualmente pelo TortoiseSVN.",
}

# Status de arquivo (gravados pelo Bridge) que indicam conflito no SVN.
CONFLICT_ITEM_STATUSES = ("base_mudou", "base_inexistente")


class RegravacaoError(ValueError):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_base_path(path: str) -> str | None:
    """Caminho relativo a Stores com '/', restrito a Files/...; None se for inseguro."""
    value = str(path or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return None
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or parts[0].lower() != "files" or len(parts) < 2 or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _mesma_url(a: Any, b: Any) -> bool:
    return str(a or "").rstrip("/").lower() == str(b or "").rstrip("/").lower()


def _requested_difference_ids(repository: Any, run_id: str, destino: str | None = None) -> tuple[set[str], set[str], set[str]]:
    """(em pedido ativo, ja gravadas por pedido concluido, com conflito no SVN) desta rodagem.

    Pedido em andamento bloqueia em qualquer branch; ja gravado e conflito valem so para a branch de destino
    (regravar na branch da rodagem nao impede regravar o mesmo arquivo em outra branch).
    """
    active: set[str] = set()
    done: set[str] = set()
    conflict: set[str] = set()
    for request in repository.regravacao_requests(run_id):
        if request.get("status") in ACTIVE_STATUSES:
            active.update(str(item.get("difference_id")) for item in request.get("items_json") or [])
        elif destino is not None and not _mesma_url(request.get("repository_url"), destino):
            continue
        elif request.get("status") == "concluido":
            for result in (request.get("result_json") or {}).get("itens") or []:
                if result.get("status") in ("gravado", "ja_gravado"):
                    done.add(str(result.get("difference_id")))
        elif request.get("status") == "erro":
            # Tentar de novo daria o mesmo conflito: a base no SVN nao e mais a da rodagem.
            for result in (request.get("result_json") or {}).get("itens") or []:
                if result.get("status") in CONFLICT_ITEM_STATUSES:
                    conflict.add(str(result.get("difference_id")))
    return active, done, conflict


def destino_da_branch(repository: Any, branch_url: Any) -> str | None:
    """URL de outra branch para regravar: so as da lista do SVN (a mesma da tela de Merge). Vazio = a da rodagem."""
    url = str(branch_url or "").strip().rstrip("/")
    if not url:
        return None
    conhecidas = []
    if hasattr(repository, "merge_branches"):
        conhecidas = [str(b.get("url") or "").rstrip("/") for b in (repository.merge_branches().get("branches") or [])]
    for conhecida in conhecidas:
        if conhecida and _mesma_url(conhecida, url):
            return conhecida
    raise RegravacaoError(
        HTTPStatus.BAD_REQUEST, "invalid_branch",
        "A branch escolhida nao esta na lista de branches do SVN. Atualize a pagina e escolha de novo.",
    )


def regravacao_candidates(repository: Any, run_id: str, destino_url: str | None = None) -> dict[str, Any] | None:
    """Diferencas da rodagem com a situacao de cada uma para a regravacao (na branch da rodagem ou em destino_url)."""
    run = repository.run(run_id)
    if not run:
        return None
    repository_url = destino_url or run.get("repository_url")
    evidences = {str(e.get("id") or e.get("id_evidencia")): e for e in repository.evidences(run_id)}
    active, done, conflict = _requested_difference_ids(repository, run_id, repository_url)
    itens = []
    for diff in repository.report_differences(run_id):
        summary = diff.get("summary_json") or {}
        manifest = summary.get("regravacao") or {}
        current = evidences.get(str(diff.get("current_evidence_id")))
        base = evidences.get(str(diff.get("base_evidence_id")))
        caminho = safe_base_path(manifest.get("caminho_base") or "") if manifest else None
        if not manifest:
            motivo = "sem_manifesto"
        elif not manifest.get("confirmado"):
            motivo = "caminho_nao_confirmado"
        elif not caminho:
            motivo = "caminho_invalido"
        elif not repository_url:
            motivo = "sem_origem_svn"
        elif not summary.get("hash_base"):
            motivo = "sem_hash"
        elif not current or not current.get("storage_path"):
            motivo = "sem_evidencia_atual"
        elif summary.get("tem_diferenca") is False:
            motivo = "sem_diferenca"
        elif str(diff.get("id")) in active:
            motivo = "em_andamento"
        elif str(diff.get("id")) in done:
            motivo = "ja_regravado"
        elif str(diff.get("id")) in conflict:
            motivo = "conflito_svn"
        else:
            motivo = None
        itens.append({
            "difference_id": diff.get("id"),
            "occurrence_id": diff.get("occurrence_id"),
            "id_caso_teste": diff.get("testcase_node_id"),
            "arquivo_base": diff.get("base_file_name"),
            "arquivo_atual": diff.get("current_file_name"),
            "base_evidence_id": diff.get("base_evidence_id"),
            "current_evidence_id": diff.get("current_evidence_id"),
            "caminho_base": manifest.get("caminho_base") if manifest else None,
            "linhas_alteradas": summary.get("linhas_alteradas_estimadas"),
            "regravavel": motivo is None,
            "motivo": motivo,
            "motivo_texto": MOTIVOS.get(motivo) if motivo else None,
            "_storage_path_atual": current.get("storage_path") if current else None,
            "_storage_path_base": base.get("storage_path") if base else None,
            "_caminho_seguro": caminho,
            "_hash_base": summary.get("hash_base"),
            "_hash_atual": summary.get("hash_atual"),
        })
    return {
        "run_id": run_id,
        # Branch em que a rodagem rodou (o destino padrao) e para onde vai a regravacao.
        "repository_url": run.get("repository_url"),
        "destino_url": repository_url,
        "repository_revision": run.get("repository_revision"),
        "itens": itens,
    }


def public_candidates(data: dict[str, Any]) -> dict[str, Any]:
    """Sem os campos internos (prefixo _) para o dashboard."""
    return {**data, "itens": [{k: v for k, v in item.items() if not k.startswith("_")} for item in data["itens"]]}


def build_regravacao_request(repository: Any, body: dict[str, Any], username: str | None) -> dict[str, Any]:
    run_id = str(body.get("run_id") or body.get("rodagem_id") or "").strip()
    ids = body.get("difference_ids") or body.get("diferencas") or []
    if not run_id:
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "run_id_required", "Informe a rodagem.")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "difference_ids_required", "Escolha ao menos um arquivo.")
    ids = list(dict.fromkeys(ids))
    mensagem = body.get("mensagem")
    if mensagem is not None and not isinstance(mensagem, str):
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "invalid_message", "A mensagem do commit deve ser texto.")
    mensagem = (mensagem or "").strip()
    if len(mensagem) > MAX_MESSAGE_LENGTH:
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "message_too_long", f"A mensagem pode ter no maximo {MAX_MESSAGE_LENGTH} caracteres.")
    if len(ids) > MAX_ITEMS:
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "too_many_items", f"No maximo {MAX_ITEMS} arquivos por pedido.")
    destino = destino_da_branch(repository, body.get("branch_url"))
    data = regravacao_candidates(repository, run_id, destino)
    if data is None:
        raise RegravacaoError(HTTPStatus.NOT_FOUND, "run_not_found", "Rodagem nao encontrada.")
    by_id = {str(item["difference_id"]): item for item in data["itens"]}
    items = []
    for difference_id in ids:
        item = by_id.get(difference_id)
        if not item:
            raise RegravacaoError(HTTPStatus.BAD_REQUEST, "difference_not_found", f"Diferenca {difference_id} nao e desta rodagem.")
        if not item["regravavel"]:
            raise RegravacaoError(
                HTTPStatus.CONFLICT if item["motivo"] == "em_andamento" else HTTPStatus.BAD_REQUEST,
                "not_regravavel",
                f"{item['arquivo_atual']}: {item['motivo_texto']}",
            )
        items.append({
            "difference_id": item["difference_id"],
            "id_caso_teste": item["id_caso_teste"],
            "arquivo_atual": item["arquivo_atual"],
            "caminho_base": item["_caminho_seguro"],
            "storage_path_atual": item["_storage_path_atual"],
            "hash_base": item["_hash_base"],
            "hash_atual": item["_hash_atual"],
        })
    destinos = [item["caminho_base"].lower() for item in items]
    if len(set(destinos)) != len(destinos):
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "duplicated_target", "Dois arquivos escolhidos regravam a mesma base.")
    run = repository.run(run_id) or {}
    now = now_iso()
    return {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "status": "solicitado",
        "requested_by": username,
        "repository_url": data["destino_url"],
        "repository_root": run.get("repository_root"),
        # Mensagem do commit editada no dashboard; vazia = o Bridge usa a padrao.
        "commit_message": mensagem or None,
        "items_json": items,
        "result_json": {},
        "svn_revision": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }


def bridge_update_fields(body: dict[str, Any]) -> dict[str, Any]:
    status = str(body.get("status") or "")
    if status not in BRIDGE_STATUSES:
        raise RegravacaoError(HTTPStatus.BAD_REQUEST, "invalid_status", "Status invalido para o Bridge.")
    fields: dict[str, Any] = {"status": status}
    if isinstance(body.get("result_json"), dict):
        fields["result_json"] = body["result_json"]
    if body.get("svn_revision") is not None:
        fields["svn_revision"] = str(body["svn_revision"])[:50]
    if body.get("error_message") is not None:
        fields["error_message"] = str(body["error_message"])[:2000]
    if status in ("concluido", "erro"):
        fields["finished_at"] = now_iso()
    return fields

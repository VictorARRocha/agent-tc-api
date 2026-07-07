from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import RunContext
from .constants import MODULE_BY_PREFIX, MODULE_CODES_BY_ID
from .parser import FailureAnalysis, mime_for
from .utils import ascii_lower, safe_token


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def module_from_failures(failures: list[FailureAnalysis]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for failure in failures:
        case_id = failure.archive.id_caso_teste
        if case_id == "ID invalido":
            continue
        prefix = case_id.split(".", 1)[0]
        module_id = MODULE_BY_PREFIX.get(prefix, MODULE_BY_PREFIX["0"])["id"]
        counts[module_id] = counts.get(module_id, 0) + 1
    if not counts:
        return MODULE_BY_PREFIX["0"]
    selected_id = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    for module in MODULE_BY_PREFIX.values():
        if module["id"] == selected_id:
            return module
    return MODULE_BY_PREFIX["0"]


def hierarchy_rows_for_module(
    rows: list[dict[str, object]],
    module: dict[str, str],
) -> list[dict[str, object]]:
    codes = set(MODULE_CODES_BY_ID.get(module["id"], ()))
    if not codes:
        return []
    return [
        row
        for row in rows
        if str(row.get("modulo_codigo") or "") in codes
    ]


def failure_id(run: RunContext, case_id: str, index: int) -> str:
    suffix = safe_token(case_id) if case_id != "ID invalido" else f"id_invalido_{index:03d}"
    return f"falha_{run.id_rodagem}_{suffix}"


def cluster_id(run: RunContext, index: int) -> str:
    return f"cluster_{run.id_rodagem}_{index:03d}"


def build_shadow_payload(
    run: RunContext,
    failures: list[FailureAnalysis],
    mds_hierarchy_rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    module = module_from_failures(failures)
    testcase_hierarchy_rows = hierarchy_rows_for_module(
        mds_hierarchy_rows or [],
        module,
    )
    failure_ids = {
        id(failure): failure_id(run, failure.archive.id_caso_teste, index)
        for index, failure in enumerate(failures, 1)
    }
    cluster_by_status: dict[str, str] = {}
    clusters: list[dict[str, object]] = []
    for failure in failures:
        if failure.status not in cluster_by_status:
            cid = cluster_id(run, len(clusters) + 1)
            cluster_by_status[failure.status] = cid
            clusters.append(
                {
                    "id_cluster": cid,
                    "fk_rodagem": run.id_rodagem,
                    "titulo_causa": "Aguardando agrupamento da IA - " + failure.status,
                    "assinatura_tecnica": ascii_lower(failure.status).replace(" ", "_"),
                    "status": failure.status,
                    "raio_x_negocio": "Cluster temporario gerado em modo sombra.",
                    "created_at": now_iso(),
                    "falhas": [],
                }
            )
        for cluster in clusters:
            if cluster["id_cluster"] == cluster_by_status[failure.status]:
                cluster["falhas"].append(failure_ids[id(failure)])
                break

    falhas_rows = []
    evidencias_rows = []
    diferencas_rows = []
    ia_failures = []
    evidence_counter = 0
    diff_counter = 0

    for index, failure in enumerate(failures, 1):
        fid = failure_ids[id(failure)]
        cid = cluster_by_status[failure.status]
        falhas_rows.append(
            {
                "id_falha": fid,
                "fk_cluster": cid,
                "fk_modulo": module["id"],
                "id_caso_teste": failure.archive.id_caso_teste,
                "nome_mds": failure.case_info.nome_mds,
                "grupo": failure.case_info.grupo,
                "descricao": failure.case_info.descricao,
                "arquivo_origem": failure.archive.nome_arquivo,
                "created_at": now_iso(),
                "tipo_detectado_python": failure.status,
                "erro_resumo": failure.erro_resumo,
                "caminho_hierarquico": failure.case_info.caminho_hierarquico,
                "procedure_name": failure.case_info.procedure_name,
            }
        )

        evidence_meta = [
            {
                "tipo": evidence.tipo_arquivo,
                "role": evidence.role,
                "nome": evidence.path.name,
                "extensao": evidence.path.suffix.lower(),
                "tamanho_bytes": evidence.path.stat().st_size,
            }
            for evidence in failure.evidences
        ]
        resumo_diferenca = [
            {
                "nome_base": comparison.resumo["nome_base"],
                "nome_atual": comparison.resumo["nome_atual"],
                "linhas_base": comparison.resumo["linhas_base"],
                "linhas_atual": comparison.resumo["linhas_atual"],
                "linhas_alteradas_estimadas": comparison.resumo[
                    "linhas_alteradas_estimadas"
                ],
                "amostra_diff": comparison.resumo["amostra_diff"][:2000],
            }
            for comparison in failure.comparisons
        ]
        ia_failures.append(
            {
                "id_falha": fid,
                "id_caso_teste": failure.archive.id_caso_teste,
                "nome_mds": failure.case_info.nome_mds,
                "grupo": failure.case_info.grupo,
                "mensagem_erro": failure.erro_resumo,
                "resumo_log": failure.erro_resumo
                or "Sem InformacaoErro.txt detectado pelo Python.",
                "resumo_diferenca": resumo_diferenca,
                "sinais": {
                    "tipo_detectado_python": failure.status,
                    "tem_informacao_erro": bool(failure.erro_resumo),
                    "tem_comparacao": bool(failure.comparisons),
                    "total_comparacoes": len(failure.comparisons),
                    "total_evidencias": len(failure.evidences),
                    "total_imagens": len(
                        [
                            evidence
                            for evidence in failure.evidences
                            if evidence.tipo_arquivo in {"imagem", "print"}
                        ]
                    ),
                    "total_prints": len(
                        [
                            evidence
                            for evidence in failure.evidences
                            if evidence.tipo_arquivo == "print"
                        ]
                    ),
                    "total_textos": len(
                        [
                            evidence
                            for evidence in failure.evidences
                            if evidence.tipo_arquivo == "texto"
                        ]
                    ),
                    "evidencia_incompleta": not failure.erro_resumo
                    and not failure.comparisons,
                },
                "metadados_evidencia": evidence_meta,
            }
        )

        for evidence in failure.evidences:
            evidence_counter += 1
            storage_kind = evidence.role
            storage_path = "/".join(
                [
                    ascii_lower(module["nome"]),
                    run.versao_safe,
                    run.id_rodagem,
                    failure.archive.id_caso_teste,
                    storage_kind,
                    evidence.path.name,
                ]
            )
            evidencias_rows.append(
                {
                    "id_evidencia": f"ev_{fid}_{evidence_counter:04d}",
                    "fk_falha": fid,
                    "tipo_arquivo": evidence.tipo_arquivo,
                    "conteudo_resumo": evidence.resumo,
                    "correlacao_visual": "Print extraido do TestComplete."
                    if evidence.tipo_arquivo == "print"
                    else "",
                    "caminho_evidencia": str(evidence.path),
                    "created_at": now_iso(),
                    "bucket": "",
                    "storage_path": storage_path,
                    "public_url": "",
                    "signed_url": "",
                    "url_expira_em": None,
                    "mime_type": mime_for(evidence.path),
                    "extensao": evidence.path.suffix.lower(),
                    "tamanho_bytes": evidence.path.stat().st_size,
                    "nome_arquivo": evidence.path.name,
                    "upload_ok": False,
                }
            )

        for comparison in failure.comparisons:
            diff_counter += 1
            diferencas_rows.append(
                {
                    "id_diferenca": f"diff_{run.id_rodagem}_{diff_counter:04d}",
                    "fk_rodagem": run.id_rodagem,
                    "fk_modulo": module["id"],
                    "id_caso_teste": failure.archive.id_caso_teste,
                    "nome_caso": failure.case_info.nome_mds,
                    "grupo": failure.case_info.grupo,
                    "nome_arquivo_base": comparison.base.name,
                    "nome_arquivo_atual": comparison.atual.name,
                    "bucket": "",
                    "storage_path_base": "",
                    "storage_path_atual": "",
                    "public_url_base": "",
                    "public_url_atual": "",
                    "mime_type_base": mime_for(comparison.base),
                    "mime_type_atual": mime_for(comparison.atual),
                    "tamanho_base": comparison.base.stat().st_size,
                    "tamanho_atual": comparison.atual.stat().st_size,
                    "resumo_diferenca": comparison.resumo,
                    "created_at": now_iso(),
                }
            )

    payload = {
        "modo": "shadow",
        "rodagem": {
            "id_rodagem": run.id_rodagem,
            "sistema": module["sistema"],
            "versao": run.versao,
            "data_inicio": run.data_inicio,
            "caminho_logs": str(run.run_folder),
            "total_falhas": len(failures),
            "total_clusters": len(clusters),
            "created_at": now_iso(),
            "fk_modulo": module["id"],
            "vm_name": run.vm_name,
        },
        "modulo": {"id_modulo": module["id"], "nome": module["nome"]},
        "agrupamentos_shadow": clusters,
        "falhas": falhas_rows,
        "evidencias": evidencias_rows,
        "diferencas_relatorio": diferencas_rows,
        "testcase_hierarchy": testcase_hierarchy_rows,
        "upsert_plan": {
            "testcase_hierarchy": {
                "table": "testcase_hierarchy",
                "conflict_key": "id",
                "rows": len(testcase_hierarchy_rows),
                "mode": "upsert",
            }
        },
        "ia_input": {
            "rodagem": {
                "id_rodagem": run.id_rodagem,
                "modulo": module["nome"],
                "vm_name": run.vm_name,
                "versao": run.versao,
            },
            "falhas": ia_failures,
            "instrucao": (
                "Agrupar falhas por causa tecnica. Nao ler arquivos. "
                "Usar apenas os resumos enviados."
            ),
        },
    }
    json.dumps(payload, ensure_ascii=False)
    return payload

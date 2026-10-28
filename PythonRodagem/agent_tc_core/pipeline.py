from __future__ import annotations

import dataclasses
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import parse_run_context
from .constants import MODULE_BY_PREFIX, STATUS_SEM_SINAL
from .extractor import ArchiveExtractionError, ArchiveInfo, extract_archive, extract_case_id, find_extractor, inventory_archives
from .mds import MdsCollection, infer_system_from_mds_paths, mds_do_projeto_da_pasta, split_mds_paths
from .parser import FailureAnalysis, analyze_failure, read_run_regravacao_manifest
from .performance import parse_times_folder
from .tempos_casos import medicoes_da_rodagem, medicoes_do_sistema
from .mod_xml_desmarcados import ler_desmarcados_arquivo
from .project_suite import ProjectSuiteVariables
from .payload import build_shadow_payload
from .reporter import write_shadow_reports
from .svn_info import working_copy_info


def sistema_pelos_casos(case_ids: list[str]) -> str | None:
    """Sistema da rodagem pelo prefixo dos casos (16 = Suprema, 19 = Practice, o resto Unico).

    Os gerais [0.x] do Unico nao contam; None se nao houver caso conhecido.
    """
    contagem: dict[str, int] = {}
    for case_id in case_ids:
        prefixo = str(case_id).split(".", 1)[0]
        if prefixo == "0" or prefixo not in MODULE_BY_PREFIX:
            continue
        sistema = MODULE_BY_PREFIX[prefixo]["sistema"]
        contagem[sistema] = contagem.get(sistema, 0) + 1
    if not contagem:
        return None
    return sorted(contagem.items(), key=lambda item: (-item[1], item[0]))[0][0]


def total_pelos_casos(failures, tempos_casos: list[dict]) -> int | None:
    """Casos rodados no Suprema/Practice: os que tiveram tempo medido mais os que falharam.

    O TotalTestesRodados.txt deles nao serve: vai para a ultima pasta criada na VM e o contador
    do TC nao e o de casos rodados. Sem nenhum tempo medido, nao da para saber (None).
    """
    medidos = {str(m["codigo_teste"]).strip() for m in tempos_casos}
    if not medidos:
        return None
    falharam = {f.archive.id_caso_teste for f in failures if f.archive.id_valido}
    return len(medidos | falharam)


TOTAL_TESTS_FILE = "TotalTestesRodados.txt"
TOTAL_OCCURRENCES_FILE = "TotalOcorrenciasTC.txt"
# Lista que o TC grava (oculta) antes de cada compactacao: um .RAR esperado por linha.
EXPECTED_ARCHIVES_FILE = "AgenteTC_Ocorrencias.txt"


def read_total_tests_from_run_folder(run_folder: Path) -> int | None:
    total_file = run_folder / TOTAL_TESTS_FILE
    if not total_file.exists():
        return None

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = total_file.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None

    match = re.search(r"\d+", text)
    if not match:
        return None
    return int(match.group(0))


def read_occurrence_totals_from_run_folder(run_folder: Path) -> dict[str, int] | None:
    total_file = run_folder / TOTAL_OCCURRENCES_FILE
    if not total_file.exists():
        return None

    text = read_text_file(total_file)
    if text is None:
        return None

    total_errors = read_named_int(text, "wpSomaErros")
    total_differences = read_named_int(text, "wpSomaDiferencas")
    if total_errors is None and total_differences is None:
        return None

    return {
        "arquivo_origem": TOTAL_OCCURRENCES_FILE,
        "total_erros_tc": total_errors or 0,
        "total_diferencas_tc": total_differences or 0,
    }


def read_occurrence_totals_from_project_suite(
    project_suite: ProjectSuiteVariables | None,
) -> dict[str, int] | None:
    if project_suite is None:
        return None

    total_errors = project_suite.project_variable_int("wpSomaErros")
    total_differences = project_suite.project_variable_int("wpSomaDiferencas")
    if total_errors is None and total_differences is None:
        return None
    # Zerados: e o valor salvo no projeto (o TC so soma em memoria), nao o desta rodagem.
    if not total_errors and not total_differences:
        return None

    return {
        "arquivo_origem": str(project_suite.project_suite_path),
        "total_erros_tc": total_errors or 0,
        "total_diferencas_tc": total_differences or 0,
    }


def build_archive_integrity(
    occurrence_totals: dict[str, int] | None,
    total_archives: int,
) -> dict[str, object] | None:
    if occurrence_totals is None:
        return None

    total_errors = occurrence_totals["total_erros_tc"]
    total_differences = occurrence_totals["total_diferencas_tc"]
    total_occurrences = total_errors + total_differences
    missing_archives = max(0, total_occurrences - total_archives)
    extra_archives = max(0, total_archives - total_occurrences)
    return {
        "arquivo_origem": occurrence_totals.get("arquivo_origem") or TOTAL_OCCURRENCES_FILE,
        "total_erros_tc": total_errors,
        "total_diferencas_tc": total_differences,
        "total_ocorrencias_tc": total_occurrences,
        "total_compactados": total_archives,
        "compactacao_completa": total_occurrences == total_archives,
        "ocorrencias_sem_compactado": missing_archives,
        "compactados_extras": extra_archives,
    }


def compactados_ausentes(run_folder: Path, archives: list[ArchiveInfo]) -> list[str]:
    """.RAR que o TC registrou e nao estao na pasta (a compactacao ou a copia para o S: falhou)."""
    lista = run_folder / EXPECTED_ARCHIVES_FILE
    try:
        text = read_text_file(lista) if lista.is_file() else None
    except OSError:
        text = None
    if not text:
        return []
    presentes = {archive.nome_arquivo.lower() for archive in archives}
    ausentes: list[str] = []
    for linha in text.lstrip("﻿").splitlines():
        nome = linha.strip()
        if nome and nome.lower() not in presentes and nome not in ausentes:
            ausentes.append(nome)
    return ausentes


def read_named_int(text: str, name: str) -> int | None:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*=\s*(\d+)\s*$", text)
    if not match:
        return None
    return int(match.group(1))


def read_text_file(path: Path) -> str | None:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return None


def run_shadow_pipeline(
    *,
    run_folder: str | Path,
    mds_path: str | Path | list[str | Path],
    output_root: str | Path,
    vm_name: str | None = None,
    times_folder: str | Path | None = None,
    project_suite_path: str | Path | None = None,
    performance_dir: str | Path | None = None,
    mod_xml_path: str | Path | None = None,
    mds_por_sistema: dict[str, str | Path | list[str | Path]] | None = None,
    project_suite_por_sistema: dict[str, str | Path] | None = None,
) -> tuple[Path, dict[str, object]]:
    # Fim da rodagem: o pipeline e chamado logo que ela termina (post do Jenkins ou rotina de fim do TC).
    # O TotalTestesRodados.txt nao serve: falta nas reexecucoes e as vezes e gravado na pasta da rodagem anterior.
    fim_rodagem = datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")
    mds_paths = split_mds_paths(mds_path)
    context = parse_run_context(
        run_folder=run_folder,
        mds_path=mds_paths[0] if mds_paths else mds_path,
        output_root=output_root,
        vm_name=vm_name,
    )

    if not context.run_folder.exists():
        raise FileNotFoundError("Pasta de rodagem nao encontrada: " + str(context.run_folder))
    missing_mds_paths = [path for path in mds_paths if not path.exists()]
    if missing_mds_paths:
        raise FileNotFoundError("Arquivo .mds nao encontrado: " + "; ".join(str(path) for path in missing_mds_paths))
    project_suite = None
    if project_suite_path:
        project_suite_file = Path(project_suite_path)
        if not project_suite_file.exists():
            raise FileNotFoundError("ProjectSuite .pjs nao encontrado: " + str(project_suite_file))
        project_suite = ProjectSuiteVariables(project_suite_file)

    # Tempo de cada caso desta rodagem, do "Tempo CT.csv" que o TC grava no S: (aba Performance).
    tempos_erro = None
    try:
        tempos_casos = medicoes_da_rodagem(performance_dir, context.data_inicio)
    except OSError as exc:
        tempos_casos = []
        tempos_erro = {"tipo": "tempos_casos", "mensagem": f"Nao foi possivel ler os tempos em {performance_dir}: {exc}"}

    extractor = find_extractor()
    archives = inventory_archives(context.run_folder)

    # Sistema da rodagem pelos casos (RARs e, sem falhas, os tempos). O nome da pasta do Suprema
    # e so a versao ("8.09a 14_10_2026 18_36_14"), entao o .mds certo sai daqui.
    sistema = sistema_pelos_casos(
        [a.id_caso_teste for a in archives if a.id_valido] or [str(m["codigo_teste"]) for m in tempos_casos]
    )
    avisos_sistema = []
    if sistema and mds_por_sistema and sistema in mds_por_sistema and infer_system_from_mds_paths(mds_paths) != sistema:
        alternativos = split_mds_paths(mds_por_sistema[sistema])
        faltando = [path for path in alternativos if not path.exists()]
        if alternativos and not faltando:
            mds_paths = alternativos
            context = parse_run_context(
                run_folder=run_folder,
                mds_path=mds_paths[0],
                output_root=output_root,
                vm_name=vm_name,
            )
            suite = (project_suite_por_sistema or {}).get(sistema)
            if project_suite is None and suite and Path(suite).exists():
                project_suite = ProjectSuiteVariables(Path(suite))
        else:
            avisos_sistema.append({
                "tipo": "mds_do_sistema",
                "mensagem": f"Rodagem do {sistema}, mas o .mds dele nao foi encontrado: "
                + "; ".join(str(path) for path in faltando or alternativos),
            })
    # Tempos de outro sistema com o mesmo inicio nao entram.
    if sistema:
        tempos_casos = medicoes_do_sistema(tempos_casos, sistema)

    # Sem compactado e sem tempo nao ha o que mostrar. Com tempos, e uma rodagem 100% verde: vai com 0 falhas.
    if not archives and not tempos_casos:
        raise RuntimeError("Nenhum RAR/ZIP encontrado em: " + str(context.run_folder))
    occurrence_totals = read_occurrence_totals_from_run_folder(context.run_folder)
    if occurrence_totals is None:
        occurrence_totals = read_occurrence_totals_from_project_suite(project_suite)
    archive_integrity = build_archive_integrity(
        occurrence_totals,
        len(archives),
    )

    run_manifest = read_run_regravacao_manifest(context.run_folder)
    analysis_dir = context.output_root / f"shadow_analysis_{context.id_rodagem}"
    report_dir = context.output_root / f"shadow_reports_{context.id_rodagem}"
    mds = MdsCollection(mds_paths)

    failures = []
    processing_errors = []
    for archive in archives:
        # Compactado que nao abre continua sendo uma falha do caso (o nome do .rar diz qual):
        # se sumisse, a rodagem apareceria sem falhas no dashboard.
        falha_extracao = None
        try:
            extracted = extract_archive(archive, analysis_dir, extractor)
        except ArchiveExtractionError as exc:
            has_partial_files = exc.target.exists() and any(
                path.is_file() for path in exc.target.rglob("*")
            )
            processing_errors.append(
                {
                    "arquivo": archive.nome_arquivo,
                    "etapa": "extracao",
                    "codigo": exc.returncode,
                    "parcial_utilizavel": has_partial_files,
                    "mensagem": str(exc)[-2000:],
                }
            )
            extracted = exc.target
            if not has_partial_files:
                falha_extracao = str(exc)
        except Exception as exc:
            processing_errors.append(
                {
                    "arquivo": archive.nome_arquivo,
                    "etapa": "extracao",
                    "codigo": None,
                    "parcial_utilizavel": False,
                    "mensagem": str(exc)[-2000:],
                }
            )
            extracted = analysis_dir / ("sem_extracao_" + archive.path.stem)
            falha_extracao = str(exc)
        try:
            extracted.mkdir(parents=True, exist_ok=True)
            failure = analyze_failure(
                archive=archive,
                extracted_dir=extracted,
                case_info=mds.case_info(archive.id_caso_teste),
                run_manifest=run_manifest.get(archive.path.stem.lower()),
            )
            if falha_extracao:
                failure = dataclasses.replace(
                    failure, erro_resumo="Nao foi possivel abrir o log compactado deste caso: " + falha_extracao.strip()[-800:]
                )
            failures.append(failure)
        except Exception as exc:
            processing_errors.append(
                {
                    "arquivo": archive.nome_arquivo,
                    "etapa": "analise",
                    "codigo": None,
                    "parcial_utilizavel": False,
                    "mensagem": str(exc)[-2000:],
                }
            )
            # Mesmo sem conseguir ler o conteudo, o caso falhou: entra so com o compactado original.
            vazio = analysis_dir / ("sem_analise_" + archive.path.stem)
            vazio.mkdir(parents=True, exist_ok=True)
            failure = analyze_failure(
                archive=archive, extracted_dir=vazio, case_info=mds.case_info(archive.id_caso_teste), run_manifest=None,
            )
            failures.append(dataclasses.replace(failure, erro_resumo="Nao foi possivel analisar o log compactado deste caso: " + str(exc)[-800:]))

    # O caso falhou mas o .RAR nao chegou: entra como falha (sem evidencia), senao a rodagem ficaria verde.
    for nome in compactados_ausentes(context.run_folder, archives):
        case_id, valid = extract_case_id(nome)
        mensagem = (
            "O log compactado deste caso nao chegou a pasta da rodagem (falha ao compactar ou ao copiar para o S:); "
            "procure o " + nome + " na pasta de compactados da VM."
        )
        processing_errors.append(
            {"arquivo": nome, "etapa": "compactado_ausente", "codigo": None, "parcial_utilizavel": False, "mensagem": mensagem}
        )
        failures.append(
            FailureAnalysis(
                archive=ArchiveInfo(
                    path=context.run_folder / nome, nome_arquivo=nome, extensao=Path(nome).suffix.lower(),
                    tamanho_bytes=0, modificado_em="", id_caso_teste=case_id, id_valido=valid,
                ),
                case_info=mds.case_info(case_id),
                extracted_dir=analysis_dir,
                status=STATUS_SEM_SINAL,
                erro_resumo=mensagem,
                comparisons=[],
                evidences=[],
            )
        )

    if sistema in ("Suprema", "Practice"):
        total_executed = total_pelos_casos(failures, tempos_casos)
    else:
        total_executed = read_total_tests_from_run_folder(context.run_folder)
        if total_executed is None and project_suite is not None:
            total_executed = project_suite.project_variable_int("wpSomaCasosExecutados")
        if total_executed is None:
            total_executed = mds.project_variable_int("wpSomaCasosExecutados")
    if archive_integrity and not archive_integrity["compactacao_completa"]:
        processing_errors.append(
            {
                "arquivo": archive_integrity["arquivo_origem"],
                "etapa": "integridade_compactacao",
                "codigo": None,
                "parcial_utilizavel": True,
                "mensagem": (
                    "Divergencia entre ocorrencias informadas pelo TC e arquivos compactados: "
                    f"TC={archive_integrity['total_ocorrencias_tc']}; "
                    f"compactados={archive_integrity['total_compactados']}; "
                    f"sem_compactado={archive_integrity['ocorrencias_sem_compactado']}; "
                    f"extras={archive_integrity['compactados_extras']}."
                ),
            }
        )
    delay_rows = parse_times_folder(times_folder)
    # Branch/revisao do projeto na VM (para a regravacao pelo dashboard); None se nao der.
    repository = working_copy_info(mds_do_projeto_da_pasta(mds_paths, context.run_folder).parent) if mds_paths else None
    # Importacao pelo dashboard: a branch escolhida la (a copia da D01 nao e a da VM que rodou; sem revisao).
    branch_url = os.environ.get("AGENT_TC_BRANCH_URL", "").strip()
    if branch_url:
        repository = {"url": branch_url, "raiz": (repository or {}).get("raiz") or "", "revisao": ""}
    payload = build_shadow_payload(
        context,
        failures,
        mds.hierarchy_rows(),
        total_executed=total_executed,
        total_archives=len(archives),
        processing_errors=processing_errors,
        delay_rows=delay_rows,
        archive_integrity=archive_integrity,
        repository=repository,
        measured_case_ids=[str(m["codigo_teste"]) for m in tempos_casos],
    )
    payload["tempos_casos"] = tempos_casos
    payload["rodagem"]["data_fim"] = fim_rodagem
    # Casos que o mod_xml.py desmarca em toda rodagem (o arquivo desta VM, o mesmo que o Jenkins usa).
    # A lista e do Unico: rodagem do Suprema ou do Practice nao mexe nela.
    if payload["rodagem"].get("sistema") == "Unico":
        payload["casos_desmarcados_mod_xml"] = ler_desmarcados_arquivo(mod_xml_path)
    if tempos_erro:
        payload.setdefault("erros_processamento", []).append(tempos_erro)
    if avisos_sistema:
        payload.setdefault("erros_processamento", []).extend(avisos_sistema)
    write_shadow_reports(report_dir, payload)
    return report_dir, payload

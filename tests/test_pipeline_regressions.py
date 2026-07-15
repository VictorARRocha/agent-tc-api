from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.config import RunContext, parse_run_context
from agent_tc_core.constants import STATUS_QUEBRA
from agent_tc_core.extractor import ArchiveInfo
from agent_tc_core.mds import CaseInfo, MdsCollection, split_mds_paths
from agent_tc_core.parser import analyze_failure
from agent_tc_core.payload import failure_id
from agent_tc_core.pipeline import (
    build_archive_integrity,
    read_occurrence_totals_from_project_suite,
    read_occurrence_totals_from_run_folder,
)
from agent_tc_core.project_suite import ProjectSuiteVariables
from agent_tc_core.sqlite_repository import SQLiteRepository
from agent_tc_core.supabase_repository import _deduplicate_rows


class PipelineRegressionTests(unittest.TestCase):
    def test_vm_is_canonical_lowercase(self):
        context = parse_run_context(
            run_folder=r"C:\logs\A08\PROXIMA1.26.7.0 09_07_2026 20_54_37",
            mds_path=r"C:\TC\Unico\Unico.mds",
            output_root=r"C:\logs\agent-tc",
            vm_name="A08",
        )
        self.assertEqual("a08", context.vm_name)
        self.assertTrue(context.id_rodagem.startswith("rod_a08_"))

    def test_practice_run_folder_can_be_date_only(self):
        context = parse_run_context(
            run_folder=r"S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados\A04\07_07_2026 18_55_32",
            mds_path=(
                r"C:\TC\TC12 - Simplificado\Cadastros\Practice Base Unificada.mds;"
                r"C:\TC\TC12 - Simplificado\Practice Antigo\Practice Bases Individuais.mds"
            ),
            output_root=r"S:\Teste automatico\Arquivos\AgenteTC\logs",
            vm_name="a04",
        )

        self.assertEqual("PRACTICE", context.versao)
        self.assertEqual("20260707_185532", context.stamp)
        self.assertEqual("rod_a04_PRACTICE_20260707_185532", context.id_rodagem)

    def test_practice_run_folder_uses_legacy_version_token(self):
        context = parse_run_context(
            run_folder=(
                r"S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados\A04"
                r"\Practice Base Agrupada 8.30a 01_07_2026 12_31_00"
            ),
            mds_path=(
                r"C:\TC\TC12 - Simplificado\Cadastros\Practice Base Unificada.mds;"
                r"C:\TC\TC12 - Simplificado\Practice Antigo\Practice Bases Individuais.mds"
            ),
            output_root=r"S:\Teste automatico\Arquivos\AgenteTC\logs",
            vm_name="a04",
        )

        self.assertEqual("8.30a", context.versao)
        self.assertEqual("rod_a04_8.30a_20260701_123100", context.id_rodagem)

    def test_suprema_run_folder_can_be_date_only(self):
        context = parse_run_context(
            run_folder=r"S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados\A03\15_07_2026 14_30_00",
            mds_path=r"C:\TC\tc12\PROJETO-TC12\Integracoes\Integracoes.mds",
            output_root=r"S:\Teste automatico\Arquivos\AgenteTC\logs",
            vm_name="a03",
        )

        self.assertEqual("SUPREMA", context.versao)
        self.assertEqual("rod_a03_SUPREMA_20260715_143000", context.id_rodagem)

    def test_failure_id_uses_archive_identity(self):
        context = RunContext(
            run_folder=Path("."),
            mds_path=Path("Unico.mds"),
            output_root=Path("."),
            vm_name="a08",
            versao="PROXIMA",
            versao_safe="PROXIMA",
            data_inicio="2026-07-09T20:54:37-03:00",
            stamp="20260709_205437",
            id_rodagem="rod_a08_PROXIMA_20260709_205437",
        )
        first = failure_id(context, "3.1.8.1.5.6", "3.1.8.1.5.6-09_07_2026-23_58_27.RAR", 1)
        second = failure_id(context, "3.1.8.1.5.6", "3.1.8.1.5.6-10_07_2026-23_58_27.RAR", 2)
        self.assertNotEqual(first, second)

    def test_supabase_batch_rows_are_deduplicated_by_conflict_key(self):
        rows = [{"id": "a", "value": 1}, {"id": "a", "value": 2}, {"id": "b", "value": 3}]
        result = _deduplicate_rows(rows, "id")
        self.assertEqual(2, len(result))
        self.assertEqual(2, result[0]["value"])

    def test_reads_tc_occurrence_totals_and_compares_archives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "TotalOcorrenciasTC.txt").write_text(
                "wpSomaErros=7\nwpSomaDiferencas=3\n",
                encoding="utf-8",
            )

            totals = read_occurrence_totals_from_run_folder(folder)
            integrity = build_archive_integrity(totals, total_archives=8)

        self.assertEqual(7, totals["total_erros_tc"])
        self.assertEqual(3, totals["total_diferencas_tc"])
        self.assertEqual("TotalOcorrenciasTC.txt", totals["arquivo_origem"])
        self.assertEqual(10, integrity["total_ocorrencias_tc"])
        self.assertEqual(8, integrity["total_compactados"])
        self.assertFalse(integrity["compactacao_completa"])
        self.assertEqual(2, integrity["ocorrencias_sem_compactado"])
        self.assertEqual(0, integrity["compactados_extras"])

    def test_reads_practice_project_suite_variables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pjs = Path(temp_dir) / "TestesVisualPractice.pjs"
            pjs.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<Root>
  <variables>
    <data>
      <Items>
        <Variable Name="wpSomaErros"><DefValue IntValue="4" /></Variable>
        <Variable Name="wpSomaDiferencas"><DefValue IntValue="6" /></Variable>
        <Variable Name="wpSomaCasosExecutados"><DefValue IntValue="42" /></Variable>
      </Items>
    </data>
  </variables>
</Root>""",
                encoding="utf-8",
            )

            project_suite = ProjectSuiteVariables(pjs)
            totals = read_occurrence_totals_from_project_suite(project_suite)

        self.assertEqual(42, project_suite.project_variable_int("wpSomaCasosExecutados"))
        self.assertEqual(4, totals["total_erros_tc"])
        self.assertEqual(6, totals["total_diferencas_tc"])
        self.assertEqual(str(pjs), totals["arquivo_origem"])

    def test_mds_collection_reads_multiple_practice_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            first = folder / "Practice Base Unificada.mds"
            second = folder / "Practice Bases Individuais.mds"
            first.write_text(
                '<Project><Node name="[19] Practice"><Node name="[19.1] Base"><Node name="[19.1.1] Caso Base" /></Node></Node></Project>',
                encoding="utf-8",
            )
            second.write_text(
                '<Project><Node name="[19] Practice Individual"><Node name="[19.100] Individuais"><Node name="[19.100.1] Caso Individual" /></Node></Node></Project>',
                encoding="utf-8",
            )

            collection = MdsCollection([first, second])
            rows = collection.hierarchy_rows()

        self.assertEqual("Caso Base", collection.case_info("19.1.1").nome_mds)
        self.assertEqual("Caso Individual", collection.case_info("19.100.1").nome_mds)
        self.assertEqual("Practice", {row["sistema"] for row in rows}.pop())
        self.assertEqual(5, len(rows))

    def test_integracoes_mds_is_suprema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mds = Path(temp_dir) / "Integracoes.mds"
            mds.write_text(
                '<Project><Node name="[16] Suprema"><Node name="[16.1] Importacoes"><Node name="[16.1.1] Caso Suprema" /></Node></Node></Project>',
                encoding="utf-8",
            )

            collection = MdsCollection([mds])
            rows = collection.hierarchy_rows()

        self.assertEqual("Suprema", collection.system)
        self.assertEqual("Suprema", {row["sistema"] for row in rows}.pop())
        self.assertEqual("Caso Suprema", collection.case_info("16.1.1").nome_mds)

    def test_mds_reads_practice_items_with_marker_before_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            mds = folder / "Practice Base Unificada.mds"
            mds.write_text(
                '<Project><Node name="[19] Practice"><Node name="[19.2] Folha"><Node name="*[19.2.3.1] Rescisao complementar" /></Node></Node></Project>',
                encoding="utf-8",
            )

            collection = MdsCollection([mds])

        self.assertEqual("Rescisao complementar", collection.case_info("19.2.3.1").nome_mds)

    def test_practice_logerro_txt_marks_test_break(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            archive_path = folder / "19.0.3 - 02-07-2026 20_28_41.RAR"
            archive_path.write_bytes(b"rar")
            extracted = folder / "extracted"
            extracted.mkdir()
            (extracted / "logErro.txt").write_text("Erro no Practice", encoding="utf-8")

            analysis = analyze_failure(
                archive=ArchiveInfo(
                    path=archive_path,
                    nome_arquivo=archive_path.name,
                    extensao=".rar",
                    tamanho_bytes=3,
                    modificado_em="2026-07-02T20:28:41",
                    id_caso_teste="19.0.3",
                    id_valido=True,
                ),
                extracted_dir=extracted,
                case_info=CaseInfo(
                    id_caso_teste="19.0.3",
                    nome_mds="Caso Practice",
                    grupo="Practice",
                    descricao="",
                    test_moniker="",
                    caminho_hierarquico="Practice > Caso Practice",
                    script_name="",
                    procedure_name="",
                ),
            )

        self.assertEqual(STATUS_QUEBRA, analysis.status)
        self.assertEqual("Erro no Practice", analysis.erro_resumo)
        self.assertTrue(any(evidence.path.name == "logErro.txt" for evidence in analysis.evidences))

    def test_splits_repeated_and_semicolon_mds_paths(self):
        paths = split_mds_paths(["a.mds;b.mds", "c.mds"])
        self.assertEqual(["a.mds", "b.mds", "c.mds"], [str(path) for path in paths])

    def test_sqlite_imports_repeated_case_and_exact_difference_links(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            payload = _payload_with_repeated_case()
            result = repository.import_payload(payload, source="test")

            self.assertTrue(result["verification"]["ok"])
            run = repository.run(payload["rodagem"]["id_rodagem"])
            self.assertEqual("analyzed", run["status"])
            self.assertEqual("a08", run["vm_name"])
            self.assertEqual(2, len(repository.failures(run["id"])))

            conn = repository.connect()
            try:
                rows = conn.execute(
                    "SELECT id, occurrence_id FROM report_differences ORDER BY id"
                ).fetchall()
                batch = conn.execute(
                    "SELECT status, summary_json FROM ingestion_batches LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(["failure-1", "failure-2"], [row["occurrence_id"] for row in rows])
            self.assertEqual("completed", batch["status"])
            self.assertIn('"verification"', batch["summary_json"])

    def test_api_marks_rerun_request_for_cancel(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            api = AgentTcApi(Path(temp_dir), repository=repository)

            status, created = api.route_post(
                "/rerun-requests",
                {
                    "id": "rerun-1",
                    "vm_name": "a08",
                    "version": "PROXIMA",
                    "casos_teste": "[3]",
                },
            )
            self.assertEqual(201, status)
            self.assertEqual("requested", created["status"])

            status, payload = api.route_post(
                "/rerun-requests/rerun-1/cancel",
                {"reason": "Rodagem enviada errada."},
            )
            self.assertEqual(202, status)
            self.assertTrue(payload["ok"])
            self.assertEqual("cancel_requested", payload["rerun_request"]["status"])
            self.assertEqual("cancel_requested", payload["rerun_request"]["execution_status"])

    def test_api_rejects_cancel_for_finished_rerun_request(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            api = AgentTcApi(Path(temp_dir), repository=repository)
            api.route_post(
                "/rerun-requests",
                {
                    "id": "rerun-2",
                    "vm_name": "a08",
                    "version": "PROXIMA",
                    "casos_teste": "[3]",
                    "status": "finalizado",
                },
            )

            status, payload = api.route_post("/rerun-requests/rerun-2/cancel", {})

            self.assertEqual(409, status)
            self.assertFalse(payload["ok"])
            self.assertEqual("rerun_request_not_cancellable", payload["error"])


def _payload_with_repeated_case():
    run_id = "rod_a08_PROXIMA_20260709_205437"
    failures = [
        {
            "id_falha": "failure-1",
            "id_caso_teste": "3.1.8.1.5.6",
            "nome_mds": "Caso repetido",
            "grupo": "Grupo",
            "arquivo_origem": "caso-09.rar",
            "tipo_detectado_python": "Diferenca",
            "erro_resumo": "",
        },
        {
            "id_falha": "failure-2",
            "id_caso_teste": "3.1.8.1.5.6",
            "nome_mds": "Caso repetido",
            "grupo": "Grupo",
            "arquivo_origem": "caso-10.rar",
            "tipo_detectado_python": "Diferenca",
            "erro_resumo": "",
        },
    ]
    return {
        "rodagem": {
            "id_rodagem": run_id,
            "sistema": "Unico",
            "versao": "PROXIMA",
            "vm_name": "A08",
            "data_inicio": "2026-07-09T20:54:37-03:00",
            "total_executed": 295,
            "total_archives": 2,
        },
        "modulo": {"id_modulo": "mod_contabil", "nome": "Contabil"},
        "falhas": failures,
        "evidencias": [],
        "diferencas_relatorio": [
            {"id_diferenca": "diff-1", "fk_falha": "failure-1", "id_caso_teste": "3.1.8.1.5.6", "resumo_diferenca": {}},
            {"id_diferenca": "diff-2", "fk_falha": "failure-2", "id_caso_teste": "3.1.8.1.5.6", "resumo_diferenca": {}},
        ],
        "agrupamentos_shadow": [
            {"id_cluster": "group-1", "titulo_causa": "Diferencas", "assinatura_tecnica": "diferencas", "status": "Diferenca", "falhas": ["failure-1", "failure-2"]}
        ],
        "testcase_hierarchy": [],
        "erros_processamento": [],
    }


if __name__ == "__main__":
    unittest.main()

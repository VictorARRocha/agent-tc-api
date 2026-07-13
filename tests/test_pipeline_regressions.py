from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.config import RunContext, parse_run_context
from agent_tc_core.payload import failure_id
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

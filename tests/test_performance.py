from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.performance import parse_times_file
from agent_tc_core.sqlite_repository import SQLiteRepository


class PerformanceTests(unittest.TestCase):
    def test_parse_times_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Tempos Folha.txt"
            path.write_text(
                "1.10.20.3 - 00:00:33 MAIS LENTO ---------------- Planilha: 00:04:59 | Atual: 00:05:32\n",
                encoding="utf-8",
            )
            rows = parse_times_file(path)

        self.assertEqual(1, len(rows))
        self.assertEqual("1.10.20.3", rows[0].testcase_node_id)
        self.assertEqual(299, rows[0].expected_seconds)
        self.assertEqual(332, rows[0].actual_seconds)
        self.assertEqual(33, rows[0].delay_seconds)

    def test_sqlite_imports_run_delays(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            run_id = "rod_a01_FOLHA_20260101_100000"
            payload = {
                "rodagem": {
                    "id_rodagem": run_id,
                    "sistema": "Unico",
                    "versao": "FOLHA",
                    "vm_name": "a01",
                    "data_inicio": "2026-01-01T10:00:00-03:00",
                    "total_archives": 0,
                    "total_executed": 0,
                },
                "modulo": {"id_modulo": "mod_folha", "nome": "Folha"},
                "falhas": [],
                "evidencias": [],
                "diferencas_relatorio": [],
                "agrupamentos_shadow": [],
                "testcase_hierarchy": [],
                "atrasos_rodagem": [
                    {
                        "id_atraso": "delay_1",
                        "codigo_teste": "1.10.20.3",
                        "nome_teste": "Caso lento",
                        "tempo_padrao_segundos": 299,
                        "tempo_atual_segundos": 332,
                        "delay_segundos": 33,
                        "status": "mais_lento",
                        "created_at": "2026-01-01T13:00:00+00:00",
                    }
                ],
            }

            repository = SQLiteRepository(Path(temp_dir) / "agent-tc.sqlite")
            repository.import_payload(payload)
            rows = repository.performance(run_id)

        self.assertEqual(1, len(rows))
        self.assertEqual("1.10.20.3", rows[0]["codigo_teste"])
        self.assertEqual(33, rows[0]["delay_segundos"])
        self.assertAlmostEqual(11.036, rows[0]["variacao_pct"], places=2)
        self.assertEqual("00:00:33", rows[0]["delay_detectado"])


if __name__ == "__main__":
    unittest.main()

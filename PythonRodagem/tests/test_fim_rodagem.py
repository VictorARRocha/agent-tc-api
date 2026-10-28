"""Fim da rodagem (tempo total na aba Resumo): hora em que o pipeline comeca; reprocessar nao muda."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from agent_tc_core.pipeline import run_shadow_pipeline
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _payload, _write_env


class FimRodagemTests(unittest.TestCase):
    def test_pipeline_marca_o_fim_quando_comeca(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            run_folder = root / "A07" / "PROXIMA 13_10_2026 11_12_01"
            run_folder.mkdir(parents=True)
            with zipfile.ZipFile(run_folder / "3.1.1-13_10_2026-11_12_01.zip", "w") as zf:
                zf.writestr("erro.txt", "quebrou")
            mds = root / "Unico.mds"
            mds.write_text('<Project><Node name="[3] Contabil"><Node name="[3.1.1] Caso" /></Node></Project>', encoding="utf-8")
            antes = datetime.now().astimezone()
            _r, payload = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "s", vm_name="a07")
        fim = datetime.fromisoformat(payload["rodagem"]["data_fim"])
        self.assertTrue(fim.isoformat().endswith("-03:00"))
        self.assertLessEqual(abs((fim - antes).total_seconds()), 60)

    def test_repositorios_guardam_o_primeiro_fim(self):
        for nome in ("sqlite", "canonical"):
            with self.subTest(nome), tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                root = Path(temp_dir)
                if nome == "sqlite":
                    repo = SQLiteRepository(root / "db.sqlite")
                    repo.initialize()
                else:
                    repo = InMemoryCanonicalRepository(_write_env(root))
                payload = _payload(root, ["falha_1"], run_id="rod_1")
                payload["rodagem"]["data_fim"] = "2026-10-13T21:30:00-03:00"
                repo.import_payload(payload)
                self.assertEqual("2026-10-13T21:30:00-03:00", str(repo.run("rod_1")["data_fim_rodagem"]))
                # Reprocessado no dia seguinte: o fim continua o da primeira analise.
                payload["rodagem"]["data_fim"] = "2026-10-14T09:00:00-03:00"
                repo.import_payload(payload)
                self.assertEqual("2026-10-13T21:30:00-03:00", str(repo.run("rod_1")["data_fim_rodagem"]))
                # Rodagem antiga, sem fim: continua sem.
                velha = _payload(root, ["falha_2"], run_id="rod_velha")
                repo.import_payload(velha)
                self.assertIsNone(repo.run("rod_velha")["data_fim_rodagem"])


if __name__ == "__main__":
    unittest.main()

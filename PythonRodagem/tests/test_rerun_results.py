"""Resultado das reexecucoes na rodagem de origem (passou, quebrou de novo, nao rodou, aguardando)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_ingest_robustness import RUN_ID, InMemoryCanonicalRepository, _payload, _write_env


class RerunResultsTests(unittest.TestCase):
    def test_cada_caso_da_reexecucao_mostra_o_que_deu(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = InMemoryCanonicalRepository(_write_env(root))
            repo.import_payload(_payload(root, ["falha_1", "falha_2", "falha_3"]))  # casos 3.1.1, 3.1.2, 3.1.3

            pedido = repo.record_rerun_request(
                {"source_run_id": RUN_ID, "vm_name": "a08", "versao": "PROXIMA", "casos_teste": "[3.1.1], [3.1.2], [3.1.3]"}
            )
            # Ainda na fila: tudo aguardando.
            self.assertEqual({"resultado": "aguardando"}, repo.rerun_results(RUN_ID)[0]["casos"]["3.1.1"])

            repo.tables["rerun_requests"][(pedido["id"],)].update(
                {"execution_status": "finalizado_ocorrencias", "created_at": "2026-07-10T12:00:00+00:00", "updated_at": "2026-07-10T14:00:00+00:00"}
            )
            # Build terminou mas a rodagem da reexecucao ainda nao foi importada.
            self.assertEqual({"resultado": "aguardando_importacao"}, repo.rerun_results(RUN_ID)[0]["casos"]["3.1.1"])

            # Reexecucao (A08, 10/07 09:05 -03:00): o 3.1.1 quebrou de novo, o 3.1.2 passou, o 3.1.3 nao rodou.
            reexec = _payload(root, ["falha_1"], run_id="rod_reexec")
            reexec["rodagem"]["data_inicio"] = "2026-07-10T09:05:00-03:00"
            repo.import_payload(reexec)
            for caso in ("3.1.1", "3.1.2"):
                repo.tables["testcase_times"][(caso,)] = {"id": caso, "node_id": caso, "run_id": "rod_reexec"}

            [resultado] = repo.rerun_results(RUN_ID)
            self.assertEqual("rod_reexec", resultado["rodagem_id"])
            self.assertEqual("quebrou_de_novo", resultado["casos"]["3.1.1"]["resultado"])
            self.assertEqual({"resultado": "passou"}, resultado["casos"]["3.1.2"])
            self.assertEqual({"resultado": "nao_rodou"}, resultado["casos"]["3.1.3"])
            # A rodagem da reexecucao nao tem reexecucoes dela.
            self.assertEqual([], repo.rerun_results("rod_reexec"))

    def test_rodagem_fora_da_janela_do_build_nao_e_ligada(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = InMemoryCanonicalRepository(_write_env(root))
            repo.import_payload(_payload(root, ["falha_1"]))
            pedido = repo.record_rerun_request({"source_run_id": RUN_ID, "vm_name": "a08", "versao": "PROXIMA", "casos_teste": "[3.1.1]"})
            repo.tables["rerun_requests"][(pedido["id"],)].update(
                {"execution_status": "finalizado_falha", "created_at": "2026-07-10T12:00:00+00:00", "updated_at": "2026-07-10T12:05:00+00:00"}
            )
            noturna = _payload(root, ["falha_1"], run_id="rod_noite")
            noturna["rodagem"]["data_inicio"] = "2026-07-10T22:00:00-03:00"
            repo.import_payload(noturna)
            [resultado] = repo.rerun_results(RUN_ID)
            self.assertIsNone(resultado["rodagem_id"])
            self.assertEqual({"resultado": "aguardando_importacao"}, resultado["casos"]["3.1.1"])


if __name__ == "__main__":
    unittest.main()

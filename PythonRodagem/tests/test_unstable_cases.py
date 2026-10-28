"""Caso instavel: alterna entre falhar e passar nas ultimas rodagens da mesma VM e modulo."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_ingest_robustness import InMemoryCanonicalRepository, _payload, _write_env

# Da mais antiga para a mais nova: casos que falharam em cada rodagem (todos os casos rodaram em todas).
SEQUENCIA = [["3.1.1"], [], ["3.1.1"], [], ["3.1.1", "3.1.2"], ["3.1.1", "3.1.2"]]


class UnstableCasesTests(unittest.TestCase):
    def test_so_o_caso_que_alterna_e_instavel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = InMemoryCanonicalRepository(_write_env(root))
            for n, casos in enumerate(SEQUENCIA):
                run_id = f"rod_{n}"
                payload = _payload(root, [f"f{n}_{i}" for i in range(len(casos))], run_id=run_id)
                for falha, caso in zip(payload["falhas"], casos):
                    falha["id_caso_teste"] = caso
                payload["rodagem"]["data_inicio"] = f"2026-10-2{n}T20:00:00-03:00"
                repo.import_payload(payload)
                for caso in ("3.1.1", "3.1.2"):
                    repo.tables["testcase_times"][(run_id, caso)] = {"id": run_id + caso, "node_id": caso, "run_id": run_id}
            # Outra VM nao entra na conta.
            outra = _payload(root, ["g1"], run_id="rod_outra_vm")
            outra["rodagem"].update({"vm_name": "a09", "data_inicio": "2026-10-25T21:00:00-03:00"})
            repo.import_payload(outra)

            instaveis = repo.unstable_cases("rod_5")
            self.assertEqual(["3.1.1"], list(instaveis))  # 3.1.2 passava e passou a falhar: nao e instavel
            self.assertEqual({"falhou": 4, "passou": 2}, {k: instaveis["3.1.1"][k] for k in ("falhou", "passou")})
            self.assertEqual(["falhou", "passou", "falhou", "passou", "falhou", "falhou"], instaveis["3.1.1"]["historico"])
            # Na rodagem antiga ainda nao havia historico suficiente.
            self.assertEqual({}, repo.unstable_cases("rod_2"))


if __name__ == "__main__":
    unittest.main()

"""Casos desmarcados pelo mod_xml.py viram "desativados" na aba Casos de teste (e saem quando tiram da lista)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.mod_xml_desmarcados import ler_desmarcados, ler_desmarcados_arquivo, motivo
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _payload, _write_env
from test_merge_api import User

MOD_XML = """
import socket
ct_desmarcar = []
if (len(argvs) == 6):
    ct_desmarcar = argvs[5].replace(' ', '').split(',')
if socket.gethostname() != 'a09':
    ct_desmarcar = ct_desmarcar + ['[1.3.4]', '[1.18]',
                                   '[6.6.2]', '[0.3]',
                                   '[3.1.8.2.1.2.3]', 'lixo', '[9.100]', '[1.3.4]']
if (argvs[4] == '1'):
    ct_desmarcar = ct_desmarcar + ['[2.5]', '[2.6]']
os.system('nao executa')
"""


class LeituraTests(unittest.TestCase):
    def test_le_a_lista_fixa_sem_executar(self):
        lido = ler_desmarcados(MOD_XML)
        self.assertEqual(["1.3.4", "1.18", "6.6.2", "0.3", "3.1.8.2.1.2.3", "9.100"], lido["casos"])
        self.assertEqual(["a09"], lido["exceto_vms"])

    def test_sem_lista_ou_arquivo_quebrado(self):
        self.assertIsNone(ler_desmarcados("x = 1"))
        self.assertIsNone(ler_desmarcados("if socket.gethostname() != 'a09':\n    pass\n"))
        self.assertIsNone(ler_desmarcados("def (:"))
        self.assertIsNone(ler_desmarcados_arquivo(Path(tempfile.gettempdir()) / "nao_existe_mod_xml.py"))

    def test_motivo(self):
        self.assertEqual("Desmarcado em toda rodagem pelo mod_xml.py (exceto na A09)", motivo(["a09"]))
        self.assertEqual("Desmarcado em toda rodagem pelo mod_xml.py", motivo([]))


class SincronizacaoTests(unittest.TestCase):
    def _repos(self, root: Path):
        sqlite = SQLiteRepository(root / "db.sqlite")
        sqlite.initialize()
        yield sqlite
        yield InMemoryCanonicalRepository(_write_env(root))

    def _importa(self, repo, root, run_id, casos):
        payload = _payload(root, ["falha_" + run_id], run_id=run_id)
        if casos is not None:
            payload["casos_desmarcados_mod_xml"] = {"casos": casos, "exceto_vms": ["a09"]}
        repo.import_payload(payload)

    def test_entra_sai_e_nao_mexe_no_manual(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            for repo in self._repos(root):
                with self.subTest(type(repo).__name__):
                    repo.save_testcase_disabled({"id": "3.1.1", "node_id": "3.1.1", "module_slug": "contabil", "reason": "manual",
                                                 "disabled_by": "ana", "disabled_at": "2026-10-01T10:00:00+00:00"})
                    self._importa(repo, root, "rod_1", ["1.3.4", "3.1.1", "9.100"])
                    linhas = {r["node_id"]: r for r in repo.testcase_disabled(None)}
                    self.assertEqual({"1.3.4", "3.1.1", "9.100"}, set(linhas))
                    self.assertEqual(("mod_xml", "folha"), (linhas["1.3.4"]["disabled_by"], linhas["1.3.4"]["module_slug"]))
                    self.assertIn("exceto na A09", linhas["9.100"]["reason"])
                    self.assertEqual(("ana", "manual"), (linhas["3.1.1"]["disabled_by"], linhas["3.1.1"]["reason"]))  # o manual fica

                    # Tiraram o 9.100 do mod_xml: sai. O manual continua mesmo fora da lista.
                    self._importa(repo, root, "rod_2", ["1.3.4"])
                    self.assertEqual({"1.3.4", "3.1.1"}, {r["node_id"] for r in repo.testcase_disabled(None)})

                    # VM sem a lista (antiga ou arquivo nao lido): nada muda.
                    self._importa(repo, root, "rod_3", None)
                    self.assertEqual({"1.3.4", "3.1.1"}, {r["node_id"] for r in repo.testcase_disabled(None)})
                    for r in repo.testcase_disabled(None):
                        repo.delete_testcase_disabled(r["node_id"])


class ApiTests(unittest.TestCase):
    def test_dashboard_nao_reativa_nem_remarca_o_que_vem_do_mod_xml(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            repo = SQLiteRepository(root / "db.sqlite")
            repo.initialize()
            api = AgentTcApi(root, repository=repo, env_path=_write_env(root), auth_validator=User("ana", "admin"))
            payload = _payload(root, ["falha_1"], run_id="rod_1")
            payload["casos_desmarcados_mod_xml"] = {"casos": ["3.1.9"], "exceto_vms": ["a09"]}
            repo.import_payload(payload)
            status, body = api.route_post("/testcase-disabled/3.1.9/delete", {}, "Bearer x")
            self.assertEqual((409, "testcase_disabled_by_mod_xml"), (status, body["error"]))
            status, body = api.route_post("/testcase-disabled", {"node_id": "3.1.9", "motivo": "x"}, "Bearer x")
            self.assertEqual(409, status)
            self.assertEqual("mod_xml", repo.testcase_disabled_item("3.1.9")["disabled_by"])
            # Os marcados a mao continuam normais.
            self.assertEqual(201, api.route_post("/testcase-disabled", {"node_id": "3.1.2"}, "Bearer x")[0])
            self.assertEqual(200, api.route_post("/testcase-disabled/3.1.2/delete", {}, "Bearer x")[0])


class PipelineTests(unittest.TestCase):
    def test_pipeline_manda_a_lista_do_mod_xml_da_vm(self):
        import zipfile

        from agent_tc_core.pipeline import run_shadow_pipeline

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            run_folder = root / "A07" / "PROXIMA 13_10_2026 11_12_01"
            run_folder.mkdir(parents=True)
            with zipfile.ZipFile(run_folder / "3.1.1-13_10_2026-11_12_01.zip", "w") as zf:
                zf.writestr("erro.txt", "quebrou")
            mds = root / "Unico.mds"
            mds.write_text('<Project><Node name="[3] Contabil"><Node name="[3.1.1] Caso" /></Node></Project>', encoding="utf-8")
            mod_xml = root / "mod_xml.py"
            mod_xml.write_text(MOD_XML, encoding="utf-8")
            _r, payload = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "s", vm_name="a07", mod_xml_path=mod_xml)
            _r, sem = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "s2", vm_name="a07", mod_xml_path=root / "x.py")
        self.assertEqual(6, len(payload["casos_desmarcados_mod_xml"]["casos"]))
        self.assertIsNone(sem["casos_desmarcados_mod_xml"])


if __name__ == "__main__":
    unittest.main()

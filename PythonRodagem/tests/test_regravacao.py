from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_tc_core import svn_info
from agent_tc_core.extractor import ArchiveInfo
from agent_tc_core.mds import CaseInfo
from agent_tc_core.parser import analyze_failure, read_regravacao_manifest, read_run_regravacao_manifest
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, RUN_ID, _payload, _write_env

SVN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<info>
<entry kind="dir" path="C:\\TC\\Unico" revision="23463">
<url>https://192.168.10.199:8443/svn/testcomplete/unico/ProjetoUnico/branches/minha-branch</url>
<repository><root>https://192.168.10.199:8443/svn/testcomplete</root></repository>
</entry>
</info>
"""


def _archive(path: Path) -> ArchiveInfo:
    return ArchiveInfo(path=path, nome_arquivo=path.name, extensao=".rar", tamanho_bytes=1,
                       modificado_em="2026-10-05T15:00:00", id_caso_teste="9.1.4.2.5", id_valido=True)


def _case() -> CaseInfo:
    return CaseInfo("9.1.4.2.5", "Caso", "Grupo", "", "", "", "", "")


class ManifestTests(unittest.TestCase):
    def test_reads_lines_with_bom_and_ignores_invalid_ones(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "Regravacao.txt"
            manifest.write_bytes(
                "\ufeffRelTarefas_248408 balancete_Atual.txt|Files\\Tarefas\\Relatório\\RelTarefas_248408 balancete.txt|S\r\n"
                "\ufeffRelTarefas_248408 conferenci_Atual.txt|Files\\Tarefas\\RelTarefas_248408 conferenci.txt|N\r\n"
                "linha quebrada sem separadores\r\n".encode("utf-8")
            )
            result = read_regravacao_manifest([manifest])
        self.assertEqual(
            {"caminho_base": "Files\\Tarefas\\Relatório\\RelTarefas_248408 balancete.txt", "confirmado": True},
            result["reltarefas_248408 balancete_atual.txt"],
        )
        self.assertFalse(result["reltarefas_248408 conferenci_atual.txt"]["confirmado"])
        self.assertEqual(2, len(result))

    def test_reads_ansi_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "regravacao.txt"
            manifest.write_bytes("Rel_Atual.txt|Files\\Relatórios\\Rel.txt|S\r\n".encode("cp1252"))
            result = read_regravacao_manifest([manifest])
        self.assertEqual("Files\\Relatórios\\Rel.txt", result["rel_atual.txt"]["caminho_base"])

    def test_comparisons_get_their_manifest_line_including_numbered_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name, text in [
                ("Rel_Antigo.txt", "a"), ("Rel_Atual.txt", "b"),
                ("Rel_02_Antigo.txt", "c"), ("Rel_02_Atual.txt", "d"),
                ("Outro_Antigo.txt", "e"), ("Outro_Atual.txt", "f"),
            ]:
                (root / name).write_text(text, encoding="utf-8")
            (root / "Regravacao.txt").write_text(
                "Rel_Atual.txt|Files\\A\\Rel.txt|S\nRel_02_Atual.txt|Files\\B\\Rel.txt|N\n", encoding="utf-8"
            )
            analysis = analyze_failure(archive=_archive(root / "caso.rar"), extracted_dir=root, case_info=_case())

        by_name = {c.atual.name: c.resumo.get("regravacao") for c in analysis.comparisons}
        self.assertEqual({"caminho_base": "Files\\A\\Rel.txt", "confirmado": True}, by_name["Rel_Atual.txt"])
        self.assertEqual({"caminho_base": "Files\\B\\Rel.txt", "confirmado": False}, by_name["Rel_02_Atual.txt"])
        self.assertIsNone(by_name["Outro_Atual.txt"])  # sem linha no manifesto (rodagem antiga, por ex.)
        # O manifesto nao vira evidencia.
        self.assertNotIn("Regravacao.txt", [e.path.name for e in analysis.evidences])

    def test_without_manifest_nothing_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Rel_Antigo.txt").write_text("a", encoding="utf-8")
            (root / "Rel_Atual.txt").write_text("b", encoding="utf-8")
            analysis = analyze_failure(archive=_archive(root / "caso.rar"), extracted_dir=root, case_info=_case())
        self.assertNotIn("regravacao", analysis.comparisons[0].resumo)


class RunManifestTests(unittest.TestCase):
    """Manifesto fora do .rar (AgenteTC_Regravacao.txt, oculto, na pasta da rodagem)."""

    def test_reads_lines_per_archive_last_line_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "AgenteTC_Regravacao.txt").write_bytes((
                "﻿9.1.4-08_10_2026-10_00_00|Rel_Atual.txt|Files\\Relatórios\\Rel.txt|N\r\n"
                "linha invalida\r\n|sem rar|Files\\x|S\r\n"
                "9.1.4-08_10_2026-10_00_00|Rel_Atual.txt|Files\\Relatórios\\Rel.txt|S\r\n"
                "2.1-08_10_2026-10_00_00|Outro_Atual.txt|Files\\Fiscal\\Outro.txt|N\r\n"
            ).encode("utf-8"))
            result = read_run_regravacao_manifest(folder)
            self.assertEqual({}, read_run_regravacao_manifest(folder / "nao_existe"))
        self.assertEqual({"9.1.4-08_10_2026-10_00_00", "2.1-08_10_2026-10_00_00"}, set(result))
        self.assertEqual({"caminho_base": "Files\\Relatórios\\Rel.txt", "confirmado": True},
                         result["9.1.4-08_10_2026-10_00_00"]["rel_atual.txt"])
        self.assertFalse(result["2.1-08_10_2026-10_00_00"]["outro_atual.txt"]["confirmado"])

    def test_old_manifest_inside_the_archive_still_works_and_new_one_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("Rel_Antigo.txt", "Rel_Atual.txt", "Outro_Antigo.txt", "Outro_Atual.txt"):
                (root / name).write_text(name, encoding="utf-8")
            (root / "Regravacao.txt").write_text("Rel_Atual.txt|Files\\A\\Rel.txt|N\nOutro_Atual.txt|Files\\A\\Outro.txt|S\n", encoding="utf-8")
            novo = {"rel_atual.txt": {"caminho_base": "Files\\A\\Rel.txt", "confirmado": True}}
            analysis = analyze_failure(archive=_archive(root / "caso.rar"), extracted_dir=root, case_info=_case(), run_manifest=novo)
        by_name = {c.atual.name: c.resumo.get("regravacao") for c in analysis.comparisons}
        self.assertTrue(by_name["Rel_Atual.txt"]["confirmado"])  # o novo vale
        self.assertTrue(by_name["Outro_Atual.txt"]["confirmado"])  # so no antigo: continua valendo

    def test_pipeline_reads_the_hidden_manifest_next_to_the_archives(self):
        import ctypes
        import zipfile

        from agent_tc_core.pipeline import run_shadow_pipeline

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            run_folder = root / "A07" / "PROXIMA 08_10_2026 10_00_00"
            run_folder.mkdir(parents=True)
            stem = "9.1.4.2.5-08_10_2026-10_00_00"
            with zipfile.ZipFile(run_folder / (stem + ".zip"), "w") as zf:
                zf.writestr("Rel_Antigo.txt", "linha 1\nlinha 2\n")
                zf.writestr("Rel_Atual.txt", "linha 1\nlinha 3\n")
            manifest = run_folder / "AgenteTC_Regravacao.txt"
            manifest.write_text(f"{stem}|Rel_Atual.txt|Files\\Gestao\\Rel.txt|S\r\n", encoding="utf-8")
            if hasattr(ctypes, "windll"):
                ctypes.windll.kernel32.SetFileAttributesW(str(manifest), 0x2)  # oculto, como o TC grava
            mds = root / "Unico.mds"
            mds.write_text('<Project><Node name="[9] Gestao"><Node name="[9.1.4.2.5] Caso" /></Node></Project>', encoding="utf-8")

            _report, payload = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "saida", vm_name="a07")

        resumos = [d.get("resumo_diferenca") or {} for d in payload["diferencas_relatorio"]]
        self.assertEqual([{"caminho_base": "Files\\Gestao\\Rel.txt", "confirmado": True}], [r.get("regravacao") for r in resumos])
        # So o .zip vira caso; o manifesto da pasta nao e um compactado.
        self.assertEqual(1, len(payload["falhas"]))


class SvnInfoTests(unittest.TestCase):
    def test_parses_url_root_and_revision(self):
        self.assertEqual(
            {
                "url": "https://192.168.10.199:8443/svn/testcomplete/unico/ProjetoUnico/branches/minha-branch",
                "raiz": "https://192.168.10.199:8443/svn/testcomplete",
                "revisao": "23463",
            },
            svn_info.parse_svn_info_xml(SVN_XML),
        )
        self.assertIsNone(svn_info.parse_svn_info_xml("nao e xml"))
        self.assertIsNone(svn_info.parse_svn_info_xml("<info></info>"))

    def test_never_breaks_the_analysis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            # Pasta sem .svn
            self.assertIsNone(svn_info.working_copy_info(folder))
            (folder / ".svn").mkdir()
            with mock.patch.object(svn_info, "find_svn", return_value=None):
                self.assertIsNone(svn_info.working_copy_info(folder))
            with mock.patch.object(svn_info, "find_svn", return_value="svn"), \
                 mock.patch.object(svn_info.subprocess, "run", side_effect=subprocess.TimeoutExpired("svn", 20)):
                self.assertIsNone(svn_info.working_copy_info(folder))
            with mock.patch.object(svn_info, "find_svn", return_value="svn"), \
                 mock.patch.object(svn_info.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 1, b"", b"E155007")):
                self.assertIsNone(svn_info.working_copy_info(folder))
            with mock.patch.object(svn_info, "find_svn", return_value="svn"), \
                 mock.patch.object(svn_info.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 0, SVN_XML.encode("utf-8"), b"")):
                self.assertEqual("23463", svn_info.working_copy_info(folder)["revisao"])

    def test_mds_in_a_subfolder_of_the_working_copy(self):
        # Suprema: .svn em C:\TC\tc12 e o .mds em PROJETO-TC12\Integracoes.
        with tempfile.TemporaryDirectory() as temp_dir:
            projeto = Path(temp_dir) / "PROJETO-TC12" / "Integracoes"
            projeto.mkdir(parents=True)
            self.assertIsNone(svn_info.working_copy_info(projeto))  # sem .svn em lugar nenhum
            (Path(temp_dir) / ".svn").mkdir()
            ok = subprocess.CompletedProcess([], 0, SVN_XML.encode("utf-8"), b"")
            with mock.patch.object(svn_info, "find_svn", return_value="svn"), \
                 mock.patch.object(svn_info.subprocess, "run", return_value=ok) as run:
                self.assertEqual("23463", svn_info.working_copy_info(projeto)["revisao"])
            # O svn info roda na pasta do projeto (a URL dela e a que tem o Stores), nao na raiz.
            self.assertEqual(str(projeto), run.call_args[0][0][-1])


class RunRepositoryColumnsTests(unittest.TestCase):
    def _repositories(self, root: Path):
        yield SQLiteRepository(root / "agent-tc.sqlite")
        yield InMemoryCanonicalRepository(_write_env(root))

    def test_import_keeps_svn_origin_and_api_exposes_it(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            for repository in self._repositories(root):
                repository.initialize()
                payload = _payload(root, ["falha_1"])
                payload["rodagem"].update({
                    "repositorio_url": "https://svn/ProjetoUnico/branches/minha-branch",
                    "repositorio_raiz": "https://svn",
                    "repositorio_revisao": "23463",
                })
                repository.import_payload(payload)
                run = repository.run(RUN_ID)
                self.assertEqual("https://svn/ProjetoUnico/branches/minha-branch", run["repository_url"])
                self.assertEqual("https://svn", run["repository_root"])
                self.assertEqual("23463", run["repository_revision"])
                # A lista de rodagens (seletor da tela Regravar arquivos) tambem traz a branch do TC.
                listed = next(r for r in repository.runs() if r["id_rodagem"] == RUN_ID)
                self.assertEqual("https://svn/ProjetoUnico/branches/minha-branch", listed["repository_url"])

    def test_payload_without_svn_origin_still_imports(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            for repository in self._repositories(root):
                repository.initialize()
                repository.import_payload(_payload(root, ["falha_1"]))
                self.assertIsNone(repository.run(RUN_ID).get("repository_url"))


if __name__ == "__main__":
    unittest.main()

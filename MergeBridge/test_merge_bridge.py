"""Testes do MergeBridge contra um repositorio SVN local de verdade (svnadmin + file:///).

Uso: py -3 -m unittest test_merge_bridge   (na pasta do MergeBridge)
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

import merge_bridge as mb

BIN = mb.TORTOISE_BIN
SCRIPT = "procedure Rotina;\r\nbegin\r\n  Log.Message('original');\r\nend;\r\n"


class FakeApi:
    def __init__(self, requests_by_id: dict[str, dict] | None = None):
        self.requests = requests_by_id or {}
        self.updates: list[tuple[str, dict]] = []
        self.branches: list[dict] = []

    def pending(self):
        return [r for r in self.requests.values() if r["status"] in ("previa_solicitada", "solicitado")]

    def processing(self):
        return [r for r in self.requests.values() if r["status"] in ("previa_processando", "processando")]

    def claim(self, request_id):
        row = self.requests[request_id]
        nxt = {"previa_solicitada": "previa_processando", "solicitado": "processando"}.get(row["status"])
        if not nxt:
            return None
        row["status"] = nxt
        return row

    def update(self, request_id, fields):
        self.requests[request_id].update(fields)
        self.updates.append((request_id, fields))

    def publish_branches(self, branches):
        self.branches = branches


class MergeBridgeSvnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        repo = root / "repo"
        subprocess.run([str(BIN / "svnadmin.exe"), "create", str(repo)], check=True)
        self.root_url = "file:///" + str(repo).replace("\\", "/")
        self.project = self.root_url + "/unico/ProjetoUnico"
        self.unico = self.project + "/Unico"
        src = root / "src"
        for rel, content in (("Script/Rotinas.sd", SCRIPT), ("Stores/Files/A.txt", "base\r\n")):
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(content, encoding="cp1252", newline="")
        self._svn("import", "-q", "-m", "inicial", str(src), self.unico)
        self._svn("mkdir", "-q", "-m", "branches", self.project + "/branches")
        self.proxima = self._branch("Proxima10_0")
        self.folha = self._branch("Folha_sustentação")
        self.config = mb.load_config({
            "AGENT_TC_BRIDGE_TOKEN": "x", "MERGE_PROJECT_URL": self.project, "MERGE_TRUNK_NAME": "Unico",
            "MERGE_WC_DIR": str(root / "wc"), "MERGE_COMMIT": "1",
        })
        self.svn = mb.Svn(self.config)

    def tearDown(self):
        self.temp.cleanup()

    # -------- utilitarios

    def _svn(self, *args):
        return subprocess.run([str(BIN / "svn.exe"), "--non-interactive", *args], check=True, capture_output=True)

    def _branch(self, name: str) -> str:
        url = self.project + "/branches/" + quote(name)
        self._svn("copy", "-q", "-m", "cria " + name, self.unico, url)
        return url

    def _put(self, url: str, rel: str, content: str, message: str = "alteracao") -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "f"
            local.write_text(content, encoding="cp1252", newline="")
            subprocess.run([str(BIN / "svnmucc.exe"), "--non-interactive", "-m", message, "put", str(local), url + "/" + rel],
                           check=True, capture_output=True)

    def _cat(self, url: str, rel: str) -> str:
        return self._svn("cat", url + "/" + rel).stdout.decode("cp1252")

    def _request(self, source: str, target: str, rid: str = "m1", status: str = "previa_solicitada") -> dict:
        names = {self.unico: "Unico", self.proxima: "Proxima10_0", self.folha: "Folha_sustentação"}
        return {"id": rid, "status": status, "requested_by": "ana", "source_url": source, "target_url": target,
                "source_name": names[source], "target_name": names[target], "target_kind": "trunk" if target == self.unico else "branch",
                "commit_message": None, "preview_json": {}}

    def _preview_and_confirm(self, api: FakeApi, rid: str = "m1") -> dict:
        mb.run_once(api, self.svn, self.config)
        row = api.requests[rid]
        self.assertEqual("previa_pronta", row["status"], row.get("error_message"))
        row["status"] = "solicitado"  # confirmacao no dashboard
        mb.run_once(api, self.svn, self.config)
        return row

    # -------- testes

    def test_branch_list_has_trunk_and_branches_including_accents(self):
        branches = mb.list_branches(self.svn, self.config)
        self.assertEqual(["Unico", "Folha_sustentação", "Proxima10_0"], [b["name"] for b in branches])
        self.assertEqual("trunk", branches[0]["kind"])
        self.assertEqual(self.folha, branches[1]["url"])
        self.assertTrue(branches[2]["last_revision"])
        self.assertTrue(mb.allowed_url(self.folha, self.config))
        self.assertFalse(mb.allowed_url(self.project + "/branches/x/sub", self.config))
        self.assertFalse(mb.allowed_url("file:///outro/repo/Unico", self.config))

    def test_preview_shows_pending_revisions_and_files_then_merge_commits(self):
        self._put(self.unico, "Script/Rotinas.sd", SCRIPT.replace("original", "nova regra"), "ajuste da rotina 252547")
        self._put(self.unico, "Stores/Files/A.txt", "base nova\r\n", "regrava A")
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})

        mb.run_once(api, self.svn, self.config)
        row = api.requests["m1"]
        self.assertEqual("previa_pronta", row["status"], row.get("error_message"))
        preview = row["preview_json"]
        self.assertEqual(2, preview["total_revisoes"])
        self.assertEqual(["regrava A", "ajuste da rotina 252547"], [r["mensagem"] for r in preview["revisoes"]])
        self.assertEqual({"Script/Rotinas.sd", "Stores/Files/A.txt"}, {f["caminho"] for f in preview["arquivos"]})
        self.assertEqual([], preview["conflitos"])
        self.assertIn("Merge de Unico para Proxima10_0", preview["mensagem_padrao"])
        self.assertIn("Pedido: {pedido}", preview["mensagem_padrao"])
        self.assertIn("nova regra", self._cat(self.unico, "Script/Rotinas.sd"))
        self.assertNotIn("nova regra", self._cat(self.proxima, "Script/Rotinas.sd"))  # previa nao grava

        row["status"] = "solicitado"
        row["commit_message"] = "Atualiza Proxima com a Unico (pedido {pedido})"
        mb.run_once(api, self.svn, self.config)
        self.assertEqual("concluido", row["status"], row.get("error_message"))
        self.assertIn("nova regra", self._cat(self.proxima, "Script/Rotinas.sd"))
        self.assertEqual("base nova\r\n", self._cat(self.proxima, "Stores/Files/A.txt"))
        log = self._svn("log", "-l", "1", "--xml", self.proxima).stdout.decode("utf-8")
        self.assertIn("Atualiza Proxima com a Unico (pedido m1)", log)
        self.assertNotIn("(AgenteTC pedido", log)  # o numero ja estava na mensagem
        self.assertIn(f"revision=\"{row['svn_revision']}\"", log)
        mergeinfo = self._svn("propget", "svn:mergeinfo", self.proxima).stdout.decode("utf-8")
        self.assertIn("/unico/ProjetoUnico/Unico:", mergeinfo)

        # Nada novo na origem: a proxima previa diz que o destino ja esta atualizado.
        api.requests["m2"] = self._request(self.unico, self.proxima, rid="m2")
        mb.run_once(api, self.svn, self.config)
        self.assertEqual("sem_alteracoes", api.requests["m2"]["status"])

    def test_branch_into_unico_and_branch_into_branch(self):
        self._put(self.folha, "Stores/Files/A.txt", "folha\r\n", "ajuste so na folha")
        api = FakeApi({"m1": self._request(self.folha, self.unico)})
        self._preview_and_confirm(api)
        self.assertEqual("concluido", api.requests["m1"]["status"], api.requests["m1"].get("error_message"))
        self.assertEqual("folha\r\n", self._cat(self.unico, "Stores/Files/A.txt"))

        api.requests["m2"] = self._request(self.folha, self.proxima, rid="m2")
        self._preview_and_confirm(api, "m2")
        self.assertEqual("concluido", api.requests["m2"]["status"], api.requests["m2"].get("error_message"))
        self.assertEqual("folha\r\n", self._cat(self.proxima, "Stores/Files/A.txt"))

    def test_conflict_in_preview_writes_nothing_and_lists_files(self):
        self._put(self.unico, "Script/Rotinas.sd", SCRIPT.replace("original", "unico"), "unico mexe")
        self._put(self.proxima, "Script/Rotinas.sd", SCRIPT.replace("original", "proxima"), "proxima mexe")
        before = self._svn("info", "--show-item", "revision", self.root_url).stdout
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})

        mb.run_once(api, self.svn, self.config)

        row = api.requests["m1"]
        self.assertEqual("conflito", row["status"])
        self.assertEqual([{"caminho": "Script/Rotinas.sd", "tipo": "texto"}], row["preview_json"]["conflitos"])
        self.assertIn("manualmente", row["error_message"])
        self.assertEqual(before, self._svn("info", "--show-item", "revision", self.root_url).stdout)
        self.assertIn("proxima", self._cat(self.proxima, "Script/Rotinas.sd"))
        self.assertFalse(mb.has_local_changes(self.svn, Path(str(self.config["wc_dir"]))))  # copia limpa

    def test_paths_are_relative_to_the_copy_even_when_it_is_under_the_current_folder(self):
        # O svn mostra caminhos relativos a pasta atual quando a copia esta dentro dela.
        self._put(self.unico, "Script/Rotinas.sd", SCRIPT.replace("original", "unico"), "unico mexe")
        self._put(self.proxima, "Script/Rotinas.sd", SCRIPT.replace("original", "proxima"), "proxima mexe")
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})
        previous = Path.cwd()
        os.chdir(self.temp.name)
        try:
            mb.run_once(api, self.svn, self.config)
        finally:
            os.chdir(previous)
        self.assertEqual([{"caminho": "Script/Rotinas.sd", "tipo": "texto"}], api.requests["m1"]["preview_json"]["conflitos"])

    def test_conflict_appearing_after_preview_reverts_and_writes_nothing(self):
        self._put(self.unico, "Script/Rotinas.sd", SCRIPT.replace("original", "unico"), "unico mexe")
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})
        mb.run_once(api, self.svn, self.config)
        row = api.requests["m1"]
        self.assertEqual("previa_pronta", row["status"])
        # Alguem altera a mesma linha no destino entre a previa e a confirmacao.
        self._put(self.proxima, "Script/Rotinas.sd", SCRIPT.replace("original", "proxima"), "proxima mexe")
        before = self._svn("info", "--show-item", "revision", self.root_url).stdout

        row["status"] = "solicitado"
        mb.run_once(api, self.svn, self.config)

        self.assertEqual("conflito", row["status"])
        self.assertEqual(["Script/Rotinas.sd"], [c["caminho"] for c in row["result_json"]["conflitos"]])
        self.assertEqual(before, self._svn("info", "--show-item", "revision", self.root_url).stdout)
        self.assertFalse(mb.has_local_changes(self.svn, Path(str(self.config["wc_dir"]))))

    def test_merges_only_up_to_the_previewed_revision(self):
        self._put(self.unico, "Stores/Files/A.txt", "versao da previa\r\n", "entra")
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})
        mb.run_once(api, self.svn, self.config)
        self._put(self.unico, "Stores/Files/A.txt", "depois da previa\r\n", "nao entra")
        api.requests["m1"]["status"] = "solicitado"
        mb.run_once(api, self.svn, self.config)
        self.assertEqual("concluido", api.requests["m1"]["status"], api.requests["m1"].get("error_message"))
        self.assertEqual("versao da previa\r\n", self._cat(self.proxima, "Stores/Files/A.txt"))

    def test_simulation_mode_merges_and_reverts(self):
        config = {**self.config, "commit": False}
        self._put(self.unico, "Stores/Files/A.txt", "nova\r\n")
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})
        mb.run_once(api, self.svn, config)
        api.requests["m1"]["status"] = "solicitado"
        mb.run_once(api, self.svn, config)
        row = api.requests["m1"]
        self.assertEqual("erro", row["status"])
        self.assertTrue(row["result_json"]["simulacao"])
        self.assertIn("simulacao", row["error_message"].lower())
        self.assertEqual("base\r\n", self._cat(self.proxima, "Stores/Files/A.txt"))

    def test_resuming_after_crash_does_not_merge_twice(self):
        self._put(self.unico, "Stores/Files/A.txt", "nova\r\n")
        api = FakeApi({"m1": self._request(self.unico, self.proxima)})
        row = self._preview_and_confirm(api)
        revision = row["svn_revision"]
        # Bridge caiu depois do commit e antes de avisar a API: o pedido volta em "processando".
        row.update(status="processando", svn_revision=None)
        mb.handle(row, api, self.svn, self.config)
        self.assertEqual(("concluido", revision), (row["status"], row["svn_revision"]))
        self.assertTrue(row["result_json"]["retomado"])

    def test_rejects_urls_outside_the_project(self):
        request = {**self._request(self.unico, self.proxima), "target_url": "file:///outro/repo/branches/x"}
        api = FakeApi({"m1": request})
        mb.run_once(api, self.svn, self.config)
        self.assertEqual("erro", api.requests["m1"]["status"])
        self.assertIn("fora do projeto", api.requests["m1"]["error_message"])

    def test_direct_merge_without_preview_takes_the_source_as_it_is_now(self):
        self._put(self.unico, "Stores/Files/A.txt", "primeira\r\n", "entra 1")
        self._put(self.unico, "Stores/Files/A.txt", "ultima\r\n", "entra 2")
        api = FakeApi({"m1": {**self._request(self.unico, self.proxima, status="solicitado"), "commit_message": "Atualiza a Proxima"}})
        mb.run_once(api, self.svn, self.config)
        row = api.requests["m1"]
        self.assertEqual("concluido", row["status"], row.get("error_message"))
        self.assertEqual("ultima\r\n", self._cat(self.proxima, "Stores/Files/A.txt"))
        self.assertEqual(2, row["result_json"]["total_revisoes"])
        log = self._svn("log", "-l", "1", "--xml", self.proxima).stdout.decode("utf-8")
        self.assertIn("Atualiza a Proxima", log)
        self.assertIn("(AgenteTC pedido m1)", log)

    def test_direct_merge_without_message_uses_the_default_with_revisions(self):
        self._put(self.unico, "Stores/Files/A.txt", "nova\r\n", "entra")
        api = FakeApi({"m1": self._request(self.unico, self.folha, status="solicitado")})
        mb.run_once(api, self.svn, self.config)
        self.assertEqual("concluido", api.requests["m1"]["status"], api.requests["m1"].get("error_message"))
        log = self._svn("log", "-l", "1", "--xml", self.folha).stdout.decode("utf-8")
        self.assertIn("Merge de Unico para Folha_sustentação", log)
        self.assertIn("Pedido: m1", log)
        self.assertRegex(log, r"Revisões: r\d+")

    def test_direct_merge_with_conflict_blocks_only_that_merge(self):
        self._put(self.unico, "Script/Rotinas.sd", SCRIPT.replace("original", "unico"), "unico mexe")
        self._put(self.proxima, "Script/Rotinas.sd", SCRIPT.replace("original", "proxima"), "proxima mexe")
        api = FakeApi({
            "m1": self._request(self.unico, self.proxima, rid="m1", status="solicitado"),
            "m2": self._request(self.unico, self.folha, rid="m2", status="solicitado"),
        })
        before = self._svn("info", "--show-item", "last-changed-revision", self.proxima).stdout
        mb.run_once(api, self.svn, self.config)
        self.assertEqual("conflito", api.requests["m1"]["status"])
        self.assertIn("manualmente", api.requests["m1"]["error_message"])
        self.assertEqual(before, self._svn("info", "--show-item", "last-changed-revision", self.proxima).stdout)
        self.assertEqual("concluido", api.requests["m2"]["status"], api.requests["m2"].get("error_message"))
        self.assertIn("unico", self._cat(self.folha, "Script/Rotinas.sd"))

    def test_svn_runs_without_opening_a_console_window(self):
        calls = []
        real = mb.subprocess.run
        mb.subprocess.run = lambda *a, **k: calls.append(k) or real(*a, **k)
        try:
            self.svn.run(["--version", "--quiet"])
        finally:
            mb.subprocess.run = real
        self.assertEqual(subprocess.CREATE_NO_WINDOW, calls[0]["creationflags"])

    def test_edited_message_without_request_number_gets_a_trailer(self):
        self.assertEqual("Minha mensagem\n\n(AgenteTC pedido m9)\n", mb.commit_message({"id": "m9", "commit_message": "Minha mensagem"}))
        self.assertEqual("Pedido m9 ok\n", mb.commit_message({"id": "m9", "commit_message": "Pedido {pedido} ok"}))

    def test_parse_merge_output(self):
        wc = Path(r"C:\AgenteTC\merge_wc")
        text = "\n".join([
            "--- Merging r10 through r12 into 'C:\\AgenteTC\\merge_wc':",
            "U    C:\\AgenteTC\\merge_wc\\Script\\A.sd",
            "A    C:\\AgenteTC\\merge_wc\\Stores\\Files\\Novo.txt",
            "C    C:\\AgenteTC\\merge_wc\\Script\\B.sd",
            "   C C:\\AgenteTC\\merge_wc\\Script\\Removido.sd",
            " U   C:\\AgenteTC\\merge_wc",
            "Summary of conflicts:",
        ])
        files, conflicts = mb.parse_merge_output(text, wc)
        self.assertEqual(["Script/A.sd", "Stores/Files/Novo.txt", "Script/B.sd", "Script/Removido.sd"], [f["caminho"] for f in files])
        self.assertEqual(["alterado", "adicionado", "conflito", "conflito"], [f["acao"] for f in files])
        self.assertEqual([("Script/B.sd", "texto"), ("Script/Removido.sd", "arvore")], [(c["caminho"], c["tipo"]) for c in conflicts])


if __name__ == "__main__":
    unittest.main()

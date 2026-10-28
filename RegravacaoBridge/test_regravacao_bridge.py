"""Testes do RegravacaoBridge contra um repositorio SVN local de verdade (svnadmin + file:///).

Uso: py -3 -m unittest test_regravacao_bridge   (na pasta do RegravacaoBridge)
"""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import regravacao_bridge as rb

BIN = rb.TORTOISE_BIN
BASE = "Relatorio base\r\nvalor=10\r\n".encode("cp1252")
ATUAL = "Relatorio base\r\nvalor=20\r\nlinha nova\r\n".encode("cp1252")
ACENTO_REL = "Files/Tarefas/Relatórios/Relatório de tarefas/Rel 232498.txt"


def h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeApi:
    """API do Agent TC em memoria: so o que o Bridge usa."""

    def __init__(self, requests_by_id: dict[str, dict], files: dict[str, bytes]):
        self.requests = requests_by_id
        self.files = files
        self.updates: list[tuple[str, dict]] = []

    def requested(self):
        return [r for r in self.requests.values() if r["status"] == "solicitado"]

    def processing(self):
        return [r for r in self.requests.values() if r["status"] == "processando"]

    def claim(self, request_id):
        row = self.requests[request_id]
        if row["status"] != "solicitado":
            return None
        row["status"] = "processando"
        return row

    def update(self, request_id, fields):
        self.requests[request_id].update(fields)
        self.updates.append((request_id, fields))

    def download(self, storage_path):
        return self.files[storage_path]


class BridgeSvnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        repo = root / "repo"
        subprocess.run([str(BIN / "svnadmin.exe"), "create", str(repo)], check=True)
        self.root_url = "file:///" + str(repo).replace("\\", "/")
        self.project_url = self.root_url + "/ProjetoUnico/Unico"
        # Projeto com duas bases (uma com acento e espaco no caminho).
        src = root / "src" / "Unico" / "Stores"
        for rel in ("Files/Folha/Rel/A.txt", ACENTO_REL):
            target = src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(BASE)
        subprocess.run([str(BIN / "svn.exe"), "import", "-q", "-m", "inicial", str(root / "src" / "Unico"), self.project_url],
                       check=True, capture_output=True)
        self.config = rb.load_config({"AGENT_TC_BRIDGE_TOKEN": "x", "SVN_ALLOWED_ROOTS": self.root_url, "SVN_COMMIT": "1"})
        self.svn = rb.Svn(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def _request(self, rel: str, *, hash_base: str | None = None, rid: str = "r1") -> dict:
        return {
            "id": rid, "run_id": "rod_1", "status": "solicitado", "requested_by": "ana",
            "repository_url": self.project_url,
            "items_json": [{"difference_id": "d_" + rel, "id_caso_teste": "9.1.4.1.3", "arquivo_atual": "Rel_Atual.txt",
                            "caminho_base": rel, "storage_path_atual": "ev/" + rel, "hash_base": hash_base or h(BASE),
                            "hash_atual": h(ATUAL)}],
        }

    def _cat(self, rel: str) -> bytes:
        url = rb.target_url(self.project_url, rel, [self.root_url])
        return self.svn.cat(url, self.svn.head_revision(self.project_url))

    def test_rewrites_base_in_one_commit_including_accented_paths(self):
        request = self._request("Files/Folha/Rel/A.txt")
        request["items_json"] += self._request(ACENTO_REL)["items_json"]
        api = FakeApi({"r1": request}, {"ev/Files/Folha/Rel/A.txt": ATUAL, "ev/" + ACENTO_REL: ATUAL})
        before = self.svn.head_revision(self.project_url)

        self.assertEqual(1, rb.run_once(api, self.svn, self.config))

        row = api.requests["r1"]
        self.assertEqual("concluido", row["status"], row)
        self.assertEqual(str(int(before) + 1), row["svn_revision"])  # um commit so
        self.assertEqual(["gravado", "gravado"], [i["status"] for i in row["result_json"]["itens"]])
        self.assertEqual(ATUAL, self._cat("Files/Folha/Rel/A.txt"))  # bytes identicos (CRLF/ANSI)
        self.assertEqual(ATUAL, self._cat(ACENTO_REL))
        log = self._last_log()
        self.assertIn("Solicitado por: ana", log)
        self.assertIn("- CT 9.1.4.1.3: " + ACENTO_REL, log)

        # Retomar o mesmo pedido (ex.: Bridge caiu depois do commit) nao grava de novo.
        api.requests["r1"]["status"] = "processando"
        rb.handle(api.requests["r1"], api, self.svn, self.config)
        self.assertEqual("concluido", api.requests["r1"]["status"])
        self.assertEqual(["ja_gravado", "ja_gravado"], [i["status"] for i in api.requests["r1"]["result_json"]["itens"]])
        self.assertEqual(str(int(before) + 1), self.svn.head_revision(self.project_url))

    def _last_log(self) -> str:
        out = subprocess.run([str(BIN / "svn.exe"), "log", "--xml", "-l", "1", self.project_url], capture_output=True).stdout.decode("utf-8")
        # O svnmucc no Windows grava a mensagem com CRLF; o conteudo (acentos) e o que importa.
        return out.split("<msg>", 1)[1].split("</msg>", 1)[0].replace("\r\n", "\n")

    def test_default_message_keeps_accents_and_gets_request_number(self):
        api = FakeApi({"r1": self._request("Files/Folha/Rel/A.txt")}, {"ev/Files/Folha/Rel/A.txt": ATUAL})
        rb.run_once(api, self.svn, self.config)
        self.assertEqual(
            "Regravação de bases pelo dashboard (AgenteTC)\n\nPedido: r1\nRodagem: rod_1\nSolicitado por: ana\n\n"
            "- CT 9.1.4.1.3: Files/Folha/Rel/A.txt\n",
            self._last_log(),
        )

    def test_message_edited_on_dashboard_is_used(self):
        request = self._request(ACENTO_REL)
        request["commit_message"] = "Ajuste do relatório de tarefas (solicitação #232498)\r\nPedido {pedido}"
        api = FakeApi({"r1": request}, {"ev/" + ACENTO_REL: ATUAL})
        rb.run_once(api, self.svn, self.config)
        self.assertEqual("concluido", api.requests["r1"]["status"])
        self.assertEqual("Ajuste do relatório de tarefas (solicitação #232498)\nPedido r1\n", self._last_log())

    def test_base_changed_since_run_writes_nothing(self):
        request = self._request("Files/Folha/Rel/A.txt")
        request["items_json"] += self._request(ACENTO_REL, hash_base=h(b"outra base"))["items_json"]
        api = FakeApi({"r1": request}, {"ev/Files/Folha/Rel/A.txt": ATUAL, "ev/" + ACENTO_REL: ATUAL})
        before = self.svn.head_revision(self.project_url)

        rb.run_once(api, self.svn, self.config)

        row = api.requests["r1"]
        self.assertEqual("erro", row["status"])
        self.assertEqual({"nao_gravado", "base_mudou"}, {i["status"] for i in row["result_json"]["itens"]})
        self.assertTrue(row["result_json"]["conflito"])
        self.assertIn("Conflito no SVN", row["error_message"])
        self.assertEqual(before, self.svn.head_revision(self.project_url))
        self.assertEqual(BASE, self._cat("Files/Folha/Rel/A.txt"))

    def test_someone_commits_between_check_and_commit_is_conflict(self):
        """Corrida: a base confere, mas outra pessoa grava antes do nosso commit."""
        rel = "Files/Folha/Rel/A.txt"
        url = rb.target_url(self.project_url, rel, [self.root_url])
        api = FakeApi({"r1": self._request(rel)}, {"ev/" + rel: ATUAL})
        original_put = self.svn.put_all

        def put_after_other_commit(files, message, base_revision):
            original_put([(b"alteracao de outra pessoa", url)], "outra pessoa\n", base_revision)
            return original_put(files, message, base_revision)

        self.svn.put_all = put_after_other_commit
        rb.run_once(api, self.svn, self.config)

        row = api.requests["r1"]
        self.assertEqual("erro", row["status"])
        self.assertTrue(row["result_json"]["conflito"])
        self.assertEqual(["base_mudou"], [i["status"] for i in row["result_json"]["itens"]])
        self.assertIn("Conflito no SVN", row["error_message"])
        self.assertEqual(b"alteracao de outra pessoa", self._cat(rel))  # nada nosso foi gravado

    def test_simulation_mode_checks_everything_and_does_not_commit(self):
        config = {**self.config, "commit": False}
        api = FakeApi({"r1": self._request("Files/Folha/Rel/A.txt")}, {"ev/Files/Folha/Rel/A.txt": ATUAL})
        before = self.svn.head_revision(self.project_url)
        rb.run_once(api, self.svn, config)
        row = api.requests["r1"]
        self.assertEqual("erro", row["status"])
        self.assertIn("simulacao", row["error_message"].lower())
        self.assertEqual("simulado", row["result_json"]["itens"][0]["status"])
        self.assertEqual(before, self.svn.head_revision(self.project_url))

    def test_corrupted_download_and_missing_base(self):
        api = FakeApi({"r1": self._request("Files/Folha/Rel/A.txt")}, {"ev/Files/Folha/Rel/A.txt": b"corrompido"})
        rb.run_once(api, self.svn, self.config)
        self.assertEqual("erro", api.requests["r1"]["result_json"]["itens"][0]["status"])
        api2 = FakeApi({"r2": self._request("Files/Folha/Rel/Nao existe.txt", rid="r2")}, {"ev/Files/Folha/Rel/Nao existe.txt": ATUAL})
        rb.run_once(api2, self.svn, self.config)
        self.assertEqual("base_inexistente", api2.requests["r2"]["result_json"]["itens"][0]["status"])
        self.assertTrue(api2.requests["r2"]["result_json"]["conflito"])
        self.assertFalse(api.requests["r1"]["result_json"]["conflito"])  # download corrompido nao e conflito

    def test_commit_refused_if_file_changed_after_checked_revision(self):
        url = rb.target_url(self.project_url, "Files/Folha/Rel/A.txt", [self.root_url])
        checked = self.svn.head_revision(self.project_url)
        # Outra pessoa altera o arquivo depois da conferencia.
        self.svn.put_all([(b"alteracao de outra pessoa", url)], "outra pessoa\n", checked)
        with self.assertRaises(rb.SvnError):
            self.svn.put_all([(ATUAL, url)], "regravacao\n", checked)
        self.assertEqual(b"alteracao de outra pessoa", self._cat("Files/Folha/Rel/A.txt"))

    def test_commit_with_hundreds_of_files_in_one_revision(self):
        # 300 caminhos longos passam dos ~32 mil caracteres da linha de comando do Windows.
        nomes = [f"Files/Folha/Rel/LancamentosEntradaComNomeBemComprido_{i:04d}_conferencia_mensal.txt" for i in range(300)]
        files = [(f"conteudo {i}".encode(), rb.target_url(self.project_url, nome, [self.root_url])) for i, nome in enumerate(nomes)]
        revision = self.svn.put_all(files, "regravacao grande\n", self.svn.head_revision(self.project_url))
        self.assertEqual(revision, self.svn.head_revision(self.project_url))
        self.assertEqual(b"conteudo 0", self._cat(nomes[0]))
        self.assertEqual(b"conteudo 299", self._cat(nomes[-1]))

    def test_target_url_only_inside_stores_files_of_allowed_repositories(self):
        roots = [self.root_url]
        self.assertTrue(rb.target_url(self.project_url, "Files/A B/ç.txt", roots).endswith("/Stores/Files/A%20B/%C3%A7.txt"))
        for bad in ("Script/RotinasGerais.sd", "Files/../Script/x.sd", "Files", "/Files/x.txt", ""):
            with self.assertRaises(rb.ItemError):
                rb.target_url(self.project_url, bad, roots)
        with self.assertRaises(rb.ItemError):
            rb.target_url("https://outro-servidor/svn/x", "Files/A.txt", roots)


class FakeImportApi:
    def __init__(self, rows: list[dict]):
        self.rows = {r["id"]: r for r in rows}
        self.updates: list[tuple[str, dict]] = []
        self.fail_updates = 0

    def imports(self, status):
        wanted = "solicitado" if status == "requested" else "processando"
        return [r for r in self.rows.values() if r["status"] == wanted]

    def claim_import(self, request_id):
        row = self.rows[request_id]
        if row["status"] != "solicitado":
            return None
        row["status"] = "processando"
        return row

    def update_import(self, request_id, fields):
        if self.fail_updates:
            self.fail_updates -= 1
            raise RuntimeError("API POST /bridge/imports/x/update retornou HTTP 502")
        self.rows[request_id].update(fields)
        self.updates.append((request_id, fields))


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.temp.name) / "ArquivosCompactados"
        self.pasta = self.base / "A07" / "PROXIMA1.26.10.99 18_10_2026 08_16_16"
        self.pasta.mkdir(parents=True)
        self.config = {"logs_base": str(self.base), "import_bat": r"C:\TC\Util Compartilhado\AgenteTC\run_agent_tc_python.bat"}
        self.calls: list[list[str]] = []
        self.original_run = rb.subprocess.run

    def tearDown(self):
        rb.subprocess.run = self.original_run
        rb.IMPORT_RESULTS_PENDING.clear()
        self.temp.cleanup()

    def _fake_run(self, returncode: int, output: str):
        def run(command, **kwargs):
            self.calls.append(command)
            return subprocess.CompletedProcess(command, returncode, output.encode("utf-8"))
        rb.subprocess.run = run

    def test_runs_the_bat_with_vm_and_folder(self):
        self._fake_run(0, "ANALISE FINALIZADA COM SUCESSO.")
        api = FakeImportApi([{"id": "imp_1", "status": "solicitado", "run_folder": str(self.pasta)}])
        self.assertEqual(1, rb.run_imports_once(api, self.config))
        self.assertEqual([[self.config["import_bat"], "A07", str(self.pasta)]], self.calls)
        self.assertEqual("concluido", api.rows["imp_1"]["status"])
        self.assertEqual((0, "ANALISE FINALIZADA COM SUCESSO."), (api.rows["imp_1"]["exit_code"], api.rows["imp_1"]["output"]))
        self.assertEqual(0, rb.run_imports_once(api, self.config))

    def test_result_the_api_did_not_receive_is_sent_next_cycle(self):
        self._fake_run(0, "ok")
        api = FakeImportApi([{"id": "imp_1", "status": "solicitado", "run_folder": str(self.pasta)}])
        api.fail_updates = 2
        rb.run_imports_once(api, self.config)
        self.assertEqual("processando", api.rows["imp_1"]["status"])
        with self.assertRaises(RuntimeError):  # API ainda fora: fica para o proximo ciclo
            rb.run_imports_once(api, self.config)
        rb.run_imports_once(api, self.config)
        self.assertEqual("concluido", api.rows["imp_1"]["status"])
        self.assertEqual(1, len(self.calls))  # o .bat nao roda de novo
        self.assertEqual({}, rb.IMPORT_RESULTS_PENDING)

    def test_bat_error_is_reported(self):
        self._fake_run(1, "Pasta de logs da VM nao encontrada")
        api = FakeImportApi([{"id": "imp_1", "status": "solicitado", "run_folder": str(self.pasta)}])
        rb.run_imports_once(api, self.config)
        self.assertEqual(("erro", 1), (api.rows["imp_1"]["status"], api.rows["imp_1"]["exit_code"]))
        self.assertIn("codigo 1", api.rows["imp_1"]["error_message"])

    def test_bad_folders_never_reach_the_bat(self):
        self._fake_run(0, "")
        bad = [
            str(self.base / "A07" / "nao existe"),
            str(self.base / "A07"),
            str(self.pasta / "caso"),
            str(self.base / "A07" / "x & calc"),
            r"C:\Windows\System32",
        ]
        api = FakeImportApi([{"id": f"imp_{i}", "status": "solicitado", "run_folder": p} for i, p in enumerate(bad)])
        rb.run_imports_once(api, self.config)
        self.assertEqual([], self.calls)
        self.assertEqual(["erro"] * len(bad), [r["status"] for r in api.rows.values()])


if __name__ == "__main__":
    unittest.main()

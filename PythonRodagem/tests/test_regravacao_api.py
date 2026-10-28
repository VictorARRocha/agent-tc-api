from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi, make_server
from agent_tc_core.regravacao import MAX_MESSAGE_LENGTH, safe_base_path
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, RUN_ID, _payload, _write_env

BRIDGE = "Bearer token-do-bridge"


class User:
    def __init__(self, username: str, role: str, permissions=("rodagem", "merge", "regravar")):
        self.username, self.role, self.permissions = username, role, list(permissions)

    def validate(self, authorization):
        return {"id": "usr_" + self.username, "username": self.username, "role": self.role, "permissions": self.permissions}


def _evidence(root: Path, name: str, index: int, kind: str) -> dict:
    source = root / "vm" / "falha_1" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("conteudo " + name, encoding="utf-8")
    role = "comparacao/base" if kind == "comparacao_base" else "comparacao/atual"
    return {
        "id_evidencia": f"ev_falha_1_{index:04d}",
        "fk_falha": "falha_1",
        "tipo_arquivo": kind,
        "caminho_evidencia": str(source),
        "storage_path": f"contabil/PROXIMA/{RUN_ID}/falha_1/{role}/{name}",
        "mime_type": "text/plain",
        "extensao": ".txt",
        "nome_arquivo": name,
    }


def _payload_with_differences(root: Path, *, svn: bool = True) -> dict:
    payload = _payload(root, ["falha_1"])
    if svn:
        payload["rodagem"].update({
            "repositorio_url": "https://svn/ProjetoUnico/branches/minha",
            "repositorio_raiz": "https://svn",
            "repositorio_revisao": "100",
        })
    manifests = {
        "A": {"caminho_base": "Files\\Contabil\\Rel\\A.txt", "confirmado": True},
        "B": {"caminho_base": "Files\\Contabil\\Rel\\B.txt", "confirmado": False},
        "C": None,
        "D": {"caminho_base": "..\\Script\\RotinasGerais.sd", "confirmado": True},
    }
    index = 10
    for letter, manifest in manifests.items():
        base, atual = f"{letter}_Antigo.txt", f"{letter}_Atual.txt"
        index += 1
        payload["evidencias"].append(_evidence(root, base, index, "comparacao_base"))
        index += 1
        payload["evidencias"].append(_evidence(root, atual, index, "comparacao_atual"))
        resumo = {"hash_base": "h_base_" + letter, "hash_atual": "h_atual_" + letter, "tem_diferenca": True,
                  "linhas_alteradas_estimadas": 2}
        if manifest:
            resumo["regravacao"] = manifest
        payload["diferencas_relatorio"].append({
            "id_diferenca": "diff_" + letter,
            "fk_falha": "falha_1",
            "id_caso_teste": "3.1.1",
            "nome_arquivo_base": base,
            "nome_arquivo_atual": atual,
            "resumo_diferenca": resumo,
        })
    return payload


class SafePathTests(unittest.TestCase):
    def test_only_paths_inside_files(self):
        self.assertEqual("Files/Folha/Rel/A B.txt", safe_base_path("Files\\Folha\\Rel\\A B.txt"))
        for bad in ["..\\Script\\x.sd", "Files\\..\\Script\\x.sd", "C:\\TC\\x.txt", "\\Files\\x.txt", "Script\\x.sd", "Files", ""]:
            self.assertIsNone(safe_base_path(bad), bad)


class RegravacaoApiTests(unittest.TestCase):
    def _apis(self, root: Path, *, svn: bool = True):
        env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
        for repository in (SQLiteRepository(root / "agent-tc.sqlite"), InMemoryCanonicalRepository(env)):
            repository.initialize()
            repository.import_payload(_payload_with_differences(root, svn=svn))
            admin = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User("ana", "admin"))
            # joao: usuario comum SEM a permissao de regravar.
            user = AgentTcApi(root, repository=repository, env_path=env, auth_validator=User("joao", "user", permissions=("rodagem", "merge")))
            yield admin, user

    def test_candidates_explain_why_each_file_can_or_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, user in self._apis(Path(temp_dir)):
                status, data = user.route_get(f"/runs/{RUN_ID}/regravacao", {}, "Bearer x")
                self.assertEqual(200, status)
                by_file = {item["arquivo_atual"]: item for item in data["itens"]}
                self.assertTrue(by_file["A_Atual.txt"]["regravavel"])
                self.assertEqual("caminho_nao_confirmado", by_file["B_Atual.txt"]["motivo"])
                self.assertEqual("sem_manifesto", by_file["C_Atual.txt"]["motivo"])
                self.assertEqual("caminho_invalido", by_file["D_Atual.txt"]["motivo"])
                self.assertEqual("https://svn/ProjetoUnico/branches/minha", data["repository_url"])
                # Campos internos (storage, hash) nao vao para o dashboard.
                self.assertFalse(any(key.startswith("_") for key in by_file["A_Atual.txt"]))

    def test_run_without_svn_origin_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir), svn=False):
                data = admin.route_get(f"/runs/{RUN_ID}/regravacao", {}, "Bearer x")[1]
                item = next(i for i in data["itens"] if i["arquivo_atual"] == "A_Atual.txt")
                self.assertEqual("sem_origem_svn", item["motivo"])

    def test_only_who_has_permission_creates_and_items_carry_what_the_bridge_needs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, user in self._apis(Path(temp_dir)):
                body = {"run_id": RUN_ID, "difference_ids": ["diff_A"]}
                status, denied = user.route_post("/regravacoes", body, "Bearer x")
                self.assertEqual((403, "permission_required", "regravar"), (status, denied["error"], denied["permission"]))

                status, created = admin.route_post("/regravacoes", body, "Bearer x")
                self.assertEqual(201, status)
                self.assertEqual("solicitado", created["status"])
                self.assertEqual("ana", created["requested_by"])
                self.assertEqual("https://svn/ProjetoUnico/branches/minha", created["repository_url"])
                item = created["items_json"][0]
                self.assertEqual("Files/Contabil/Rel/A.txt", item["caminho_base"])
                self.assertEqual("h_base_A", item["hash_base"])
                self.assertTrue(item["storage_path_atual"].endswith("/A_Atual.txt"))

                # Mesmo arquivo de novo enquanto o primeiro pedido esta ativo.
                status, body_err = admin.route_post("/regravacoes", body, "Bearer x")
                self.assertEqual((409, "not_regravavel"), (status, body_err["error"]))
                listed = admin.route_get("/regravacoes", {"run_id": [RUN_ID]}, "Bearer x")[1]
                self.assertEqual([created["id"]], [row["id"] for row in listed])

    def test_commit_message_edited_on_dashboard_is_stored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir)):
                mensagem = "Regravação ajustada\n\nPedido: {pedido}\n- CT 3.1.1: relatório novo"
                status, created = admin.route_post(
                    "/regravacoes", {"run_id": RUN_ID, "difference_ids": ["diff_A"], "mensagem": "  " + mensagem + "  "}, "Bearer x"
                )
                self.assertEqual(201, status)
                self.assertEqual(mensagem, created["commit_message"])
                stored = admin.route_get("/regravacoes", {"run_id": [RUN_ID]}, "Bearer x")[1][0]
                self.assertEqual(mensagem, stored["commit_message"])

    def test_empty_message_means_default_and_invalid_messages_are_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir)):
                base = {"run_id": RUN_ID, "difference_ids": ["diff_A"]}
                self.assertEqual(400, admin.route_post("/regravacoes", {**base, "mensagem": 123}, "Bearer x")[0])
                self.assertEqual(400, admin.route_post("/regravacoes", {**base, "mensagem": "x" * (MAX_MESSAGE_LENGTH + 1)}, "Bearer x")[0])
                status, created = admin.route_post("/regravacoes", {**base, "mensagem": "   "}, "Bearer x")
                self.assertEqual(201, status)
                self.assertIsNone(created["commit_message"])

    def test_rejects_invalid_requests(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir)):
                cases = [
                    ({"run_id": RUN_ID, "difference_ids": ["diff_B"]}, 400),  # caminho nao confirmado
                    ({"run_id": RUN_ID, "difference_ids": ["diff_D"]}, 400),  # fora de Stores\Files
                    ({"run_id": RUN_ID, "difference_ids": ["diff_X"]}, 400),  # nao e da rodagem
                    ({"run_id": RUN_ID, "difference_ids": []}, 400),
                    ({"run_id": "nao_existe", "difference_ids": ["diff_A"]}, 404),
                ]
                for body, expected in cases:
                    self.assertEqual(expected, admin.route_post("/regravacoes", body, "Bearer x")[0], body)

    def test_cancel_only_while_queued(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, user in self._apis(Path(temp_dir)):
                created = admin.route_post("/regravacoes", {"run_id": RUN_ID, "difference_ids": ["diff_A"]}, "Bearer x")[1]
                self.assertEqual(403, user.route_post(f"/regravacoes/{created['id']}/cancel", {}, "Bearer x")[0])
                status, body = admin.route_post(f"/regravacoes/{created['id']}/cancel", {}, "Bearer x")
                self.assertEqual(200, status)
                self.assertEqual("cancelado", body["regravacao"]["status"])
                self.assertEqual(409, admin.route_post(f"/regravacoes/{created['id']}/cancel", {}, "Bearer x")[0])
                # Cancelado libera o arquivo para um novo pedido.
                self.assertEqual(201, admin.route_post("/regravacoes", {"run_id": RUN_ID, "difference_ids": ["diff_A"]}, "Bearer x")[0])

    def test_bridge_flow(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir)):
                created = admin.route_post("/regravacoes", {"run_id": RUN_ID, "difference_ids": ["diff_A"]}, "Bearer x")[1]
                rid = created["id"]
                self.assertEqual(401, admin.route_get("/bridge/regravacoes/requested", {}, "Bearer errado")[0])
                self.assertEqual([rid], [r["id"] for r in admin.route_get("/bridge/regravacoes/requested", {}, BRIDGE)[1]])

                status, claim = admin.route_post(f"/bridge/regravacoes/{rid}/claim", {}, BRIDGE)
                self.assertEqual((200, True), (status, claim["claimed"]))
                self.assertEqual("processando", claim["regravacao"]["status"])
                self.assertFalse(admin.route_post(f"/bridge/regravacoes/{rid}/claim", {}, BRIDGE)[1]["claimed"])
                self.assertEqual([], admin.route_get("/bridge/regravacoes/requested", {}, BRIDGE)[1])
                self.assertEqual([rid], [r["id"] for r in admin.route_get("/bridge/regravacoes/processing", {}, BRIDGE)[1]])
                self.assertEqual(409, admin.route_post(f"/regravacoes/{rid}/cancel", {}, "Bearer x")[0])

                self.assertEqual(400, admin.route_post(f"/bridge/regravacoes/{rid}/update", {"status": "cancelado"}, BRIDGE)[0])
                status, done = admin.route_post(
                    f"/bridge/regravacoes/{rid}/update",
                    {"status": "concluido", "svn_revision": "23500", "result_json": {"itens": [{"difference_id": "diff_A", "status": "gravado"}]}},
                    BRIDGE,
                )
                self.assertEqual(200, status)
                row = done["regravacao"]
                self.assertEqual(("concluido", "23500"), (row["status"], row["svn_revision"]))
                self.assertTrue(row["finished_at"])
                self.assertEqual([], admin.route_get("/bridge/regravacoes/processing", {}, BRIDGE)[1])
                self.assertEqual("gravado", row["result_json"]["itens"][0]["status"])
                self.assertEqual(404, admin.route_post("/bridge/regravacoes/nao-existe/update", {"status": "erro"}, BRIDGE)[0])
                # Depois de gravado, o arquivo aparece como ja regravado (nao volta a ser regravavel).
                data = admin.route_get(f"/runs/{RUN_ID}/regravacao", {}, "Bearer x")[1]
                item = next(i for i in data["itens"] if i["difference_id"] == "diff_A")
                self.assertEqual((False, "ja_regravado"), (item["regravavel"], item["motivo"]))

    def test_conflict_in_svn_blocks_new_attempts_but_other_errors_do_not(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir)):
                def finish(item_status: str, conflito: bool) -> None:
                    rid = admin.route_post("/regravacoes", {"run_id": RUN_ID, "difference_ids": ["diff_A"]}, "Bearer x")[1]["id"]
                    admin.route_post(f"/bridge/regravacoes/{rid}/claim", {}, BRIDGE)
                    admin.route_post(f"/bridge/regravacoes/{rid}/update", {
                        "status": "erro", "error_message": "x",
                        "result_json": {"conflito": conflito, "itens": [{"difference_id": "diff_A", "status": item_status}]},
                    }, BRIDGE)

                def item_a() -> dict:
                    data = admin.route_get(f"/runs/{RUN_ID}/regravacao", {}, "Bearer x")[1]
                    return next(i for i in data["itens"] if i["difference_id"] == "diff_A")

                # Erro comum (ex.: download falhou): pode tentar de novo.
                finish("erro", False)
                self.assertTrue(item_a()["regravavel"])
                # Conflito: a base mudou no SVN; tentar de novo daria o mesmo resultado.
                finish("base_mudou", True)
                item = item_a()
                self.assertEqual((False, "conflito_svn"), (item["regravavel"], item["motivo"]))
                self.assertIn("manualmente", item["motivo_texto"])
                status, body = admin.route_post("/regravacoes", {"run_id": RUN_ID, "difference_ids": ["diff_A"]}, "Bearer x")
                self.assertEqual((400, "not_regravavel"), (status, body["error"]))


    def test_regravar_em_outra_branch_da_lista_do_svn(self):
        proxima = "https://svn/ProjetoUnico/branches/Proxima10_0"
        branches = [
            {"name": "Unico", "url": "https://svn/ProjetoUnico/Unico", "kind": "trunk"},
            {"name": "minha", "url": "https://svn/ProjetoUnico/branches/minha", "kind": "branch"},
            {"name": "Proxima10_0", "url": proxima, "kind": "branch"},
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            for admin, _ in self._apis(Path(temp_dir)):
                self.assertEqual(200, admin.route_post("/bridge/merge/branches", {"branches": branches}, BRIDGE)[0])
                body = {"run_id": RUN_ID, "difference_ids": ["diff_A"]}

                # Branch fora da lista do SVN: recusada (na lista e no pedido).
                status, err = admin.route_post("/regravacoes", {**body, "branch_url": "https://svn/inventada"}, "Bearer x")
                self.assertEqual((400, "invalid_branch"), (status, err["error"]))
                self.assertEqual(400, admin.route_get(f"/runs/{RUN_ID}/regravacao", {"branch": ["https://svn/inventada"]}, "Bearer x")[0])

                # Regrava na branch da rodagem e conclui.
                rid = admin.route_post("/regravacoes", body, "Bearer x")[1]["id"]
                admin.route_post(f"/bridge/regravacoes/{rid}/claim", {}, BRIDGE)
                admin.route_post(f"/bridge/regravacoes/{rid}/update", {
                    "status": "concluido", "result_json": {"itens": [{"difference_id": "diff_A", "status": "gravado"}]},
                }, BRIDGE)

                def item_a(query) -> dict:
                    data = admin.route_get(f"/runs/{RUN_ID}/regravacao", query, "Bearer x")[1]
                    return {**next(i for i in data["itens"] if i["difference_id"] == "diff_A"), "_destino": data["destino_url"],
                            "_rodagem": data["repository_url"]}

                na_rodagem = item_a({})
                self.assertEqual((False, "ja_regravado"), (na_rodagem["regravavel"], na_rodagem["motivo"]))
                # Na Proxima o mesmo arquivo ainda pode ser regravado (o ja gravado vale por branch).
                na_proxima = item_a({"branch": [proxima + "/"]})
                self.assertTrue(na_proxima["regravavel"])
                self.assertEqual((proxima, "https://svn/ProjetoUnico/branches/minha"), (na_proxima["_destino"], na_proxima["_rodagem"]))

                status, created = admin.route_post("/regravacoes", {**body, "branch_url": proxima}, "Bearer x")
                self.assertEqual(201, status)
                self.assertEqual(proxima, created["repository_url"])  # o Bridge grava (e confere a base) nesta branch
                # Pedido em andamento bloqueia o arquivo em qualquer branch.
                self.assertEqual("em_andamento", item_a({})["motivo"])


class BridgeFilesHttpTests(unittest.TestCase):
    def test_bridge_downloads_evidence_with_its_token(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            env = _write_env(root, "AGENT_TC_BRIDGE_TOKEN=token-do-bridge\n")
            repository = SQLiteRepository(root / "agent-tc.sqlite")
            repository.initialize()
            repository.import_payload(_payload_with_differences(root))
            storage_path = f"contabil/PROXIMA/{RUN_ID}/falha_1/comparacao/atual/A_Atual.txt"
            stored = root / "storage" / storage_path
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(b"conteudo A_Atual.txt")
            server = make_server("127.0.0.1", 0, root, repository=repository, env_path=env)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/bridge/files/{storage_path}"
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(url)
                self.assertEqual(401, ctx.exception.code)
                request = urllib.request.Request(url, headers={"Authorization": BRIDGE})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(b"conteudo A_Atual.txt", response.read())
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

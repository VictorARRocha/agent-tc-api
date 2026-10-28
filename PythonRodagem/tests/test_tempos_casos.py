"""Tempo dos casos no banco (aba Performance sem a planilha do C:\\Tempos TC)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core import tempos_casos as tc
from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.local_auth import LocalAuthService
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_ingest_robustness import InMemoryCanonicalRepository, _payload, _write_env
from test_permissions import AuthSQLiteRepository

INICIO_1 = "2026-10-08T10:00:00-03:00"
INICIO_2 = "2026-10-13T11:12:01-03:00"


def payload_com_tempos(root: Path, run_id: str, inicio: str, tempos: dict[str, int]) -> dict:
    payload = _payload(root, ["falha_" + run_id], run_id=run_id)
    payload["rodagem"]["data_inicio"] = inicio
    payload["tempos_casos"] = [{"codigo_teste": caso, "segundos": s, "medido_em": inicio} for caso, s in tempos.items()]
    return payload


class RegrasTests(unittest.TestCase):
    def test_limite_por_faixa_nunca_menor_que_15s(self):
        casos = {30: 15, 59: 15, 60: 15, 90: 15, 119: 18, 120: 15, 200: 20, 299: 30, 300: 21, 480: 34, 599: 42, 600: 30, 1800: 90}
        for base, limite in casos.items():
            self.assertEqual(limite, tc.limite_segundos(base), base)

    def test_classificacao(self):
        self.assertEqual("igual", tc.classificar(40, 55))  # 15s exatos: ainda dentro
        self.assertEqual("mais_lento", tc.classificar(40, 56))
        self.assertEqual("mais_rapido", tc.classificar(40, 24))
        self.assertEqual("igual", tc.classificar(900, 940))  # 15 min: limite de 45s
        self.assertEqual("mais_lento", tc.classificar(900, 946))
        self.assertEqual("igual", tc.classificar(0, 50))  # sem base

    def test_le_tempo_ct_como_o_tc_grava(self):
        texto = (
            "\ufeff3.1.2.1.1;00:00:17 - 25/06/2026-19:20:54;00:00:16 - 27/06/2026-04:15:58\r"
            "2.1.7;00:02:10 - 13/10/2026-11:12:01;00:00:00 - 14/10/2026-08:00:00\r"
            "lixo;00:00:10 - 13/10/2026-11:12:01\r"
        )
        lido = tc.ler_tempo_ct(texto)
        self.assertEqual([("2026-06-25T19:20:54-03:00", 17), ("2026-06-27T04:15:58-03:00", 16)], lido["3.1.2.1.1"])
        self.assertEqual([("2026-10-13T11:12:01-03:00", 130)], lido["2.1.7"])  # tempo zero fica de fora
        self.assertEqual({"3.1.2.1.1", "2.1.7"}, set(lido))

    def test_le_tempo_padrao_mesmo_com_linhas_coladas(self):
        texto = "\ufeff3.1.8.1.8.1;00:02:18\r4.2.1;00:00:12\r4.2.2;00:00:05\r\n3.1.8.1.8.1;00:02:20"
        self.assertEqual({"3.1.8.1.8.1": 140, "4.2.1": 12, "4.2.2": 5}, tc.ler_tempo_padrao(texto))

    def test_medicoes_da_rodagem_pela_hora_de_inicio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pasta = Path(temp_dir)
            (pasta / "Fiscal Tempo CT.csv").write_bytes(
                "2.1.7;00:02:10 - 13/10/2026-11:12:01;00:02:00 - 12/10/2026-09:00:00\r2.1.8;00:00:40 - 13/10/2026-11:12:01\r".encode("utf-8")
            )
            (pasta / "Folha Tempo CT.csv").write_bytes(b"1.1;00:00:20 - 13/10/2026-11:12:02\r")
            medicoes = tc.medicoes_da_rodagem(pasta, "2026-10-13T11:12:01-03:00")
            self.assertEqual({("2.1.7", 130), ("2.1.8", 40)}, {(m["codigo_teste"], m["segundos"]) for m in medicoes})
            self.assertEqual([], tc.medicoes_da_rodagem(pasta / "nao_existe", "2026-10-13T11:12:01-03:00"))
            self.assertEqual([], tc.medicoes_da_rodagem(None, "2026-10-13T11:12:01-03:00"))


class RepositorioMixin:
    def novo_repositorio(self, root: Path):
        raise NotImplementedError

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.repo = self.novo_repositorio(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_primeira_medicao_vira_base_e_proxima_compara(self):
        self.repo.import_payload(payload_com_tempos(self.root, "rod_1", INICIO_1, {"3.1.1": 130, "3.1.2": 40}))
        primeira = {r["codigo_teste"]: r for r in self.repo.performance("rod_1")}
        self.assertEqual(("igual", 130, 0), (primeira["3.1.1"]["status"], primeira["3.1.1"]["tempo_padrao_segundos"], primeira["3.1.1"]["delay_segundos"]))
        self.assertEqual("primeira_medicao", primeira["3.1.1"]["base_origem"])

        self.repo.import_payload(payload_com_tempos(self.root, "rod_2", INICIO_2, {"3.1.1": 161, "3.1.2": 41}))
        linhas = self.repo.performance("rod_2")
        por_caso = {r["codigo_teste"]: r for r in linhas}
        self.assertEqual(("mais_lento", 31, "00:02:10", "00:02:41"), (
            por_caso["3.1.1"]["status"], por_caso["3.1.1"]["delay_segundos"], por_caso["3.1.1"]["tempo_padrao"], por_caso["3.1.1"]["tempo_atual"]))
        self.assertEqual("igual", por_caso["3.1.2"]["status"])
        self.assertEqual([130, 161], por_caso["3.1.1"]["tendencia"])
        self.assertEqual("3.1.1", linhas[0]["codigo_teste"])  # quem estourou vem primeiro
        # A rodagem antiga continua mostrando so o que ja tinha (a tendencia nao enxerga o futuro).
        self.assertEqual([130], {r["codigo_teste"]: r for r in self.repo.performance("rod_1")}["3.1.1"]["tendencia"])

    def test_base_da_planilha_e_historico(self):
        resultado = self.repo.import_testcase_times(
            {"3.1.1": 120, "9.9": 0},
            [{"codigo_teste": "3.1.1", "segundos": 125, "medido_em": "2026-06-25T19:20:54-03:00"},
             {"codigo_teste": "3.1.2", "segundos": 50, "medido_em": "2026-06-27T04:15:58-03:00"},
             {"codigo_teste": "3.1.2", "segundos": 45, "medido_em": "2026-06-25T19:20:54-03:00"}],
        )
        self.assertEqual({"medicoes": 3, "bases_planilha": 1, "bases_primeira_medicao": 1}, resultado)
        self.assertEqual((120, "planilha"), (self.repo.testcase_time_base("3.1.1")["base_seconds"], self.repo.testcase_time_base("3.1.1")["origin"]))
        self.assertEqual(45, self.repo.testcase_time_base("3.1.2")["base_seconds"])  # a mais antiga
        # Rodar de novo nao duplica nem troca a base.
        self.repo.import_testcase_times({"3.1.1": 999}, [{"codigo_teste": "3.1.1", "segundos": 125, "medido_em": "2026-06-25T19:20:54-03:00"}])
        historico = self.repo.testcase_time_history("3.1.1")
        self.assertEqual((120, 1), (historico["base_segundos"], len(historico["medicoes"])))

    def test_troca_de_base_guarda_a_anterior(self):
        self.repo.import_payload(payload_com_tempos(self.root, "rod_1", INICIO_1, {"3.1.1": 130}))
        self.repo.import_payload(payload_com_tempos(self.root, "rod_2", INICIO_2, {"3.1.1": 161}))
        self.repo.save_testcase_time_base({"id": "3.1.1", "base_seconds": 161, "origin": "substituida", "previous_seconds": 130,
                                           "changed_by": "ana", "changed_at": "2026-10-13T12:00:00+00:00"})
        self.assertEqual("igual", self.repo.performance("rod_2")[0]["status"])
        historico = self.repo.testcase_time_history("3.1.1")
        self.assertEqual((161, 130, "ana"), (historico["base_segundos"], historico["base_anterior_segundos"], historico["base_definida_por"]))
        self.assertEqual(["2026-10-13T11:12:01-03:00", "2026-10-08T10:00:00-03:00"], [m["medido_em"] for m in historico["medicoes"]])
        self.assertEqual("rod_2", historico["medicoes"][0]["run_id"])

    def test_historico_traz_a_versao_de_cada_medicao(self):
        self.repo.import_testcase_times({}, [
            {"codigo_teste": "3.1.1", "segundos": 120, "medido_em": "2026-06-25T19:20:54-03:00"},  # sem rodagem registrada
            {"codigo_teste": "3.1.1", "segundos": 125, "medido_em": INICIO_1},  # historico importado de uma rodagem que existe
        ])
        self.repo.import_payload(payload_com_tempos(self.root, "rod_1", INICIO_1, {"3.2.9": 10}))
        payload = payload_com_tempos(self.root, "rod_2", INICIO_2, {"3.1.1": 161})
        payload["rodagem"]["versao"] = "PROXIMA1.26.10.0"
        self.repo.import_payload(payload)
        versoes = [(m["medido_em"][:10], m["versao"]) for m in self.repo.testcase_time_history("3.1.1")["medicoes"]]
        self.assertEqual([("2026-10-13", "PROXIMA1.26.10.0"), ("2026-10-08", "PROXIMA"), ("2026-06-25", None)], versoes)

    def test_rodagem_sem_tempos_mostra_o_que_veio_da_planilha_antiga(self):
        payload = _payload(self.root, ["falha_1"], run_id="rod_velha")
        payload["atrasos_rodagem"] = [{"id_atraso": "d1", "codigo_teste": "3.1.1", "tempo_padrao_segundos": 10,
                                       "tempo_atual_segundos": 40, "delay_segundos": 30, "status": "mais_lento"}]
        self.repo.import_payload(payload)
        [linha] = self.repo.performance("rod_velha")
        self.assertEqual(("d1", 30), (linha["id"], linha["delay_segundos"]))


class SQLiteTemposTests(RepositorioMixin, unittest.TestCase):
    def novo_repositorio(self, root):
        repo = SQLiteRepository(root / "agent-tc.sqlite")
        repo.initialize()
        return repo


class CanonicalTemposTests(RepositorioMixin, unittest.TestCase):
    def novo_repositorio(self, root):
        return InMemoryCanonicalRepository(_write_env(root))


class ApiTemposTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.repo = AuthSQLiteRepository(root / "agent-tc.sqlite")
        self.repo.initialize()
        self.api = AgentTcApi(root, repository=self.repo, env_path=_write_env(root, "AGENT_TC_API_TOKEN=token-da-vm\n"))
        auth = LocalAuthService(self.repo)
        auth.register({"username": "chefe", "password": "senha-forte"})
        self.admin = "Bearer " + auth.login("chefe", "senha-forte")["token"]
        status, body = self.api.route_post("/auth/register", {"username": "ana", "password": "senha-forte", "modules": ["contabil"]})
        self.assertEqual(201, status)
        self.ana_id = body["user"]["id"]
        self.api.route_patch(f"/auth/users/{self.ana_id}", {"status": "approved", "permissions": ["rodagem"]}, self.admin)
        self.ana = "Bearer " + auth.login("ana", "senha-forte")["token"]
        self.repo.import_payload(payload_com_tempos(root, "rod_1", INICIO_1, {"3.1.1": 130, "1.2": 40}))

    def tearDown(self):
        self.temp.cleanup()

    def test_historico_respeita_o_modulo(self):
        status, body = self.api.route_get("/testcase-times/3.1.1", {}, self.ana)
        self.assertEqual((200, 130, 1), (status, body["base_segundos"], len(body["medicoes"])))
        self.assertEqual(403, self.api.route_get("/testcase-times/1.2", {}, self.ana)[0])
        self.assertEqual(400, self.api.route_get("/testcase-times/abc", {}, self.ana)[0])
        self.assertEqual(401, self.api.route_get("/testcase-times/3.1.1", {}, None)[0])

    def test_trocar_base_so_com_permissao_de_regravar_e_no_modulo(self):
        status, body = self.api.route_post("/testcase-times/3.1.1/base", {"segundos": 161}, self.ana)
        self.assertEqual((403, "permission_required"), (status, body["error"]))
        self.api.route_patch(f"/auth/users/{self.ana_id}", {"permissions": ["rodagem", "regravar"]}, self.admin)
        status, body = self.api.route_post("/testcase-times/3.1.1/base", {"segundos": 161}, self.ana)
        self.assertEqual((200, 161, 130, "ana"), (status, body["base_segundos"], body["base_anterior_segundos"], body["base_definida_por"]))
        self.assertEqual("substituida", body["base_origem"])
        self.assertEqual((403, "module_required"), (lambda r: (r[0], r[1]["error"]))(
            self.api.route_post("/testcase-times/1.2/base", {"segundos": 50}, self.ana)))
        for invalido in (0, -5, "161", 1.5, True, 90000, None):
            self.assertEqual(400, self.api.route_post("/testcase-times/3.1.1/base", {"segundos": invalido}, self.ana)[0], invalido)

    def test_carga_inicial_so_com_o_token_das_vms(self):
        corpo = {"bases": {"3.1.9": 60}, "medicoes": [{"codigo_teste": "3.1.9", "segundos": 62, "medido_em": "2026-06-25T19:20:54-03:00"}]}
        self.assertEqual(401, self.api.route_post("/testcase-times/import", corpo, self.admin)[0])
        status, body = self.api.route_post("/testcase-times/import", corpo, "Bearer token-da-vm")
        self.assertEqual((200, 1, 1), (status, body["medicoes"], body["bases_planilha"]))
        self.assertEqual(400, self.api.route_post("/testcase-times/import", {"bases": [], "medicoes": []}, "Bearer token-da-vm")[0])

    def test_aba_performance_pela_rota_da_rodagem(self):
        status, linhas = self.api.route_get("/runs/rod_1/performance", {}, self.admin)
        self.assertEqual((200, {"3.1.1", "1.2"}), (status, {r["codigo_teste"] for r in linhas}))


class PipelineTemposTests(unittest.TestCase):
    def test_pipeline_pega_os_tempos_da_rodagem(self):
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
            performance = root / "Performance"
            performance.mkdir()
            (performance / "Contábil Tempo CT.csv").write_bytes(
                "3.1.1;00:01:00 - 12/10/2026-09:00:00;00:02:41 - 13/10/2026-11:12:01\r3.1.2;00:00:40 - 13/10/2026-11:12:01\r".encode("utf-8")
            )
            _report, payload = run_shadow_pipeline(
                run_folder=run_folder, mds_path=mds, output_root=root / "saida", vm_name="a07", performance_dir=performance
            )
            _report, sem_pasta = run_shadow_pipeline(
                run_folder=run_folder, mds_path=mds, output_root=root / "saida2", vm_name="a07", performance_dir=root / "nao_existe"
            )
        self.assertEqual({("3.1.1", 161), ("3.1.2", 40)}, {(m["codigo_teste"], m["segundos"]) for m in payload["tempos_casos"]})
        self.assertEqual([], sem_pasta["tempos_casos"])
        self.assertEqual("mod_contabil", payload["modulo"]["id_modulo"])  # com falha: vale a falha, como sempre

    def test_rodagem_100_verde_vai_com_0_falhas_no_modulo_dos_casos_medidos(self):
        from agent_tc_core.pipeline import run_shadow_pipeline

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            run_folder = root / "A06" / "PROXIMA1.26.10.0 13_10_2026 11_12_01"
            run_folder.mkdir(parents=True)
            (run_folder / "TotalTestesRodados.txt").write_text("21", encoding="utf-8")
            mds = root / "Unico.mds"
            mds.write_text('<Project><Node name="[2] Fiscal"><Node name="[2.1.7] Caso" /></Node></Project>', encoding="utf-8")
            performance = root / "Performance"
            performance.mkdir()
            (performance / "Fiscal Tempo CT.csv").write_bytes(b"2.1.7;00:02:10 - 13/10/2026-11:12:01\r2.1.8;00:00:40 - 13/10/2026-11:12:01\r")
            (performance / "GerenciadorSCI Tempo CT.csv").write_bytes(b"0.4;00:00:30 - 13/10/2026-11:12:01\r")
            _r, verde = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "s1", vm_name="a06", performance_dir=performance)
            # Sem compactado e sem tempo: continua parando, como sempre.
            with self.assertRaisesRegex(RuntimeError, "Nenhum RAR/ZIP"):
                run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "s2", vm_name="a06", performance_dir=root / "x")
        self.assertEqual(("mod_fiscal", 0, 3), (verde["modulo"]["id_modulo"], len(verde["falhas"]), len(verde["tempos_casos"])))
        self.assertEqual(21, verde["rodagem"]["total_analisados"])

        # E entra no banco como uma rodagem normal, com a aba Performance.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            repo = SQLiteRepository(Path(temp_dir) / "db.sqlite")
            repo.initialize()
            repo.import_payload(verde)
            run_id = verde["rodagem"]["id_rodagem"]
            self.assertEqual(("fiscal", 0), (repo.run(run_id)["modulo_slug"], repo.run(run_id)["total_falhas"]))
            self.assertEqual({"0.4", "2.1.7", "2.1.8"}, {r["codigo_teste"] for r in repo.performance(run_id)})

    def test_regra_do_modulo(self):
        from agent_tc_core.payload import module_from_failures

        self.assertEqual("mod_fiscal", module_from_failures([], ["0.4", "0.5", "2.1.7", "2.1.8", "6.1"])["id"])
        self.assertEqual("mod_contabil", module_from_failures([], ["4.1", "7.2"])["id"])
        self.assertEqual("mod_geral", module_from_failures([], ["0.4", "8.1"])["id"])
        self.assertEqual("mod_geral", module_from_failures([])["id"])
        # Falha nas configuracoes gerais [0.x] nao leva a rodagem da Folha para o Geral.
        from types import SimpleNamespace
        falha = lambda c: SimpleNamespace(archive=SimpleNamespace(id_caso_teste=c))
        self.assertEqual("mod_folha", module_from_failures([falha("0.2")], ["1.3.7", "1.6.20"])["id"])
        self.assertEqual("mod_folha", module_from_failures([falha("0.2"), falha("0.3"), falha("1.3.7")])["id"])
        self.assertEqual("mod_geral", module_from_failures([falha("0.2")], ["0.4"])["id"])


if __name__ == "__main__":
    unittest.main()

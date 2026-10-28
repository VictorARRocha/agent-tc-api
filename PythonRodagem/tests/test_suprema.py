"""Rodagens do Suprema e do Practice: .mds pelos casos, diferencas sem extensao, mod_xml, tempos e total."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_tc_core.config import pasta_agrupada_do_practice
from agent_tc_core.extractor import extract_case_id
from agent_tc_core.mds import MdsIndex, mds_do_projeto_da_pasta
from agent_tc_core.parser import find_comparisons
from agent_tc_core.pipeline import run_shadow_pipeline, sistema_pelos_casos
from agent_tc_core.tempos_casos import medicoes_do_sistema

UNICO_MDS = '<Project><Node name="[3] Contabil"><Node name="[3.1.1] Caso do Unico" /></Node></Project>'
SUPREMA_MDS = (
    '<Project><testItem name="[16] Suprema">'
    '<testItem name="[16.3.4.22] Apuracao Reinf" description="#200862, Confere a apuracao" />'
    '<testItem name="[16.11.2.1] Emissao DARF" description="#123" />'
    "</testItem></Project>"
)
MOD_XML = (
    "import socket\n"
    "ct_desmarcar = []\n"
    "if socket.gethostname() != 'a09':\n"
    "    ct_desmarcar = ct_desmarcar + ['[1.3.4]', '[2.1]']\n"
)


def _rodagem_suprema(root: Path, pasta: str = "8.09a 14_10_2026 18_36_14") -> Path:
    """Como o Suprema grava: pasta so com a versao, _Antigo/_Atual sem extensao e InformacaoErro.txt."""
    run_folder = root / "A03" / pasta
    run_folder.mkdir(parents=True)
    with zipfile.ZipFile(run_folder / "16.3.4.22-14_10_2026-19_33_39.zip", "w") as zf:
        zf.writestr("16.3.4.22/ApuracaoReinf200862_Antigo", "linha 1\r\nvalor 10\r\n")
        zf.writestr("16.3.4.22/ApuracaoReinf200862_Atual", "linha 1\r\nvalor 12\r\n")
    with zipfile.ZipFile(run_folder / "16.11.2.1-14_10_2026-23_29_21.zip", "w") as zf:
        zf.writestr("16.11.2.1/InformacaoErro.txt", "Foi excedido o tempo limite da rotina pEsperaProcesso")
    return run_folder


class SupremaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.unico = self.root / "Unico" / "Unico.mds"
        self.unico.parent.mkdir()
        self.unico.write_text(UNICO_MDS, encoding="utf-8")
        self.suprema = self.root / "tc12" / "Integracoes" / "Integracoes.mds"
        self.suprema.parent.mkdir(parents=True)
        self.suprema.write_text(SUPREMA_MDS, encoding="utf-8")
        self.mod_xml = self.root / "mod_xml.py"
        self.mod_xml.write_text(MOD_XML, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _pipeline(self, run_folder, **kwargs):
        kwargs.setdefault("mds_por_sistema", {"Suprema": self.suprema})
        _r, payload = run_shadow_pipeline(
            run_folder=run_folder,
            mds_path=self.unico,
            output_root=self.root / "saida",
            vm_name="a03",
            mod_xml_path=self.mod_xml,
            **kwargs,
        )
        return payload

    def test_sistema_pelos_casos(self):
        self.assertEqual("Suprema", sistema_pelos_casos(["16.3.4.22", "16.11.2.1"]))
        self.assertEqual("Practice", sistema_pelos_casos(["19.2.4.11"]))
        self.assertEqual("Unico", sistema_pelos_casos(["0.3", "3.1.1", "1.2"]))
        self.assertIsNone(sistema_pelos_casos(["0.3"]))
        self.assertIsNone(sistema_pelos_casos([]))

    def test_rodagem_do_suprema_usa_o_mds_dele_mesmo_com_o_do_unico_no_bat(self):
        payload = self._pipeline(_rodagem_suprema(self.root))
        rodagem = payload["rodagem"]
        self.assertEqual("Suprema", rodagem["sistema"])
        self.assertEqual("mod_suprema", rodagem["fk_modulo"])
        self.assertEqual("8.09a", rodagem["versao"])
        self.assertEqual("rod_a03_8.09a_20261014_183614", rodagem["id_rodagem"])
        nomes = {f["id_caso_teste"]: f["nome_mds"] for f in payload["falhas"]}
        self.assertEqual({"16.3.4.22": "Apuracao Reinf", "16.11.2.1": "Emissao DARF"}, nomes)
        ids = {str(n.get("node_id")) for n in payload["testcase_hierarchy"]}
        self.assertIn("16.3.4.22", ids)
        self.assertNotIn("3.1.1", ids)

    def test_diferenca_sem_extensao_e_quebra(self):
        payload = self._pipeline(_rodagem_suprema(self.root))
        tipos = {f["id_caso_teste"]: f["tipo_detectado_python"] for f in payload["falhas"]}
        self.assertEqual("Diferença entre arquivos de comparação", tipos["16.3.4.22"])
        self.assertEqual("Quebra de testes", tipos["16.11.2.1"])
        self.assertEqual(1, len(payload["diferencas_relatorio"]))

    def test_sem_mds_do_suprema_avisa_e_segue_com_o_do_bat(self):
        payload = self._pipeline(
            _rodagem_suprema(self.root), mds_por_sistema={"Suprema": self.root / "nao_existe.mds"}
        )
        self.assertTrue(any(e.get("tipo") == "mds_do_sistema" for e in payload.get("erros_processamento") or []))
        self.assertEqual(2, len(payload["falhas"]))

    def test_sem_troca_automatica_fica_o_mds_informado(self):
        payload = self._pipeline(_rodagem_suprema(self.root), mds_por_sistema=None)
        nomes = {f["id_caso_teste"]: f["nome_mds"] for f in payload["falhas"]}
        self.assertNotEqual("Apuracao Reinf", nomes["16.3.4.22"])

    def test_lista_do_mod_xml_so_vai_em_rodagem_do_unico(self):
        payload = self._pipeline(_rodagem_suprema(self.root))
        self.assertNotIn("casos_desmarcados_mod_xml", payload)

        run_folder = self.root / "A07" / "PROXIMA 13_10_2026 11_12_01"
        run_folder.mkdir(parents=True)
        with zipfile.ZipFile(run_folder / "3.1.1-13_10_2026-11_12_01.zip", "w") as zf:
            zf.writestr("erro.txt", "quebrou")
        unico = self._pipeline(run_folder)
        self.assertEqual(["1.3.4", "2.1"], unico["casos_desmarcados_mod_xml"]["casos"])
        self.assertEqual("Unico", unico["rodagem"]["sistema"])

    def test_tempos_so_do_sistema_da_rodagem(self):
        perf = self.root / "Performance"
        perf.mkdir()
        # Mesmo inicio (14/10/2026 18:36:14) no Suprema e numa rodagem do Unico.
        (perf / "Suprema Tempo CT.csv").write_text("16.3.4.22;00:03:51 - 14/10/2026-18:36:14;\r", encoding="utf-8")
        (perf / "Folha Tempo CT.csv").write_text("1.2.3;00:01:00 - 14/10/2026-18:36:14;\r", encoding="utf-8")
        payload = self._pipeline(_rodagem_suprema(self.root), performance_dir=perf)
        self.assertEqual(["16.3.4.22"], [m["codigo_teste"] for m in payload["tempos_casos"]])

    def test_medicoes_do_sistema(self):
        medicoes = [{"codigo_teste": "16.1"}, {"codigo_teste": "3.1"}, {"codigo_teste": "0.3"}, {"codigo_teste": "19.2"}]
        self.assertEqual(["16.1"], [m["codigo_teste"] for m in medicoes_do_sistema(medicoes, "Suprema")])
        self.assertEqual(["3.1", "0.3"], [m["codigo_teste"] for m in medicoes_do_sistema(medicoes, "Unico")])


class ComparacaoSemExtensaoTests(unittest.TestCase):
    def test_pares_sem_extensao_de_texto_entram_e_binarios_nao(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            pasta = Path(temp_dir)
            arquivos = {
                "ApuracaoReinf200862_Antigo": b"a\r\nb\r\n",
                "ApuracaoReinf200862_Atual": b"a\r\nc\r\n",
                "Relatorio_Antigo.txt": b"x\n",
                "Relatorio_Atual.txt": b"y\n",
                "ExportacaoBens_Antigo": b"\x03\x00\x01binario",
                "ExportacaoBens_Atual": b"\x03\x00\x02binario",
                "PrintsTelasCasoTeste": b"nao e comparacao",
            }
            for nome, conteudo in arquivos.items():
                (pasta / nome).write_bytes(conteudo)
            pares = find_comparisons(sorted(pasta.iterdir()))
            self.assertEqual(["apuracaoreinf200862", "relatorio"], [p.key for p in pares])
            self.assertEqual("ApuracaoReinf200862_Atual", pares[0].atual.name)


class PracticeTests(unittest.TestCase):
    def test_id_do_caso_no_nome_do_rar(self):
        nomes = {
            "3.1.1-13_10_2026-11_12_01.RAR": "3.1.1",
            "19.2.4.11 - 15-10-2026 12_12_31.RAR": "19.2.4.11",
            "19.0.2.1.17  - 09-10-2026 17_29_45.RAR": "19.0.2.1.17",
            "[19.1000.1] - Inconformidades e-Social(novo auditor) - 10-10-2026 00_09_50.RAR": "19.1000.1",
            "[19.1300.1] Exportação consulta verbas - 10-10-2026 00_09_50.RAR": "19.1300.1",
        }
        for nome, esperado in nomes.items():
            self.assertEqual((esperado, True), extract_case_id(nome), nome)
        self.assertEqual(("ID invalido", False), extract_case_id("lixo.rar"))

    def test_nomes_do_mds_do_practice(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            mds = Path(temp_dir) / "Practice Base Unificada.mds"
            mds.write_text(
                '<Project><testItem name="[19] Practice">'
                '<testItem name="[19.0.1.1] - Conferencia SWS" />'
                '<testItem name="[19.0.2.1.17 ] - Plano de saude parte 2" />'
                '<testItem name="[19.0.5.12] - Título: ferias&#13;&#10;Referência: 08/2025" />'
                '<testItem name="*[19.0.13] -Solicitação #235004" />'
                "</testItem></Project>",
                encoding="utf-8",
            )
            index = MdsIndex(mds)
        nomes = {k: v["node_name"] for k, v in index.by_id.items()}
        self.assertEqual("Conferencia SWS", nomes["19.0.1.1"])
        self.assertEqual("Plano de saude parte 2", nomes["19.0.2.1.17"])
        self.assertEqual("Título: ferias Referência: 08/2025", nomes["19.0.5.12"])
        self.assertEqual("Solicitação #235004", nomes["19.0.13"])

    def test_pasta_da_base_agrupada_da_mesma_rodagem(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            vm = Path(temp_dir) / "A04"
            for nome in (
                "Practice Base Agrupada 8.34a 09_10_2026 17_29_45",  # esta rodagem
                "Practice Base Agrupada 8.34a 06_10_2026 18_05_11",  # rodagem anterior
                "Practice Base Agrupada 8.33a 09_10_2026 18_00_00",  # outra versao
                "Practice Base Agrupada 8.34a 10_10_2026 08_00_00",  # depois da Individual
                "FOLHA_SUSTENTACAO_2026 09_10_2026 20_00_00",       # Folha na mesma VM
                "Practice Base Individual 8.34a 10_10_2026 00_09_50",
                "Practice Base Individual 8.34a 20_10_2026 00_09_50",
            ):
                (vm / nome).mkdir(parents=True)
            individual = vm / "Practice Base Individual 8.34a 10_10_2026 00_09_50"
            self.assertEqual(vm / "Practice Base Agrupada 8.34a 09_10_2026 17_29_45", pasta_agrupada_do_practice(individual))
            # Agrupada de mais de 24 h antes e de outra rodagem; a propria Agrupada e o Unico nao puxam nada.
            self.assertIsNone(pasta_agrupada_do_practice(vm / "Practice Base Individual 8.34a 20_10_2026 00_09_50"))
            self.assertIsNone(pasta_agrupada_do_practice(vm / "Practice Base Agrupada 8.34a 09_10_2026 17_29_45"))
            self.assertIsNone(pasta_agrupada_do_practice(vm / "FOLHA_SUSTENTACAO_2026 09_10_2026 20_00_00"))

    def test_origem_svn_vem_do_projeto_que_gerou_a_pasta(self):
        # Base Individual e Base Agrupada sao projetos (e Stores) diferentes; a lista de .mds e a mesma.
        unificada = Path(r"C:\TC\TC12 - Simplificado\Cadastros\Practice Base Unificada.mds")
        individuais = Path(r"C:\TC\TC12 - Simplificado\Practice Antigo\Practice Bases Individuais.mds")
        mds = [unificada, individuais]
        self.assertEqual(individuais, mds_do_projeto_da_pasta(mds, r"S:\x\A04\Practice Base Individual 8.34a 10_10_2026 00_09_50"))
        self.assertEqual(unificada, mds_do_projeto_da_pasta(mds, r"S:\x\A04\Practice Base Agrupada 8.34a 09_10_2026 17_29_45"))
        suprema = Path(r"C:\TC\tc12\PROJETO-TC12\Integracoes\Integracoes.mds")
        self.assertEqual(suprema, mds_do_projeto_da_pasta([suprema], r"S:\x\A03\8.09a 14_10_2026 18_36_14"))

    def test_total_pelos_casos_medidos_e_que_falharam(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            mds = root / "Practice Base Unificada.mds"
            mds.write_text('<Project><testItem name="[19.1.1] A" /><testItem name="[19.1.2] B" /></Project>', encoding="utf-8")
            run_folder = root / "A04" / "Practice Base Agrupada 8.34a 09_10_2026 17_29_45"
            run_folder.mkdir(parents=True)
            # O TotalTestesRodados.txt do Practice nao e o total de casos (vai para a ultima pasta da VM).
            (run_folder / "TotalTestesRodados.txt").write_text("0", encoding="utf-8-sig")
            with zipfile.ZipFile(run_folder / "19.1.2 - 09-10-2026 17_29_45.zip", "w") as zf:
                zf.writestr("logErro.txt", "quebrou")
            perf = root / "Performance"
            perf.mkdir()
            # Como o TC grava: BOM, linhas separadas so por CR e id com espaco no fim.
            linhas = ["19.1.1;00:01:00 - 09/10/2026-17:29:45;", "19.0.1 ;00:00:05 - 09/10/2026-17:29:45;"]
            (perf / "Practice Tempo CT.csv").write_bytes(("﻿" + chr(13).join(linhas) + chr(13)).encode("utf-8"))
            _r, payload = run_shadow_pipeline(
                run_folder=run_folder, mds_path=mds, output_root=root / "saida", vm_name="a04", performance_dir=perf
            )
        self.assertEqual("Practice", payload["rodagem"]["sistema"])
        self.assertEqual(3, payload["rodagem"]["total_executed"])  # 19.1.1 e 19.0.1 medidos + 19.1.2 falhou

    def test_suprema_sem_tempos_fica_sem_total(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            mds = root / "Integracoes.mds"
            mds.write_text(SUPREMA_MDS, encoding="utf-8")
            run_folder = _rodagem_suprema(root)
            (run_folder / "TotalTestesRodados.txt").write_text("0", encoding="utf-8-sig")
            _r, payload = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "saida", vm_name="a03")
        self.assertIsNone(payload["rodagem"]["total_executed"])


if __name__ == "__main__":
    unittest.main()

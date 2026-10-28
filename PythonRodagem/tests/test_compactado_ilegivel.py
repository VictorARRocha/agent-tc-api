"""Compactado que o extrator nao abre nao pode sumir da rodagem (A05N, PRE-CLIENTES 26/10/2026:
os .RAR eram ZIP, o UnRAR disse "danificado" e a rodagem apareceu com 0 falhas)."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_tc_core import pipeline
from agent_tc_core.pipeline import run_shadow_pipeline

MDS = '<Project><Node name="[1] Folha"><Node name="[1.3.7] Caso A" /><Node name="[1.6.20] Caso B" /></Node></Project>'


class CompactadoIlegivelTests(unittest.TestCase):
    def _rodar(self, montar):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            run_folder = root / "A05N" / "PRE-CLIENTES 26_10_2026 18_13_24"
            run_folder.mkdir(parents=True)
            montar(run_folder)
            mds = root / "Unico.mds"
            mds.write_text(MDS, encoding="utf-8")
            _r, payload = run_shadow_pipeline(run_folder=run_folder, mds_path=mds, output_root=root / "s", vm_name="a05n")
        return payload

    def test_zip_com_nome_rar_e_extraido(self):
        def montar(pasta: Path):
            with zipfile.ZipFile(pasta / "1.3.7-26_10_2026-18_24_31.RAR", "w") as zf:
                zf.writestr("InformacaoErro.txt", "Foi excedido o tempo limite")

        payload = self._rodar(montar)
        self.assertEqual(1, len(payload["falhas"]))
        self.assertEqual([], [e for e in payload.get("erros_processamento") or [] if e["etapa"] == "extracao"])
        self.assertIn("tempo limite", str(payload["falhas"][0]))

    def test_compactado_que_nao_abre_vira_falha_com_o_motivo(self):
        def montar(pasta: Path):
            (pasta / "1.6.20-26_10_2026-23_33_20.RAR").write_bytes(b"nao e um compactado")

        payload = self._rodar(montar)
        self.assertEqual(1, len(payload["falhas"]))
        self.assertIn("Nao foi possivel abrir o log compactado", str(payload["falhas"][0]))
        self.assertEqual(["extracao"], [e["etapa"] for e in payload["erros_processamento"]])

    def test_erro_ao_analisar_o_caso_nao_some_com_a_falha(self):
        def montar(pasta: Path):
            with zipfile.ZipFile(pasta / "1.3.7-26_10_2026-18_24_31.zip", "w") as zf:
                zf.writestr("InformacaoErro.txt", "quebrou")

        original = pipeline.analyze_failure
        chamadas = []

        def primeira_falha(**kwargs):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise PermissionError("arquivo travado")
            return original(**kwargs)

        with mock.patch.object(pipeline, "analyze_failure", side_effect=primeira_falha):
            payload = self._rodar(montar)
        self.assertEqual(1, len(payload["falhas"]))
        self.assertIn("Nao foi possivel analisar", str(payload["falhas"][0]))
        self.assertEqual(["analise"], [e["etapa"] for e in payload["erros_processamento"]])
    def test_rar_registrado_pelo_tc_que_nao_chegou_vira_falha(self):
        def montar(pasta: Path):
            with zipfile.ZipFile(pasta / "1.3.7-26_10_2026-18_24_31.RAR", "w") as zf:
                zf.writestr("InformacaoErro.txt", "quebrou")
            # Lista do TC (UTF-8 com BOM): o 1.3.7 chegou, o 1.6.20 nao; linha repetida conta uma vez.
            (pasta / "AgenteTC_Ocorrencias.txt").write_text(
                "1.3.7-26_10_2026-18_24_31.RAR\r\n1.6.20-26_10_2026-23_33_20.RAR\r\n1.6.20-26_10_2026-23_33_20.RAR\r\n",
                encoding="utf-8-sig",
                newline="",
            )

        payload = self._rodar(montar)
        self.assertEqual(2, len(payload["falhas"]))
        ausente = [f for f in payload["falhas"] if "1.6.20" in str(f)]
        self.assertEqual(1, len(ausente))
        self.assertIn("nao chegou a pasta da rodagem", str(ausente[0]))
        self.assertEqual(["compactado_ausente"], [e["etapa"] for e in payload["erros_processamento"]])

    def test_sem_a_lista_do_tc_nada_muda(self):
        def montar(pasta: Path):
            with zipfile.ZipFile(pasta / "1.3.7-26_10_2026-18_24_31.RAR", "w") as zf:
                zf.writestr("InformacaoErro.txt", "quebrou")

        payload = self._rodar(montar)
        self.assertEqual(1, len(payload["falhas"]))
        self.assertEqual([], payload.get("erros_processamento") or [])


class ParesDeComparacaoTests(unittest.TestCase):
    def test_mesmo_nome_em_pastas_diferentes_sao_dois_pares(self):
        from agent_tc_core.parser import find_comparisons

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            arquivos = []
            for empresa in ("EmpA", "EmpB"):
                pasta = Path(temp_dir) / empresa
                pasta.mkdir()
                for lado, texto in (("Antigo", "1"), ("Atual", "2")):
                    arquivo = pasta / f"Relatorio_{lado}.txt"
                    arquivo.write_text(texto + empresa, encoding="utf-8")
                    arquivos.append(arquivo)
            pares = find_comparisons(arquivos)
            self.assertEqual(["EmpA", "EmpB"], sorted(p.atual.parent.name for p in pares))
            self.assertTrue(all(p.base.parent == p.atual.parent for p in pares))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from agent_tc_core.ai_grouping import (
    AiGroupingBusyError,
    AiGroupingError,
    AiGroupingValidationError,
    FallbackAiClient,
    GeminiChatCompletionsClient,
    ai_client_from_env,
    _strip_json_fence,
    group_failures_in_batches,
    validate_ai_grouping_response,
)
from agent_tc_core.api_server import AgentTcApi


class FakeRepository:
    def __init__(self) -> None:
        self.jobs = []
        self.persisted = None
        self.grouped = False

    def run(self, run_id):
        return {"id": run_id, "module_id": "mod_contabil", "module_name": "Contabil", "version": "TESTE", "vm_name": "a08"}

    def failures(self, run_id):
        return [
            {"id": "falha-1", "id_caso_teste": "3.1", "nome_mds": "Caso 1", "status": "test_break", "erro_principal": "Access violation"},
            {"id": "falha-2", "id_caso_teste": "3.2", "nome_mds": "Caso 2", "status": "difference", "erro_principal": "Relatorio diferente"},
        ]

    def evidences(self, run_id):
        return []

    def report_differences(self, run_id):
        return []

    def ai_grouping_status(self, run_id):
        return {"run_id": run_id, "status": "completed" if self.grouped else "not_requested", "grouped": self.grouped}

    def save_ai_job(self, row):
        self.jobs.append(dict(row))

    def persist_ai_grouping(self, run_id, job_id, rows):
        self.persisted = rows
        self.grouped = True


class FakeOpenAIClient:
    model = "fake-model"

    def group_failures(self, ai_input):
        first_id = ai_input["falhas"][0]["id"]
        second_id = ai_input["falhas"][1]["id"]
        response = {
            "clusters": [
                {
                    "titulo_causa": "Falha de acesso",
                    "assinatura_tecnica": "access_violation",
                    "classificacao": "Quebra",
                    "confianca": 95,
                    "falhas": [first_id],
                    "justificativa": "As mensagens indicam falha de acesso.",
                    "proximos_passos": ["Revisar o ponto de acesso."],
                },
                {
                    "titulo_causa": "Diferenca de relatorio",
                    "assinatura_tecnica": "relatorio_diferente",
                    "classificacao": "Diferenca",
                    "confianca": 90,
                    "falhas": [second_id],
                    "justificativa": "O relatorio atual diverge da base.",
                    "proximos_passos": [],
                },
            ]
        }
        return response, {"id": "resp_fake", "output_text": "{}"}


class FakeBatchClient:
    model = "fake-batch-model"
    provider = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_ids = []

    def group_failures(self, ai_input):
        self.calls += 1
        self.seen_ids.extend(item["id"] for item in ai_input["falhas"])
        return {
            "clusters": [
                {
                    "titulo_causa": "Lote",
                    "assinatura_tecnica": "causa_lote",
                    "classificacao": "Quebra",
                    "confianca": 90,
                    "falhas": [item["id"] for item in ai_input["falhas"]],
                    "justificativa": "Agrupamento do lote.",
                    "proximos_passos": [],
                }
            ]
        }, {"batch": self.calls}


class AiGroupingTests(unittest.TestCase):
    def setUp(self):
        self.ai_input = {"falhas": [{"id": "a"}, {"id": "b"}]}

    def test_rejects_unknown_failure(self):
        response = {
            "clusters": [{"titulo_causa": "X", "assinatura_tecnica": "causa_x", "classificacao": "Quebra", "confianca": 80, "falhas": ["x"], "justificativa": "Motivo", "proximos_passos": []}]
        }
        with self.assertRaises(AiGroupingValidationError):
            validate_ai_grouping_response(response, self.ai_input)

    def test_rejects_failure_in_two_clusters(self):
        cluster = {"titulo_causa": "X", "assinatura_tecnica": "causa_x", "classificacao": "Quebra", "confianca": 80, "falhas": ["a"], "justificativa": "Motivo", "proximos_passos": []}
        second = dict(cluster, assinatura_tecnica="causa_y")
        with self.assertRaises(AiGroupingValidationError):
            validate_ai_grouping_response({"clusters": [cluster, second]}, self.ai_input)

    def test_rejects_missing_failure(self):
        response = {
            "clusters": [{"titulo_causa": "X", "assinatura_tecnica": "causa_x", "classificacao": "Quebra", "confianca": 80, "falhas": ["a"], "justificativa": "Motivo", "proximos_passos": []}]
        }
        with self.assertRaises(AiGroupingValidationError):
            validate_ai_grouping_response(response, self.ai_input)

    def test_extracts_json_from_model_text(self):
        text = 'Claro, segue o JSON:\n{"clusters":[{"falhas":["a"]}]}\nFim.'
        self.assertEqual('{"clusters":[{"falhas":["a"]}]}', _strip_json_fence(text))

    def test_groups_in_batches_cover_all_failures(self):
        ai_input = {"falhas": [{"id": f"f{i}"} for i in range(6)]}
        client = FakeBatchClient()
        response, raw = group_failures_in_batches(client, ai_input, batch_size=2)
        self.assertEqual(3, client.calls)
        self.assertEqual(["f001", "f002", "f001", "f002", "f001", "f002"], client.seen_ids)
        self.assertEqual(1, len(response["clusters"]))
        grouped_ids = [failure_id for cluster in response["clusters"] for failure_id in cluster["falhas"]]
        self.assertEqual([f"f{i}" for i in range(6)], grouped_ids)
        self.assertEqual("batched", raw["mode"])

    def test_api_real_flow_and_idempotence(self):
        repository = FakeRepository()
        with tempfile.TemporaryDirectory() as temp_dir:
            api = AgentTcApi(
                Path(temp_dir),
                repository=repository,
                openai_client=FakeOpenAIClient(),
                require_ai_auth=False,
            )
            status, payload = api.route_post("/runs/run-1/ai-group", {"dry_run": False})
            self.assertEqual(200, status)
            self.assertEqual(2, payload["grupos"])
            self.assertEqual("completed", repository.jobs[-1]["status"])
            self.assertEqual(2, len(repository.persisted["links"]))

            status, payload = api.route_post("/runs/run-1/ai-group", {"dry_run": False})
            self.assertEqual(409, status)
            self.assertEqual("already_grouped", payload["error"])

    def test_real_flow_requires_authentication_by_default(self):
        repository = FakeRepository()
        with tempfile.TemporaryDirectory() as temp_dir:
            api = AgentTcApi(Path(temp_dir), repository=repository, openai_client=FakeOpenAIClient())
            status, payload = api.route_post("/runs/run-1/ai-group", {"dry_run": False})
            self.assertEqual(401, status)
            self.assertEqual("unauthorized", payload["error"])
            self.assertEqual([], repository.jobs)

    def test_dry_run_requires_authentication_by_default(self):
        repository = FakeRepository()
        with tempfile.TemporaryDirectory() as temp_dir:
            api = AgentTcApi(Path(temp_dir), repository=repository, openai_client=FakeOpenAIClient())
            status, payload = api.route_post("/runs/run-1/ai-group", {"dry_run": True})
            self.assertEqual(401, status)
            self.assertEqual("unauthorized", payload["error"])
            self.assertFalse((Path(temp_dir) / "ai_grouping").exists())


class ScriptedClient:
    """Responde em ordem o roteiro: excecao (levanta) ou "ok" (agrupa tudo num cluster)."""

    def __init__(self, provider, model, script):
        self.provider = provider
        self.model = model
        self.script = list(script)
        self.calls = 0

    def group_failures(self, ai_input):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return {
            "clusters": [{
                "titulo_causa": "Tudo", "assinatura_tecnica": "causa_unica", "classificacao": "Quebra", "confianca": 90,
                "falhas": [item["id"] for item in ai_input["falhas"]], "justificativa": "Motivo.", "proximos_passos": [],
            }]
        }, {"model": self.model}


def busy():
    return AiGroupingBusyError("Gemini HTTP 503: high demand")


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.ai_input = {"falhas": [{"id": "a"}, {"id": "b"}]}
        self.sleeps = []

    def chain(self, *levels):
        return FallbackAiClient(list(levels), retry_seconds=15, sleep=self.sleeps.append)

    def test_ocupado_repete_no_mesmo_modelo_a_cada_15s(self):
        main = ScriptedClient("gemini", "gemini-3.5-flash", [busy(), busy(), "ok"])
        client = self.chain((main, 2))
        client.group_failures(self.ai_input)
        self.assertEqual(3, main.calls)
        self.assertEqual([15, 15], self.sleeps)
        self.assertEqual([("gemini", "gemini-3.5-flash")], client.used)

    def test_esgotou_as_tentativas_vai_para_a_openai_e_depois_para_o_gemini_leve(self):
        main = ScriptedClient("gemini", "gemini-3.5-flash", [busy(), busy(), busy()])
        openai = ScriptedClient("openai", "gpt-5.4-mini", [busy()])
        lite = ScriptedClient("gemini", "gemini-3.5-flash-lite", [busy(), "ok"])
        client = self.chain((main, 2), (openai, 0), (lite, 1))
        client.group_failures(self.ai_input)
        self.assertEqual((3, 1, 2), (main.calls, openai.calls, lite.calls))
        self.assertEqual([15, 15, 15], self.sleeps)  # sem espera ao trocar de nivel
        self.assertEqual([("gemini", "gemini-3.5-flash-lite (reserva)")], client.used)

    def test_resposta_invalida_passa_ao_proximo_nivel_sem_esperar(self):
        main = ScriptedClient("gemini", "gemini-3.5-flash", [AiGroupingValidationError("JSON invalido")])
        lite = ScriptedClient("gemini", "gemini-3.5-flash-lite", ["ok"])
        client = self.chain((main, 2), (lite, 1))
        client.group_failures(self.ai_input)
        self.assertEqual((1, 1), (main.calls, lite.calls))
        self.assertEqual([], self.sleeps)

    def test_todos_falharam_explica_cada_modelo(self):
        main = ScriptedClient("gemini", "gemini-3.5-flash", [busy(), busy(), busy()])
        lite = ScriptedClient("gemini", "gemini-3.5-flash-lite", [AiGroupingError("Gemini HTTP 404: sem modelo")])
        with self.assertRaises(AiGroupingError) as ctx:
            self.chain((main, 2), (lite, 1)).group_failures(self.ai_input)
        self.assertIn("Nenhum modelo conseguiu agrupar", str(ctx.exception))
        self.assertIn("gemini-3.5-flash-lite: Gemini HTTP 404", str(ctx.exception))

    def test_503_do_gemini_conta_como_ocupado_e_400_nao(self):
        client = GeminiChatCompletionsClient(api_key="x")
        for code, esperado in ((503, AiGroupingBusyError), (429, AiGroupingBusyError), (404, AiGroupingError)):
            erro = HTTPError("u", code, "m", {}, io.BytesIO(b"detalhe"))
            with mock.patch("agent_tc_core.ai_grouping.urlopen", side_effect=erro):
                with self.assertRaises(esperado) as ctx:
                    client.group_failures(self.ai_input)
            self.assertEqual(esperado is AiGroupingBusyError, isinstance(ctx.exception, AiGroupingBusyError))

    def test_niveis_do_env(self):
        limpo = {k: "" for k in ("AI_PROVIDER", "OPENAI_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FALLBACK_MODEL", "AI_RETRIES")}
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, limpo):
            env = Path(temp_dir) / "docker.env"
            env.write_text("AI_PROVIDER=gemini\nGEMINI_API_KEY=g\nGEMINI_MODEL=gemini-3.5-flash\n", encoding="utf-8")
            sem_openai = ai_client_from_env(env)
            self.assertEqual([("gemini-3.5-flash", 2), ("gemini-3.5-flash-lite", 1)], [(c.model, n) for c, n in sem_openai.levels])
            env.write_text(env.read_text(encoding="utf-8") + "OPENAI_API_KEY=o\n", encoding="utf-8")
            com_openai = ai_client_from_env(env)
            self.assertEqual(["gemini-3.5-flash", "gpt-5.4-mini", "gemini-3.5-flash-lite"], [c.model for c, _ in com_openai.levels])
            env.write_text(env.read_text(encoding="utf-8") + "GEMINI_FALLBACK_MODEL=\n", encoding="utf-8")
            self.assertEqual(2, len(ai_client_from_env(env).levels))  # reserva desligada

    def test_job_grava_o_modelo_que_respondeu(self):
        repository = FakeRepository()
        main = ScriptedClient("gemini", "gemini-3.5-flash", [busy(), busy(), busy()])
        lite = ScriptedClient("gemini", "gemini-3.5-flash-lite", ["ok"])
        with tempfile.TemporaryDirectory() as temp_dir:
            api = AgentTcApi(Path(temp_dir), repository=repository, openai_client=self.chain((main, 2), (lite, 1)), require_ai_auth=False)
            status, _ = api.route_post("/runs/run-1/ai-group", {"dry_run": False})
        self.assertEqual(200, status)
        self.assertEqual("gemini-3.5-flash", repository.jobs[0]["model"])  # enquanto roda
        self.assertEqual("gemini-3.5-flash-lite (reserva)", repository.jobs[-1]["model"])


if __name__ == "__main__":
    unittest.main()

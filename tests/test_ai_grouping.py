from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tc_core.ai_grouping import AiGroupingValidationError, validate_ai_grouping_response
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
        response = {
            "clusters": [
                {
                    "titulo_causa": "Falha de acesso",
                    "assinatura_tecnica": "access_violation",
                    "classificacao": "Quebra",
                    "confianca": 95,
                    "falhas": ["falha-1"],
                    "justificativa": "As mensagens indicam falha de acesso.",
                    "proximos_passos": ["Revisar o ponto de acesso."],
                },
                {
                    "titulo_causa": "Diferenca de relatorio",
                    "assinatura_tecnica": "relatorio_diferente",
                    "classificacao": "Diferenca",
                    "confianca": 90,
                    "falhas": ["falha-2"],
                    "justificativa": "O relatorio atual diverge da base.",
                    "proximos_passos": [],
                },
            ]
        }
        return response, {"id": "resp_fake", "output_text": "{}"}


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


if __name__ == "__main__":
    unittest.main()

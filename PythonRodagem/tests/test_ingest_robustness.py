from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_tc_core.api_server import AgentTcApi
from agent_tc_core.canonical_repository import CanonicalRepository
from agent_tc_core.postgres_repository import PostgresRepository
from agent_tc_core.sqlite_repository import SQLiteRepository
from test_pipeline_regressions import _payload_with_repeated_case


RUN_ID = "rod_a08_PROXIMA_20260709_205437"


def _matches(row, query):
    for column, raw in query.items():
        if column in {"select", "order", "limit", "offset", "on_conflict"}:
            continue
        value = row.get(column)
        if raw.startswith("eq."):
            if str(value) != raw[3:]:
                return False
        elif raw.startswith("in.(") and raw.endswith(")"):
            if str(value) not in raw[4:-1].split(","):
                return False
        elif raw == "is.null":
            if value is not None:
                return False
        elif raw == "not.is.null":
            if value is None:
                return False
        else:
            raise NotImplementedError(raw)
    return True


class InMemoryCanonicalRepository(CanonicalRepository):
    """Emula o banco no formato de filtros usado pelo CanonicalRepository."""

    backend_name = "memory"

    def __init__(self, env_path):
        super().__init__(env_path)
        self.tables = defaultdict(dict)

    def _rest_json(self, method, path, body=None, *, query=None, extra_headers=None):
        table = path.strip("/").removeprefix(self.table_prefix)
        query = dict(query or {})
        if method == "POST":
            keys = [key.strip() for key in query.get("on_conflict", "id").split(",")]
            for row in body:
                key = tuple(row.get(name) for name in keys)
                self.tables[table][key] = {**self.tables[table].get(key, {}), **row}
            return None
        rows = [row for row in self.tables[table].values() if _matches(row, query)]
        for item in reversed([part for part in (query.get("order") or "").split(",") if part]):
            column, _, direction = item.partition(".")
            rows.sort(key=lambda row: str(row.get(column) or ""), reverse=direction == "desc")
        if method == "GET":
            offset = int(query.get("offset") or 0)
            limit = query.get("limit")
            rows = rows[offset : offset + int(limit)] if limit else rows[offset:]
            select = query.get("select")
            if select and select != "*":
                rows = [{column: row.get(column) for column in select.split(",")} for row in rows]
            return [dict(row) for row in rows]
        if method == "DELETE":
            for key, row in list(self.tables[table].items()):
                if _matches(row, query):
                    del self.tables[table][key]
            return None
        if method == "PATCH":
            for row in rows:
                row.update(body)
            return [dict(row) for row in rows]
        raise NotImplementedError(method)

    def ids(self, table):
        return sorted(str(row["id"]) for row in self.tables[table].values())


def _write_env(root: Path, extra: str = "") -> Path:
    env_path = root / ".env"
    env_path.write_text(
        f"AGENT_TC_STORAGE=local\nAGENT_TC_STORAGE_ROOT={root / 'storage'}\n{extra}",
        encoding="utf-8",
    )
    return env_path


def _evidence(root: Path, failure_id: str, name: str, index: int) -> dict:
    source = root / "vm" / failure_id / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("conteudo " + name, encoding="utf-8")
    return {
        "id_evidencia": f"ev_{failure_id}_{index:04d}",
        "fk_falha": failure_id,
        "tipo_arquivo": "texto",
        "caminho_evidencia": str(source),
        "storage_path": f"contabil/PROXIMA/{RUN_ID}/{failure_id}/textos/{name}",
        "mime_type": "text/plain",
        "extensao": ".txt",
        "nome_arquivo": name,
    }


def _payload(root: Path, failure_ids: list[str], *, missing_evidence: bool = False, run_id: str = RUN_ID) -> dict:
    falhas = [
        {
            "id_falha": failure_id,
            "id_caso_teste": "3.1." + str(index),
            "nome_mds": "Caso " + failure_id,
            "arquivo_origem": failure_id + ".rar",
            "tipo_detectado_python": "Quebra de testes",
        }
        for index, failure_id in enumerate(failure_ids, 1)
    ]
    evidencias = [_evidence(root, failure_id, failure_id + ".txt", index) for index, failure_id in enumerate(failure_ids, 1)]
    if missing_evidence:
        evidencias.append({**evidencias[0], "id_evidencia": "ev_sem_arquivo", "caminho_evidencia": str(root / "nao_existe.txt")})
    for item in evidencias:
        item["storage_path"] = item["storage_path"].replace(RUN_ID, run_id)
    return {
        "rodagem": {"id_rodagem": run_id, "versao": "PROXIMA", "vm_name": "a08", "data_inicio": "2026-07-09T20:54:37-03:00"},
        "modulo": {"id_modulo": "mod_contabil", "nome": "Contabil"},
        "falhas": falhas,
        "evidencias": evidencias,
        "diferencas_relatorio": [],
        "agrupamentos_shadow": [
            {"id_cluster": "cluster_" + run_id + "_001", "titulo_causa": "Quebra", "status": "Quebra de testes", "falhas": list(failure_ids)}
        ],
        "testcase_hierarchy": [],
        "atrasos_rodagem": [],
    }


class ReimportTests(unittest.TestCase):
    def test_reimport_with_fewer_failures_removes_stale_rows_and_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = InMemoryCanonicalRepository(_write_env(root))
            repository.import_payload(_payload(root, ["falha_1", "falha_2"]))
            stale_file = root / "storage" / "contabil" / "PROXIMA" / RUN_ID / "falha_2" / "textos" / "falha_2.txt"
            self.assertTrue(stale_file.exists())

            result = repository.import_payload(_payload(root, ["falha_1"]))

            self.assertTrue(result["verification"]["ok"])
            self.assertEqual(["falha_1"], repository.ids("occurrences"))
            self.assertEqual(["ev_falha_1_0001"], repository.ids("evidence_files"))
            self.assertFalse(stale_file.exists())
            self.assertTrue((root / "storage" / "contabil" / "PROXIMA" / RUN_ID / "falha_1" / "textos" / "falha_1.txt").exists())
            self.assertEqual("analyzed", repository.tables["runs"][(RUN_ID,)]["status"])

    def test_run_shows_processing_errors_of_the_latest_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = InMemoryCanonicalRepository(_write_env(root))
            erro = {"arquivo": "3.1.9-x.RAR", "etapa": "compactado_ausente", "mensagem": "nao chegou"}
            repository.import_payload({**_payload(root, ["falha_1"]), "erros_processamento": [erro]})
            self.assertEqual([erro], repository.run(RUN_ID)["erros_processamento"])
            self.assertEqual([[erro]], [r["erros_processamento"] for r in repository.runs()])

            # Reimportada sem problemas: vale a importacao mais recente.
            for batch in repository.tables["ingestion_batches"].values():
                batch["created_at"] = "2000-01-01T00:00:00+00:00"
            repository.import_payload(_payload(root, ["falha_1", "falha_2"]))
            self.assertEqual([], repository.run(RUN_ID)["erros_processamento"])
            self.assertEqual([[]], [r["erros_processamento"] for r in repository.runs()])

    def test_reimport_keeps_ai_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = InMemoryCanonicalRepository(_write_env(root))
            repository.import_payload(_payload(root, ["falha_1"]))
            repository._upsert(
                "ai_groups",
                [{"id": "grp_ai_1", "run_id": RUN_ID, "ai_analysis_job_id": "aij_1", "title": "IA", "status": "grouped"}],
            )

            repository.import_payload(_payload(root, ["falha_1"]))

            self.assertIn("grp_ai_1", repository.ids("ai_groups"))

    def test_missing_evidence_fails_the_import_instead_of_dropping_it(self):
        # Antes a evidencia era pulada (resposta ok) e, ao reprocessar, a copia boa gravada antes era apagada.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = InMemoryCanonicalRepository(_write_env(root))
            repository.import_payload(_payload(root, ["falha_1"], run_id="rod_1"))
            antes = dict(repository.tables["evidence_files"])

            with self.assertRaisesRegex(RuntimeError, "nao foi gravada"):
                repository.import_payload(_payload(root, ["falha_1"], missing_evidence=True, run_id="rod_1"))

            self.assertEqual(antes, repository.tables["evidence_files"])
            self.assertEqual("import_failed", repository.tables["runs"][("rod_1",)]["status"])
            second = repository.import_payload(_payload(root, ["falha_9"], run_id="rod_2"))
            self.assertTrue(second["verification"]["ok"])
            self.assertEqual("analyzed", repository.tables["runs"][("rod_2",)]["status"])


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))

    def executemany(self, sql, params):
        self.conn.statements.append((sql, list(params)))

    def fetchall(self):
        return []

    def fetchone(self):
        return {"count": 0}


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def wrote(self, table, value=None):
        return any(
            sql.startswith("INSERT INTO") and f'"agent_tc_{table}"' in sql and (value is None or value in str(params))
            for sql, params in self.statements
        )


class PostgresTransactionTests(unittest.TestCase):
    def _repository(self, root: Path):
        env_path = _write_env(root, "POSTGRES_DSN=postgresql://fake/agent_tc\n")
        repository = PostgresRepository(env_path=env_path)
        connections = []

        def connect():
            connections.append(FakeConnection())
            return connections[-1]

        repository.connect = connect
        return repository, connections

    def test_failed_import_rolls_back_and_records_failure_outside_transaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository, connections = self._repository(root)
            payload = _payload(root, ["falha_1"])

            # O banco falso nao devolve linhas, entao a verificacao pos-importacao falha.
            with self.assertRaises(RuntimeError):
                repository.import_payload(payload)

            transactions = [conn for conn in connections if conn.wrote("occurrences")]
            self.assertEqual(1, len(transactions))
            transaction = transactions[0]
            self.assertTrue(transaction.rolled_back)
            self.assertFalse(transaction.committed)
            self.assertTrue(transaction.wrote("evidence_files"))
            failure_records = [conn for conn in connections if conn.wrote("runs", "import_failed")]
            self.assertTrue(failure_records)
            self.assertTrue(all(conn.committed and conn is not transaction for conn in failure_records))
            self.assertTrue(all(conn.closed for conn in connections))

    def test_successful_import_commits_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository, connections = self._repository(root)
            payload = _payload(root, [])
            payload["agrupamentos_shadow"] = []

            repository.import_payload(payload)

            transactions = [conn for conn in connections if conn.wrote("ingestion_batches", "completed")]
            self.assertEqual(1, len(transactions))
            self.assertTrue(transactions[0].committed)
            self.assertFalse(transactions[0].rolled_back)
            self.assertTrue(transactions[0].wrote("runs", "analyzed"))
            self.assertIsNone(repository._transaction_connection())


class StaleAiJobTests(unittest.TestCase):
    class Client:
        provider = "fake"
        model = "fake-model"

        def group_failures(self, ai_input):
            cluster = {
                "titulo_causa": "Causa",
                "assinatura_tecnica": "causa_unica",
                "classificacao": "Quebra",
                "confianca": 80,
                "falhas": [failure["id"] for failure in ai_input["falhas"]],
                "justificativa": "Mesma causa.",
                "proximos_passos": [],
            }
            return {"clusters": [cluster]}, {"fake": True}

    def _api(self, root: Path):
        repository = SQLiteRepository(root / "agent-tc.sqlite")
        repository.import_payload(_payload_with_repeated_case())
        api = AgentTcApi(root, repository=repository, openai_client=self.Client(), require_ai_auth=False)
        return api, repository

    def _running_job(self, repository, minutes_ago: int):
        created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        repository.save_ai_job(
            {
                "id": "aij_interrompido",
                "run_id": RUN_ID,
                "provider": "fake",
                "model": "fake-model",
                "request_json": {},
                "response_json": {},
                "status": "running",
                "error_message": None,
                "created_at": created,
                "completed_at": None,
            }
        )

    def test_stale_running_job_no_longer_blocks_grouping(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            api, repository = self._api(Path(temp_dir))
            self._running_job(repository, minutes_ago=120)

            status, payload = api.route_post(f"/runs/{RUN_ID}/ai-group", {"dry_run": False})

            self.assertEqual(200, status, payload)
            conn = repository.connect()
            try:
                old = conn.execute("SELECT status, error_message FROM ai_analysis_jobs WHERE id = 'aij_interrompido'").fetchone()
            finally:
                conn.close()
            self.assertEqual("failed", old["status"])
            self.assertIn("interrompido", old["error_message"])

    def test_recent_running_job_still_blocks_grouping(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            api, repository = self._api(Path(temp_dir))
            self._running_job(repository, minutes_ago=5)

            status, payload = api.route_post(f"/runs/{RUN_ID}/ai-group", {"dry_run": False})

            self.assertEqual(409, status)
            self.assertEqual("already_processing", payload["error"])


class IngestStagingTests(unittest.TestCase):
    class Repository:
        def __init__(self, fail=False):
            self.fail = fail
            self.seen_paths = []

        def import_payload(self, payload, source="payload"):
            for evidence in payload["evidencias"]:
                path = Path(evidence["caminho_evidencia"])
                self.seen_paths.append(path)
                assert path.read_bytes() == b"evidencia"
            if self.fail:
                raise ValueError("falha simulada")
            return {"ok": True}

    def _post(self, root: Path, repository):
        env_path = root / ".env"
        env_path.write_text("AGENT_TC_BRIDGE_TOKEN=token\n", encoding="utf-8")
        api = AgentTcApi(root, repository=repository, env_path=env_path)
        content = b"evidencia"
        body = {
            "payload": {"rodagem": {"id_rodagem": RUN_ID}, "evidencias": [{"id_evidencia": "ev_1", "nome_arquivo": "erro.txt"}]},
            "files": [
                {
                    "id_evidencia": "ev_1",
                    "nome_arquivo": "erro.txt",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            ],
        }
        return api.route_post("/ingest", body, "Bearer token")

    def test_staged_files_are_removed_after_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = self.Repository()

            status, payload = self._post(root, repository)

            self.assertEqual(201, status, payload)
            self.assertEqual(1, len(repository.seen_paths))
            self.assertFalse(repository.seen_paths[0].exists())
            self.assertEqual([], list((root / "api_ingest_files").glob("*")) if (root / "api_ingest_files").exists() else [])

    def test_staged_files_are_removed_when_import_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = self.Repository(fail=True)

            status, payload = self._post(root, repository)

            self.assertEqual(400, status)
            self.assertFalse(repository.seen_paths[0].exists())


if __name__ == "__main__":
    unittest.main()

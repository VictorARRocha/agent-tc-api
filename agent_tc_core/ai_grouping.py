from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_TEXT_LENGTH = 1600
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
GEMINI_OPENAI_CHAT_COMPLETIONS_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
CONTRACT_VERSION = "agent-tc-ai-grouping-v1"
SIGNATURE_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class AiGroupingError(RuntimeError):
    pass


class AiGroupingValidationError(AiGroupingError):
    pass


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        timeout: int = 120,
        max_output_tokens: int = 6000,
    ):
        if not api_key:
            raise AiGroupingError("OPENAI_API_KEY nao configurada")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> "OpenAIResponsesClient":
        env = _read_env(env_path)
        return cls(
            api_key=env.get("OPENAI_API_KEY", ""),
            model=env.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
            timeout=_positive_int(env.get("OPENAI_TIMEOUT_SECONDS"), 120),
            max_output_tokens=_positive_int(env.get("OPENAI_MAX_OUTPUT_TOKENS"), 6000),
        )

    def group_failures(self, ai_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": _system_prompt()}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(ai_input, ensure_ascii=False, separators=(",", ":")),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "agent_tc_ai_grouping",
                    "strict": True,
                    "schema": response_json_schema(),
                }
            },
        }
        request = Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AiGroupingError(f"OpenAI HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise AiGroupingError("Falha de conexao com a OpenAI: " + str(exc)) from exc

        output_text = _extract_output_text(raw_response)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise AiGroupingValidationError("A OpenAI nao retornou JSON valido") from exc
        return parsed, raw_response


class GeminiChatCompletionsClient:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        timeout: int = 120,
        max_output_tokens: int = 6000,
    ):
        if not api_key:
            raise AiGroupingError("GEMINI_API_KEY nao configurada")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> "GeminiChatCompletionsClient":
        env = _read_env(env_path)
        return cls(
            api_key=env.get("GEMINI_API_KEY", ""),
            model=env.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
            timeout=_positive_int(env.get("GEMINI_TIMEOUT_SECONDS") or env.get("AI_TIMEOUT_SECONDS"), 120),
            max_output_tokens=_positive_int(
                env.get("GEMINI_MAX_OUTPUT_TOKENS") or env.get("AI_MAX_OUTPUT_TOKENS"),
                6000,
            ),
        )

    def group_failures(self, ai_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _system_prompt() + "\n" + _json_only_prompt()},
                {"role": "user", "content": json.dumps(ai_input, ensure_ascii=False, separators=(",", ":"))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_tc_ai_grouping",
                    "strict": True,
                    "schema": response_json_schema(),
                },
            },
            "max_tokens": self.max_output_tokens,
        }
        request = Request(
            GEMINI_OPENAI_CHAT_COMPLETIONS_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400 and "response_format" in detail:
                return self._group_failures_without_schema(ai_input)
            raise AiGroupingError(f"Gemini HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise AiGroupingError("Falha de conexao com o Gemini: " + str(exc)) from exc

        output_text = _extract_chat_completion_text(raw_response, provider="Gemini")
        try:
            parsed = json.loads(_strip_json_fence(output_text))
        except json.JSONDecodeError as exc:
            raise AiGroupingValidationError("O Gemini nao retornou JSON valido") from exc
        return parsed, raw_response

    def _group_failures_without_schema(self, ai_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _system_prompt() + "\n" + _json_only_prompt()},
                {"role": "user", "content": json.dumps(ai_input, ensure_ascii=False, separators=(",", ":"))},
            ],
            "max_tokens": self.max_output_tokens,
        }
        request = Request(
            GEMINI_OPENAI_CHAT_COMPLETIONS_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AiGroupingError(f"Gemini HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise AiGroupingError("Falha de conexao com o Gemini: " + str(exc)) from exc

        output_text = _extract_chat_completion_text(raw_response, provider="Gemini")
        try:
            parsed = json.loads(_strip_json_fence(output_text))
        except json.JSONDecodeError as exc:
            raise AiGroupingValidationError("O Gemini nao retornou JSON valido") from exc
        return parsed, raw_response


def ai_client_from_env(env_path: str | Path | None = None) -> Any:
    env = _read_env(env_path)
    provider = (env.get("AI_PROVIDER") or "openai").strip().lower()
    if provider == "openai":
        return OpenAIResponsesClient.from_env(env_path)
    if provider == "gemini":
        return GeminiChatCompletionsClient.from_env(env_path)
    raise AiGroupingError("AI_PROVIDER invalido. Use openai ou gemini")


def build_ai_grouping_input(repository: Any, run_id: str) -> dict[str, Any]:
    run = repository.run(run_id)
    if not run:
        raise ValueError("Rodagem nao encontrada: " + run_id)

    failures = repository.failures(run_id)
    evidences = repository.evidences(run_id)
    differences = repository.report_differences(run_id) if hasattr(repository, "report_differences") else []

    evidences_by_failure: dict[str, list[dict[str, Any]]] = {}
    for evidence in evidences:
        failure_id = str(evidence.get("falha_id") or evidence.get("fk_falha") or evidence.get("occurrence_id") or "")
        if failure_id:
            evidences_by_failure.setdefault(failure_id, []).append(evidence)

    differences_by_failure: dict[str, list[dict[str, Any]]] = {}
    for difference in differences:
        failure_id = str(difference.get("falha_id") or difference.get("fk_falha") or difference.get("occurrence_id") or "")
        if failure_id:
            differences_by_failure.setdefault(failure_id, []).append(difference)

    return {
        "contract_version": CONTRACT_VERSION,
        "task": "Agrupar falhas e diferencas por causa tecnica ou funcional sem acessar arquivos externos.",
        "rules": [
            "Usar somente ids de falha recebidos neste payload.",
            "Nao inventar falhas, evidencias, causas ou casos de teste.",
            "Agrupar apenas ocorrencias com causa semelhante.",
            "Toda falha deve aparecer exatamente uma vez.",
            "Uma falha pode aparecer em apenas um cluster.",
            "Usar portugues do Brasil nos textos exibidos ao usuario.",
        ],
        "rodagem": {
            "id": run.get("id") or run.get("id_rodagem") or run_id,
            "sistema": run.get("system") or run.get("sistema"),
            "versao": run.get("version") or run.get("versao"),
            "vm": run.get("vm_name"),
            "modulo": ((run.get("modulo") or {}).get("nome") if isinstance(run.get("modulo"), dict) else run.get("module_name")),
            "module_id": run.get("module_id") or run.get("fk_modulo"),
            "started_at": run.get("started_at") or run.get("data_inicio"),
            "total_occurrences": run.get("total_occurrences") or run.get("total_falhas"),
            "total_executed": run.get("total_executed") or run.get("total_analisados"),
        },
        "falhas": [
            _failure_payload(
                failure,
                evidences_by_failure.get(str(failure.get("id") or failure.get("id_falha")), []),
                differences_by_failure.get(str(failure.get("id") or failure.get("id_falha")), []),
            )
            for failure in failures
        ],
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "failures_count": len(failures),
            "evidences_count": len(evidences),
            "differences_count": len(differences),
        },
    }


def validate_ai_grouping_response(response: Any, ai_input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(response, dict) or set(response) != {"clusters"}:
        raise AiGroupingValidationError("Resposta deve conter somente o campo clusters")
    clusters = response.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise AiGroupingValidationError("Resposta deve conter pelo menos um cluster")

    expected_ids = {str(item.get("id")) for item in ai_input.get("falhas", []) if item.get("id")}
    seen_ids: set[str] = set()
    signatures: set[str] = set()
    normalized_clusters = []
    for index, cluster in enumerate(clusters):
        if not isinstance(cluster, dict):
            raise AiGroupingValidationError(f"Cluster {index} nao e um objeto")
        required = {"titulo_causa", "assinatura_tecnica", "classificacao", "confianca", "falhas", "justificativa", "proximos_passos"}
        if set(cluster) != required:
            raise AiGroupingValidationError(f"Cluster {index} possui campos ausentes ou desconhecidos")
        title = _required_text(cluster["titulo_causa"], f"clusters[{index}].titulo_causa", 160)
        signature = _required_text(cluster["assinatura_tecnica"], f"clusters[{index}].assinatura_tecnica", 120)
        if not SIGNATURE_RE.fullmatch(signature):
            raise AiGroupingValidationError(f"Assinatura tecnica invalida no cluster {index}")
        if signature in signatures:
            raise AiGroupingValidationError("Assinatura tecnica duplicada: " + signature)
        signatures.add(signature)
        classification = _required_text(cluster["classificacao"], f"clusters[{index}].classificacao", 80)
        confidence = cluster["confianca"]
        if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
            raise AiGroupingValidationError(f"Confianca invalida no cluster {index}")
        failure_ids = cluster["falhas"]
        if not isinstance(failure_ids, list) or not failure_ids:
            raise AiGroupingValidationError(f"Cluster {index} nao possui falhas")
        normalized_ids = []
        for failure_id in failure_ids:
            failure_id = str(failure_id)
            if failure_id not in expected_ids:
                raise AiGroupingValidationError("Falha inexistente na rodagem: " + failure_id)
            if failure_id in seen_ids:
                raise AiGroupingValidationError("Falha repetida em mais de um cluster: " + failure_id)
            seen_ids.add(failure_id)
            normalized_ids.append(failure_id)
        steps = cluster["proximos_passos"]
        if not isinstance(steps, list) or len(steps) > 10:
            raise AiGroupingValidationError(f"proximos_passos invalido no cluster {index}")
        normalized_steps = [_required_text(step, f"clusters[{index}].proximos_passos", 500) for step in steps]
        normalized_clusters.append(
            {
                "titulo_causa": title,
                "assinatura_tecnica": signature,
                "classificacao": classification,
                "confianca": confidence,
                "falhas": normalized_ids,
                "justificativa": _required_text(cluster["justificativa"], f"clusters[{index}].justificativa", 1200),
                "proximos_passos": normalized_steps,
            }
        )
    missing = expected_ids - seen_ids
    if missing:
        raise AiGroupingValidationError("Falhas nao agrupadas: " + ", ".join(sorted(missing)))
    return {"clusters": normalized_clusters}


def response_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["clusters"],
        "properties": {
            "clusters": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["titulo_causa", "assinatura_tecnica", "classificacao", "confianca", "falhas", "justificativa", "proximos_passos"],
                    "properties": {
                        "titulo_causa": {"type": "string"},
                        "assinatura_tecnica": {"type": "string"},
                        "classificacao": {"type": "string"},
                        "confianca": {"type": "integer", "minimum": 0, "maximum": 100},
                        "falhas": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "justificativa": {"type": "string"},
                        "proximos_passos": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


def materialize_ai_rows(run: dict[str, Any], job_id: str, response: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    run_id = str(run.get("id") or run.get("id_rodagem"))
    module_id = str(run.get("module_id") or run.get("fk_modulo"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    groups = []
    links = []
    actions = []
    for cluster in response["clusters"]:
        identity = run_id + "|" + cluster["assinatura_tecnica"] + "|" + "|".join(sorted(cluster["falhas"]))
        group_id = "grp_ai_" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        groups.append(
            {
                "id": group_id,
                "run_id": run_id,
                "module_id": module_id,
                "ai_analysis_job_id": job_id,
                "title": cluster["titulo_causa"],
                "technical_signature": cluster["assinatura_tecnica"],
                "classification": cluster["classificacao"],
                "confidence": cluster["confianca"],
                "justification": cluster["justificativa"],
                "status": "grouped",
                "created_at": now,
                "updated_at": now,
            }
        )
        links.extend({"group_id": group_id, "occurrence_id": failure_id, "created_at": now} for failure_id in cluster["falhas"])
        for position, action in enumerate(cluster["proximos_passos"], start=1):
            action_id = "act_ai_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{group_id}|{position}|{action}").hex
            actions.append(
                {
                    "id": action_id,
                    "run_id": run_id,
                    "group_id": group_id,
                    "occurrence_id": None,
                    "category": "ai_grouping",
                    "hypothesis": cluster["justificativa"],
                    "action": action,
                    "confidence": cluster["confianca"],
                    "priority": None,
                    "status": "suggested",
                    "created_at": now,
                    "updated_at": now,
                }
            )
    return {"groups": groups, "links": links, "actions": actions}


def make_job_id(run_id: str, ai_input: dict[str, Any]) -> str:
    stable_input = dict(ai_input)
    stable_input.pop("metadata", None)
    digest = hashlib.sha256(json.dumps(stable_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return "aij_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}|{CONTRACT_VERSION}|{digest}").hex


def write_ai_dry_run(logs_root: str | Path, run_id: str, ai_input: dict[str, Any]) -> Path:
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(logs_root) / "ai_grouping" / safe_run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ai_grouping_input_{stamp}.json"
    out_path.write_text(json.dumps(ai_input, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _failure_payload(failure: dict[str, Any], evidences: list[dict[str, Any]], differences: list[dict[str, Any]]) -> dict[str, Any]:
    failure_id = str(failure.get("id") or failure.get("id_falha") or "")
    error_message = _first_text(failure, "error_message", "erro_principal", "mensagem_principal", "descricao", "log_summary")
    return {
        "id": failure_id,
        "id_caso_teste": failure.get("testcase_node_id") or failure.get("id_caso_teste"),
        "nome_caso": failure.get("testcase_name") or failure.get("nome_mds"),
        "descricao_caso": failure.get("testcase_description") or failure.get("descricao_caso"),
        "grupo": failure.get("group_name") or failure.get("grupo"),
        "hierarquia": failure.get("caminho_hierarquico") or failure.get("full_path_label"),
        "arquivo_origem": failure.get("source_archive_name") or failure.get("arquivo_origem"),
        "tipo_detectado_python": failure.get("status") or failure.get("tipo_detectado_python"),
        "tipo_ocorrencia": failure.get("occurrence_type") or failure.get("tipo_ocorrencia"),
        "mensagem_erro": _short(error_message),
        "assinatura_tecnica_atual": failure.get("technical_signature") or failure.get("hipotese_principal"),
        "evidencias": [_evidence_payload(evidence) for evidence in evidences],
        "resumo_diferencas": [_difference_payload(difference) for difference in differences],
    }


def _evidence_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": evidence.get("id") or evidence.get("id_evidencia"),
        "tipo": evidence.get("file_type") or evidence.get("tipo_arquivo"),
        "papel": evidence.get("file_role") or evidence.get("role"),
        "nome_arquivo": evidence.get("original_name") or evidence.get("nome_arquivo"),
        "mime_type": evidence.get("mime_type"),
        "extensao": evidence.get("extension") or evidence.get("extensao"),
        "tamanho_bytes": evidence.get("size_bytes") or evidence.get("tamanho_bytes"),
    }


def _difference_payload(difference: dict[str, Any]) -> dict[str, Any]:
    summary = difference.get("summary_json") or difference.get("resumo_diferenca") or {}
    if not isinstance(summary, dict):
        summary = {"resumo": str(summary)}
    return {
        "id": difference.get("id") or difference.get("id_diferenca"),
        "arquivo_base": difference.get("base_file_name") or difference.get("nome_arquivo_base"),
        "arquivo_atual": difference.get("current_file_name") or difference.get("nome_arquivo_atual"),
        "linhas_base": difference.get("base_lines") or summary.get("linhas_base"),
        "linhas_atual": difference.get("current_lines") or summary.get("linhas_atual"),
        "linhas_alteradas_estimadas": difference.get("changed_lines_estimate") or summary.get("linhas_alteradas_estimadas"),
        "amostra_diff": _short(str(summary.get("amostra_diff") or summary.get("resumo") or "")),
    }


def _extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
            if content.get("type") == "refusal":
                raise AiGroupingError("A OpenAI recusou a analise: " + str(content.get("refusal") or ""))
    raise AiGroupingError("Resposta da OpenAI sem output_text")


def _extract_chat_completion_text(response: dict[str, Any], *, provider: str) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise AiGroupingError(f"Resposta do {provider} sem choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    raise AiGroupingError(f"Resposta do {provider} sem conteudo textual")


def _strip_json_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        return value
    extracted = _extract_json_object(value)
    return extracted or value


def _extract_json_object(value: str) -> str | None:
    start = value.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(value)):
        char = value[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return None


def _system_prompt() -> str:
    return (
        "Voce e o classificador de falhas do Agent TC. Sua unica tarefa e agrupar as falhas "
        "recebidas por causa semelhante. Nao possui ferramentas, banco de dados ou acesso a arquivos. "
        "Nao invente informacoes. Toda falha deve aparecer exatamente uma vez. Escreva textos em "
        "portugues do Brasil e assinaturas tecnicas curtas em snake_case."
    )


def _json_only_prompt() -> str:
    return (
        "Responda somente com JSON valido, sem markdown. O JSON deve ter exatamente o formato: "
        "{\"clusters\":[{\"titulo_causa\":\"...\",\"assinatura_tecnica\":\"snake_case\","
        "\"classificacao\":\"...\",\"confianca\":0,\"falhas\":[\"id\"],"
        "\"justificativa\":\"...\",\"proximos_passos\":[\"...\"]}]}"
    )


def _read_env(path: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path and Path(path).exists():
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "AI_PROVIDER",
        "AI_TIMEOUT_SECONDS",
        "AI_MAX_OUTPUT_TOKENS",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_TIMEOUT_SECONDS",
        "OPENAI_MAX_OUTPUT_TOKENS",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_TIMEOUT_SECONDS",
        "GEMINI_MAX_OUTPUT_TOKENS",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _required_text(value: Any, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AiGroupingValidationError("Texto obrigatorio invalido: " + field)
    value = value.strip()
    if len(value) > max_length:
        raise AiGroupingValidationError("Texto excede o limite: " + field)
    return value


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _short(value: str) -> str:
    value = value.strip()
    if len(value) <= MAX_TEXT_LENGTH:
        return value
    return value[:MAX_TEXT_LENGTH].rstrip() + "..."

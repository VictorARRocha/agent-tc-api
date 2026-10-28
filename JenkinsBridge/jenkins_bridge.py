import json
import logging
import re
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import dotenv_values


POLL_INTERVAL_SECONDS = 10
BASE_DIR = Path(__file__).resolve().parent
ENV_VALUES = dotenv_values(BASE_DIR / ".env")
LOG_FILE = BASE_DIR / "logs" / "jenkins_bridge.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

LOGGER = logging.getLogger("jenkins_bridge")
# Ultimo estado registrado por chave; evita repetir a mesma linha a cada ciclo.
LAST_LOGGED_STATE = {}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def log(message):
    LOGGER.info("[" + now_iso() + "] " + message)


def log_change(key, state, message):
    """Registra a mensagem so quando o estado associado a chave muda."""
    if LAST_LOGGED_STATE.get(key) == state:
        return
    LAST_LOGGED_STATE[key] = state
    log(message)


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def required_env(name):
    value = ENV_VALUES.get(name)
    if not value:
        raise RuntimeError("Variavel obrigatoria ausente no .env: " + name)
    return value


def optional_env(name, default=None):
    value = ENV_VALUES.get(name)
    return value if value not in (None, "") else default


def load_config():
    bridge_backend = optional_env("JENKINS_BRIDGE_BACKEND", optional_env("AGENT_TC_BRIDGE_BACKEND", "api")).strip().lower()
    if bridge_backend != "api":
        raise RuntimeError("JENKINS_BRIDGE_BACKEND invalido. Use 'api'.")
    config = {
        "bridge_backend": bridge_backend,
        "agent_tc_api_url": optional_env("AGENT_TC_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        "agent_tc_bridge_token": optional_env("AGENT_TC_BRIDGE_TOKEN", ""),
        "jenkins_url": required_env("JENKINS_URL").rstrip("/"),
        "jenkins_job_path": required_env("JENKINS_JOB_PATH"),
        "jenkins_user": required_env("JENKINS_USER"),
        "jenkins_api_token": required_env("JENKINS_API_TOKEN"),
    }
    return config


def api_headers(config):
    headers = {"Content-Type": "application/json"}
    token = config.get("agent_tc_bridge_token")
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def api_url(config, path):
    if not path.startswith("/"):
        path = "/" + path
    return config["agent_tc_api_url"] + path


def jenkins_auth(config):
    return (config["jenkins_user"], config["jenkins_api_token"])


def jenkins_build_url(config):
    path = config["jenkins_job_path"]
    if not path.startswith("/"):
        path = "/" + path
    return config["jenkins_url"] + path


def safe_text(value, max_len=1000):
    if value is None:
        return None
    return str(value)[:max_len]


def extract_queue_id(queue_url):
    if not queue_url:
        return None

    match = re.search(r"/queue/item/(\d+)/?", queue_url)
    if not match:
        return None

    return match.group(1)


def update_request(config, request_id, fields):
    response = requests.post(
        api_url(config, "/bridge/rerun-requests/" + str(request_id) + "/update"),
        headers=api_headers(config),
        data=json.dumps(fields, separators=(",", ":"), ensure_ascii=False),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    row = payload.get("rerun_request")
    return [row] if row else []


def fetch_requested(config):
    response = requests.get(
        api_url(config, "/bridge/rerun-requests/requested"),
        headers=api_headers(config),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_active_requests(config):
    response = requests.get(
        api_url(config, "/bridge/rerun-requests/active"),
        headers=api_headers(config),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_cancel_requested(config):
    response = requests.get(
        api_url(config, "/bridge/rerun-requests/cancel-requested"),
        headers=api_headers(config),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def claim_request(config, record):
    request_id = record["id"]

    response = requests.post(
        api_url(config, "/bridge/rerun-requests/" + str(request_id) + "/claim"),
        headers=api_headers(config),
        data=b"{}",
        timeout=30,
    )
    response.raise_for_status()
    return bool(response.json().get("claimed"))


def compact_config_json(record):
    config_json = record.get("config_json")

    if config_json is None:
        raise RuntimeError("Campo config_json vazio no registro.")

    # ensure_ascii: acento vai escapado (\u00e7), senao o Jenkins le o form no charset errado (branch com acento quebrava).
    if isinstance(config_json, str):
        parsed = json.loads(config_json)
        return json.dumps(parsed, separators=(",", ":"), ensure_ascii=True)

    return json.dumps(config_json, separators=(",", ":"), ensure_ascii=True)


def trigger_jenkins(config, compact_json):
    url = jenkins_build_url(config)

    return requests.post(
        url,
        auth=jenkins_auth(config),
        data={"CONFIG_JSON": compact_json},
        timeout=60,
    )


def save_jenkins_success(config, request_id, response):
    queue_url = response.headers.get("Location")
    queue_id = extract_queue_id(queue_url)

    retorno = {
        "http_status": response.status_code,
        "body": response.text,
        "headers": {"Location": queue_url},
    }

    update_request(
        config,
        request_id,
        {
            "status": "enviado_jenkins",
            "execution_status": "na_fila" if queue_id else "enviado_jenkins",
            "jenkins_queue_url": queue_url,
            "error_message": None if queue_id else "Jenkins nao retornou queue_id no header Location.",
        },
    )


def save_jenkins_error(config, request_id, message, response=None):
    retorno = {"http_status": None, "body": None}

    if response is not None:
        retorno = {"http_status": response.status_code, "body": response.text}

    update_request(
        config,
        request_id,
        {
            "status": "erro",
            "execution_status": "erro_envio",
            "error_message": safe_text(message),
        },
    )


def get_queue_item(config, queue_id):
    url = config["jenkins_url"] + "/queue/item/" + str(queue_id) + "/api/json"

    response = requests.get(url, auth=jenkins_auth(config), timeout=30)
    response.raise_for_status()
    return response.json()


def queue_item_exists(config, queue_id):
    url = config["jenkins_url"] + "/queue/item/" + str(queue_id) + "/api/json"
    response = requests.get(url, auth=jenkins_auth(config), timeout=30)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def get_build_json(config, build_url):
    api_url = build_url.rstrip("/") + "/api/json"

    response = requests.get(api_url, auth=jenkins_auth(config), timeout=30)
    response.raise_for_status()
    return response.json()


def cancel_queue_item(config, queue_id):
    url = config["jenkins_url"] + "/queue/cancelItem"
    response = requests.post(
        url,
        auth=jenkins_auth(config),
        params={"id": str(queue_id)},
        timeout=30,
    )
    response.raise_for_status()
    return response


def stop_build(config, build_url):
    url = build_url.rstrip("/") + "/stop"
    response = requests.post(url, auth=jenkins_auth(config), timeout=30)
    if response.status_code not in (200, 201, 302):
        response.raise_for_status()
    return response


def cancel_requested_record(config, record):
    request_id = record["id"]
    build_url = record.get("jenkins_build_url") or record.get("build_url") or record.get("jenkins_url")
    queue_id = record.get("queue_id") or extract_queue_id(record.get("jenkins_queue_url"))

    try:
        if build_url:
            stop_build(config, build_url)
            message = "Cancelamento enviado ao build do Jenkins."
            update_request(
                config,
                request_id,
                {
                    "status": "cancelando",
                    "execution_status": "cancelando",
                    "error_message": message,
                },
            )
        elif queue_id:
            try:
                cancel_queue_item(config, queue_id)
            except requests.HTTPError:
                # O Jenkins devolve 500 no cancelItem quando o item ja saiu da fila (virou build ou ja foi cancelado).
                try:
                    item = get_queue_item(config, queue_id)
                except requests.HTTPError:
                    item = None
                build_url = ((item or {}).get("executable") or {}).get("url")
                if build_url:
                    stop_build(config, build_url)
                    update_request(
                        config,
                        request_id,
                        {
                            "status": "cancelando",
                            "execution_status": "cancelando",
                            "jenkins_build_url": build_url,
                            "error_message": "Cancelamento enviado ao build do Jenkins.",
                        },
                    )
                    log("Cancelamento processado para registro " + str(request_id) + ". Item ja era build; build parado.")
                    return
                if item is not None and not item.get("cancelled"):
                    raise
            if queue_item_exists(config, queue_id):
                message = "Cancelamento enviado para item da fila do Jenkins."
                update_request(
                    config,
                    request_id,
                    {
                        "status": "cancelando",
                        "execution_status": "cancelando",
                        "error_message": message,
                    },
                )
            else:
                message = "Item removido da fila do Jenkins."
                update_request(
                    config,
                    request_id,
                    {
                        "status": "cancelado",
                        "execution_status": "cancelado",
                        "execution_result": "ABORTED",
                        "error_message": message,
                    },
                )
        else:
            message = "Cancelado antes de ser enviado ao Jenkins."
            update_request(
                config,
                request_id,
                {
                    "status": "cancelado",
                    "execution_status": "cancelado",
                    "execution_result": "ABORTED",
                    "error_message": message,
                },
            )

        log("Cancelamento processado para registro " + str(request_id) + ". " + message)
    except Exception as exc:
        message = "Falha ao cancelar no Jenkins: " + safe_text(exc)
        update_request(
            config,
            request_id,
            {
                "status": "cancel_requested",
                "execution_status": "erro_cancelamento",
                "error_message": message,
            },
        )
        log("Erro ao cancelar registro " + str(request_id) + ": " + message)


def monitor_queue_item(config, record):
    request_id = record["id"]
    queue_id = record.get("queue_id") or extract_queue_id(record.get("jenkins_queue_url"))

    if not queue_id:
        return

    try:
        item = get_queue_item(config, queue_id)
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        is_canceling = str(record.get("execution_status") or record.get("status") or "").lower() == "cancelando"
        if is_canceling and response is not None and response.status_code == 404:
            update_request(
                config,
                request_id,
                {
                    "status": "cancelado",
                    "execution_status": "cancelado",
                    "execution_result": "ABORTED",
                    "error_message": "Item da fila nao existe mais no Jenkins apos solicitacao de cancelamento.",
                },
            )
            log("Registro " + str(request_id) + " confirmado como cancelado na fila.")
            return
        raise
    executable = item.get("executable")

    if not executable:
        status = "cancelando" if str(record.get("execution_status") or record.get("status") or "").lower() == "cancelando" else "na_fila"
        update_request(
            config,
            request_id,
            {
                "execution_status": status,
                "error_message": None,
            },
        )
        log_change(request_id, status, "Registro " + str(request_id) + " ainda na fila.")
        return

    build_number = str(executable.get("number"))
    build_url = executable.get("url")

    update_request(
        config,
        request_id,
        {
            "jenkins_build_number": build_number,
            "jenkins_build_url": build_url,
            "execution_status": "rodando",
            "error_message": None,
        },
    )

    log("Registro " + str(request_id) + " virou build #" + build_number + ".")


def calculate_progress(record, timestamp, estimated_duration):
    if not timestamp or not estimated_duration or int(estimated_duration) <= 0:
        return 40, 0

    elapsed = max(0, now_ms() - int(timestamp))
    progress = round((elapsed / int(estimated_duration)) * 100)
    progress = min(95, max(10, progress))

    return progress, elapsed


def monitor_build(config, record):
    request_id = record["id"]
    build_url = record.get("jenkins_build_url") or record.get("build_url") or record.get("jenkins_url")

    if not build_url:
        return

    build = get_build_json(config, build_url)

    building = build.get("building")
    result = build.get("result")
    timestamp = build.get("timestamp") or 0
    duration = build.get("duration") or 0
    estimated_duration = build.get("estimatedDuration") or 0

    if building:
        progress, elapsed = calculate_progress(record, timestamp, estimated_duration)

        is_canceling = str(record.get("execution_status") or record.get("status") or "").lower() == "cancelando"
        update_request(
            config,
            request_id,
        {
            "execution_status": "cancelando" if is_canceling else "rodando",
            "execution_result": None,
            "error_message": None,
        },
    )

        # Registra a cada faixa de 10% (o progresso estimado para em 95%).
        log_change(
            request_id,
            ("cancelando" if is_canceling else "rodando", progress // 10),
            "Registro " + str(request_id) + " rodando: " + str(progress) + "%.",
        )
        return

    if result == "SUCCESS":
        execution_status = "finalizado_sucesso"
    elif result == "UNSTABLE":
        # O pipeline marca erro/aviso do TC como UNSTABLE: a rodagem foi ate o fim com quebras ou diferencas.
        execution_status = "finalizado_ocorrencias"
    elif result == "FAILURE":
        execution_status = "finalizado_falha"
    elif result == "ABORTED":
        execution_status = "cancelado"
    else:
        execution_status = "erro_monitoramento"

    update_request(
        config,
        request_id,
        {
            "execution_status": execution_status,
            "execution_result": result,
            "status": "finalizado" if execution_status.startswith("finalizado") else execution_status,
            "error_message": None if execution_status != "erro_monitoramento" else "Build finalizado sem result reconhecido.",
        },
    )

    log("Registro " + str(request_id) + " finalizado: " + str(result) + ".")
    LAST_LOGGED_STATE.pop(request_id, None)


def monitor_active_record(config, record):
    request_id = record["id"]

    try:
        if record.get("jenkins_build_url") or record.get("build_url") or record.get("jenkins_build_number") or record.get("build_number"):
            monitor_build(config, record)
        elif record.get("jenkins_queue_url") or record.get("queue_id"):
            monitor_queue_item(config, record)
    except Exception as exc:
        message = safe_text(exc)
        update_request(
            config,
            request_id,
            {
                "execution_status": "erro_monitoramento",
                "error_message": message,
            },
        )
        log_change(request_id, ("erro_monitoramento", message), "Erro ao monitorar registro " + str(request_id) + ": " + str(message))


def process_record(config, record):
    request_id = record["id"]

    if not claim_request(config, record):
        log("Registro " + str(request_id) + " ja foi capturado por outro processo.")
        return

    try:
        compact_json = compact_config_json(record)
        response = trigger_jenkins(config, compact_json)

        if response.status_code in (200, 201, 202):
            save_jenkins_success(config, request_id, response)
            log("Registro " + str(request_id) + " enviado ao Jenkins.")
        else:
            message = "Jenkins retornou HTTP " + str(response.status_code)
            save_jenkins_error(config, request_id, message, response)
            log("Registro " + str(request_id) + " marcado como erro: " + message + ".")
    except Exception as exc:
        message = "Falha ao processar registro: " + str(exc)
        save_jenkins_error(config, request_id, message)
        log("Registro " + str(request_id) + " marcado como erro: " + message + ".")


def run_loop():
    config = load_config()
    log("Jenkins Bridge iniciado.")

    while True:
        try:
            records = fetch_requested(config)

            if records:
                log("Registros solicitados encontrados: " + str(len(records)) + ".")

            for record in records:
                process_record(config, record)

            cancel_records = fetch_cancel_requested(config)

            if cancel_records:
                log("Registros com cancelamento solicitado: " + str(len(cancel_records)) + ".")

            for record in cancel_records:
                cancel_requested_record(config, record)

            active_records = fetch_active_requests(config)

            log_change(
                "active_records",
                len(active_records),
                "Registros ativos para monitorar: " + str(len(active_records)) + ".",
            )

            for record in active_records:
                monitor_active_record(config, record)

            if LAST_LOGGED_STATE.pop("loop_error", None) is not None:
                log("Loop principal normalizado.")
        except Exception as exc:
            log_change("loop_error", str(exc), "Erro no loop principal: " + str(exc))

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    setup_logging()
    try:
        run_loop()
    except Exception:
        # Sem console (pythonw/tarefa agendada), o erro fatal so fica registrado aqui.
        LOGGER.exception("[" + now_iso() + "] Jenkins Bridge encerrado por erro fatal.")
        raise

"""RegravacaoBridge: executa na D01 os pedidos de regravacao de bases feitos no dashboard.

Para cada pedido (API Agent TC):
  1. pega o pedido de forma atomica (claim);
  2. para cada arquivo: baixa o _Atual das evidencias e confere o hash; le a base no SVN
     na revisao HEAD e confere se ainda e a da rodagem (hash do _Antigo);
  3. se tudo conferir, grava todos os arquivos num unico commit (svnmucc), com a revisao
     conferida como base: se alguem alterou um arquivo no meio, o commit falha;
  4. devolve o resultado por arquivo e a revisao do commit.
Qualquer divergencia cancela o pedido inteiro (nada e gravado pela metade).

Com SVN_COMMIT diferente de 1 roda em modo simulacao: faz todas as conferencias e nao grava.

Tambem importa rodagens pedidas no dashboard: roda o run_agent_tc_python.bat com a VM e a
pasta do log compactado (ArquivosCompactados/<VM>/<rodagem>), um pedido por vez.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import dotenv_values

POLL_INTERVAL_SECONDS = 15
SVN_TIMEOUT_SECONDS = 120
HTTP_TIMEOUT_SECONDS = 60
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "logs" / "regravacao_bridge.log"
TORTOISE_BIN = Path(r"C:\Program Files\TortoiseSVN\bin")
IMPORT_TIMEOUT_SECONDS = 60 * 60
DEFAULT_LOGS_BASE = r"S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados"
# A pasta vai como argumento de um .bat: caracteres que o cmd interpreta ficam de fora.
UNSAFE_CHARS = re.compile(r'["%!^&|<>\x00-\x1f]')
DEFAULT_ALLOWED_ROOTS = "https://192.168.10.199:8443/svn/testcomplete"

LOGGER = logging.getLogger("regravacao_bridge")
LAST_LOGGED_STATE: dict[str, object] = {}


class SvnError(RuntimeError):
    pass


# Commit recusado porque alguem gravou o mesmo arquivo depois da conferencia
# (file:// devolve E160028 "out of date"; http(s) pode devolver E160024/E170004).
OUT_OF_DATE_MARKERS = ("e160028", "e160024", "e170004", "out of date", "out-of-date")

# Status de arquivo que sao conflito: a base no SVN nao e mais a da rodagem.
CONFLICT_STATUSES = ("base_mudou", "base_inexistente")

CONFLICT_MESSAGE = (
    "Conflito no SVN: a base mudou depois da rodagem e nada foi gravado. "
    "Faca a regravacao manualmente (SVN Update na branch, confira quem alterou, "
    "substitua o arquivo e faca o commit)."
)


def is_out_of_date(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in OUT_OF_DATE_MARKERS)


class ItemError(RuntimeError):
    """Problema num arquivo do pedido (base mudou, download corrompido...)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def log(message: str) -> None:
    LOGGER.info("[" + now_iso() + "] " + message)


def log_change(key: str, state: object, message: str) -> None:
    if LAST_LOGGED_STATE.get(key) == state:
        return
    LAST_LOGGED_STATE[key] = state
    log(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- configuracao

def load_config(env: dict[str, str | None] | None = None) -> dict[str, object]:
    values = env if env is not None else dotenv_values(BASE_DIR / ".env")

    def get(name: str, default: str = "") -> str:
        value = values.get(name)
        return str(value).strip() if value not in (None, "") else default

    token = get("AGENT_TC_BRIDGE_TOKEN")
    if not token:
        raise RuntimeError("AGENT_TC_BRIDGE_TOKEN ausente no .env (o mesmo do JenkinsBridge).")
    return {
        "api_url": get("AGENT_TC_API_URL", "http://127.0.0.1:8001").rstrip("/"),
        "token": token,
        "svn_bin": get("SVN_BIN_DIR", str(TORTOISE_BIN)),
        "svn_username": get("SVN_USERNAME"),
        "svn_password": get("SVN_PASSWORD"),
        "trust_failures": get("SVN_TRUST_SERVER_CERT_FAILURES"),
        "allowed_roots": [r.strip().rstrip("/") for r in get("SVN_ALLOWED_ROOTS", DEFAULT_ALLOWED_ROOTS).split(",") if r.strip()],
        "commit": get("SVN_COMMIT", "0") == "1",
        "logs_base": get("AGENT_TC_LOGS_BASE", DEFAULT_LOGS_BASE).rstrip("\\/"),
        "import_bat": get("AGENT_TC_IMPORT_BAT", str(BASE_DIR.parent / "run_agent_tc_python.bat")),
    }


# ---------------------------------------------------------------- API

class Api:
    def __init__(self, config: dict[str, object], session: requests.Session | None = None):
        self.base = str(config["api_url"])
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + str(config["token"])})

    def _json(self, method: str, path: str, body: dict | None = None):
        response = self.session.request(method, self.base + path, json=body, timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            raise RuntimeError(f"API {method} {path} retornou HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def requested(self) -> list[dict]:
        return self._json("GET", "/bridge/regravacoes/requested")

    def processing(self) -> list[dict]:
        return self._json("GET", "/bridge/regravacoes/processing")

    def claim(self, request_id: str) -> dict | None:
        data = self._json("POST", f"/bridge/regravacoes/{quote(request_id, safe='')}/claim", {})
        return data.get("regravacao") if data.get("claimed") else None

    def update(self, request_id: str, fields: dict) -> None:
        self._json("POST", f"/bridge/regravacoes/{quote(request_id, safe='')}/update", fields)

    def imports(self, status: str) -> list[dict]:
        return self._json("GET", "/bridge/imports/" + status)

    def claim_import(self, request_id: str) -> dict | None:
        data = self._json("POST", f"/bridge/imports/{quote(request_id, safe='')}/claim", {})
        return data.get("import") if data.get("claimed") else None

    def update_import(self, request_id: str, fields: dict) -> None:
        self._json("POST", f"/bridge/imports/{quote(request_id, safe='')}/update", fields)

    def download(self, storage_path: str) -> bytes:
        response = self.session.get(self.base + "/bridge/files/" + quote(storage_path, safe="/"), timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise ItemError("erro", f"Nao foi possivel baixar o arquivo atual (HTTP {response.status_code}).")
        return response.content


# ---------------------------------------------------------------- SVN

class Svn:
    def __init__(self, config: dict[str, object]):
        self.bin = Path(str(config["svn_bin"]))
        self.username = str(config.get("svn_username") or "")
        self.password = str(config.get("svn_password") or "")
        self.trust = str(config.get("trust_failures") or "")

    def _exe(self, name: str) -> str:
        candidate = self.bin / (name + ".exe")
        if candidate.exists():
            return str(candidate)
        found = shutil.which(name)
        if not found:
            raise SvnError(f"{name} nao encontrado (SVN_BIN_DIR={self.bin}).")
        return found

    def _run(self, tool: str, args: list[str], timeout: int = SVN_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
        command = [self._exe(tool), "--non-interactive"]
        if self.trust:
            command += ["--trust-server-cert-failures", self.trust]
        stdin = None
        if self.username:
            command += ["--username", self.username]
            if self.password:
                command.append("--password-from-stdin")
                stdin = (self.password + "\n").encode("utf-8")
        try:
            return subprocess.run(command + args, input=stdin, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SvnError(f"{tool} demorou mais de {timeout}s.") from exc

    @staticmethod
    def _err(result: subprocess.CompletedProcess) -> str:
        return (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()[-500:]

    def head_revision(self, url: str) -> str:
        result = self._run("svn", ["info", "--show-item", "revision", url])
        if result.returncode != 0:
            raise SvnError("svn info falhou: " + self._err(result))
        return result.stdout.decode("utf-8", errors="replace").strip()

    def cat(self, url: str, revision: str) -> bytes | None:
        """Conteudo do arquivo na revisao; None se o arquivo nao existe."""
        result = self._run("svn", ["cat", f"{url}@{revision}"])
        if result.returncode == 0:
            return result.stdout
        message = self._err(result)
        if "E160013" in message or "E200009" in message or "path not found" in message.lower():
            return None
        raise SvnError("svn cat falhou: " + message)

    def put_all(self, files: list[tuple[bytes, str]], message: str, base_revision: str) -> str:
        """Um commit com todos os arquivos; falha se algum mudou depois de base_revision."""
        with tempfile.TemporaryDirectory(prefix="regravacao_") as temp_dir:
            folder = Path(temp_dir)
            message_file = folder / "mensagem.txt"
            # O svnmucc le o -F na pagina de codigo do Windows (ANSI) e converte para UTF-8;
            # gravar em UTF-8 duplicaria a codificacao dos acentos (conferido com svn log).
            message_file.write_bytes(message.encode("mbcs" if os.name == "nt" else "utf-8", errors="replace"))
            # Os "put" vao num arquivo (-X, um argumento por linha): na linha de comando, o limite
            # do Windows (~32 mil caracteres) estourava por volta de 150 arquivos.
            puts: list[str] = []
            for index, (data, url) in enumerate(files):
                local = folder / f"arquivo_{index:04d}"
                local.write_bytes(data)
                puts += ["put", str(local), url]
            args_file = folder / "argumentos.txt"
            args_file.write_text("\n".join(puts) + "\n", encoding="utf-8")
            args = ["-r", base_revision, "-F", str(message_file), "-X", str(args_file)]
            # Commit grande demora mais: 120 s + 1 s por arquivo.
            result = self._run("svnmucc", args, timeout=SVN_TIMEOUT_SECONDS + len(files))
        if result.returncode != 0:
            raise SvnError("Commit recusado pelo SVN: " + self._err(result))
        match = re.search(r"r(\d+) committed", result.stdout.decode("utf-8", errors="replace"))
        if not match:
            raise SvnError("Commit feito, mas a revisao nao foi reconhecida: " + self._err(result))
        return match.group(1)


# ---------------------------------------------------------------- regras

def target_url(repository_url: str, caminho_base: str, allowed_roots: list[str]) -> str:
    """URL do arquivo base no SVN: <URL do projeto>/Stores/<caminho>. So dentro de Stores/Files."""
    repo = str(repository_url or "").rstrip("/")
    if not repo or not any(repo == root or repo.startswith(root + "/") for root in allowed_roots):
        raise ItemError("erro", f"Repositorio fora dos permitidos (SVN_ALLOWED_ROOTS): {repo or '(vazio)'}")
    parts = str(caminho_base or "").replace("\\", "/").split("/")
    if len(parts) < 2 or parts[0].lower() != "files" or any(p in ("", ".", "..") for p in parts):
        raise ItemError("erro", f"Caminho de base invalido: {caminho_base}")
    return repo + "/Stores/" + "/".join(quote(part) for part in parts)


def default_commit_message(request: dict, items: list[dict]) -> str:
    """Mensagem padrao (o dashboard mostra a mesma, com {pedido} no lugar do numero)."""
    lines = [
        "Regravação de bases pelo dashboard (AgenteTC)",
        "",
        "Pedido: {pedido}",
        "Rodagem: " + str(request.get("run_id")),
        "Solicitado por: " + str(request.get("requested_by") or "(desconhecido)"),
        "",
    ]
    lines += [f"- CT {item.get('id_caso_teste')}: {item.get('caminho_base')}" for item in items]
    return "\n".join(lines)


def commit_message(request: dict, items: list[dict]) -> str:
    """A mensagem editada no dashboard (ou a padrao), com {pedido} trocado pelo numero."""
    text = str(request.get("commit_message") or "").strip() or default_commit_message(request, items)
    text = text.replace("\r\n", "\n").replace("{pedido}", str(request.get("id")))
    return text.rstrip() + "\n"


def process_request(request: dict, api: Api, svn: Svn, config: dict[str, object]) -> dict:
    """Confere e grava um pedido ja pego. Devolve os campos para /update."""
    items = request.get("items_json") or []
    if not items:
        return {"status": "erro", "error_message": "Pedido sem arquivos.", "result_json": {"itens": []}}

    results, puts = [], []
    try:
        repository_url = str(request.get("repository_url") or "")
        allowed = list(config["allowed_roots"])  # type: ignore[arg-type]
        urls = [target_url(repository_url, item.get("caminho_base"), allowed) for item in items]
        revision = svn.head_revision(repository_url)
    except (ItemError, SvnError) as exc:
        return {"status": "erro", "error_message": str(exc), "result_json": {"itens": []}}

    for item, url in zip(items, urls):
        result = {"difference_id": item.get("difference_id"), "arquivo_atual": item.get("arquivo_atual"),
                  "caminho_base": item.get("caminho_base"), "destino": url}
        try:
            atual = api.download(str(item.get("storage_path_atual") or ""))
            if item.get("hash_atual") and sha256(atual) != item["hash_atual"]:
                raise ItemError("erro", "O arquivo atual baixado nao confere com o da rodagem (hash).")
            base = svn.cat(url, revision)
            if base is None:
                raise ItemError("base_inexistente", "A base nao existe no SVN nesse caminho.")
            hash_svn = sha256(base)
            if hash_svn == sha256(atual):
                result.update(status="ja_gravado", mensagem="A base no SVN ja e igual ao arquivo atual.")
            elif hash_svn != item.get("hash_base"):
                raise ItemError("base_mudou", f"A base mudou no SVN desde a rodagem (revisao {revision}).")
            else:
                result.update(status="pronto")
                puts.append((atual, url))
        except ItemError as exc:
            result.update(status=exc.status, mensagem=str(exc))
        except (SvnError, requests.RequestException) as exc:
            result.update(status="erro", mensagem=str(exc)[:500])
        results.append(result)

    problems = [r for r in results if r["status"] not in ("pronto", "ja_gravado")]
    if problems:
        for r in results:
            if r["status"] == "pronto":
                r.update(status="nao_gravado", mensagem="Nao gravado: outro arquivo do pedido falhou.")
        conflito = any(r["status"] in CONFLICT_STATUSES for r in results)
        message = f"{len(problems)} arquivo(s) com problema; nada foi gravado."
        return {"status": "erro", "error_message": (CONFLICT_MESSAGE + " " + message) if conflito else message,
                "result_json": {"revisao_conferida": revision, "itens": results, "conflito": conflito}}

    if not puts:
        for r in results:
            r.setdefault("mensagem", "")
        return {"status": "concluido", "result_json": {"revisao_conferida": revision, "itens": results}}

    if not config["commit"]:
        for r in results:
            if r["status"] == "pronto":
                r.update(status="simulado", mensagem="Conferido; seria gravado.")
        return {"status": "erro", "error_message": "Modo simulacao (SVN_COMMIT=0): tudo conferido, nada foi gravado.",
                "result_json": {"revisao_conferida": revision, "simulacao": True, "itens": results}}

    try:
        new_revision = svn.put_all(puts, commit_message(request, items), revision)
    except SvnError as exc:
        if is_out_of_date(str(exc)):
            # Alguem gravou o arquivo entre a conferencia e o commit: e conflito, nao falha do Bridge.
            for r in results:
                if r["status"] == "pronto":
                    r.update(status="base_mudou", mensagem="Outro commit alterou este arquivo durante a regravacao.")
            return {"status": "erro", "error_message": CONFLICT_MESSAGE + " " + str(exc)[:300],
                    "result_json": {"revisao_conferida": revision, "itens": results, "conflito": True}}
        for r in results:
            if r["status"] == "pronto":
                r.update(status="nao_gravado", mensagem="O commit foi recusado.")
        return {"status": "erro", "error_message": str(exc), "result_json": {"revisao_conferida": revision, "itens": results}}
    for r in results:
        if r["status"] == "pronto":
            r.update(status="gravado", mensagem=f"Gravado na revisao {new_revision}.")
    return {"status": "concluido", "svn_revision": new_revision,
            "result_json": {"revisao_conferida": revision, "itens": results}}


def handle(request: dict, api: Api, svn: Svn, config: dict[str, object]) -> None:
    request_id = str(request["id"])
    try:
        fields = process_request(request, api, svn, config)
    except Exception as exc:  # nunca deixar o pedido preso em "processando"
        fields = {"status": "erro", "error_message": "Falha inesperada no Bridge: " + str(exc)[:500], "result_json": {}}
    api.update(request_id, fields)
    log(f"Pedido {request_id}: {fields['status']}" + (f" (r{fields['svn_revision']})" if fields.get("svn_revision") else "")
        + (f" - {fields['error_message']}" if fields.get("error_message") else ""))


def run_once(api: Api, svn: Svn, config: dict[str, object]) -> int:
    handled = 0
    for request in api.requested():
        claimed = api.claim(str(request["id"]))
        if not claimed:
            continue
        handle(claimed, api, svn, config)
        handled += 1
    return handled


# ---------------------------------------------------------------- importacao de rodagem

def import_command(request: dict, config: dict[str, object]) -> list[str]:
    """Confere de novo a pasta (vai como argumento do .bat) e monta o comando: .bat VM pasta."""
    pasta = str(request.get("run_folder") or "")
    base = str(config["logs_base"]) + "\\"
    parts = pasta[len(base):].split("\\") if pasta.lower().startswith(base.lower()) else []
    if UNSAFE_CHARS.search(pasta) or len(parts) != 2 or any(part.strip() in ("", ".", "..") for part in parts):
        raise ItemError("erro", "Pasta fora de " + base + "<VM>\\<rodagem>: " + pasta)
    if not Path(pasta).is_dir():
        raise ItemError("erro", "Pasta nao encontrada na D01: " + pasta)
    return [str(config["import_bat"]), parts[0], pasta]


def process_import(request: dict, config: dict[str, object]) -> dict:
    try:
        command = import_command(request, config)
    except ItemError as exc:
        return {"status": "erro", "error_message": str(exc)}
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=IMPORT_TIMEOUT_SECONDS, cwd=str(Path(command[0]).parent),
            # Branch escolhida na importacao: o pipeline usa no lugar do svn info da copia da D01.
            env={**os.environ, "AGENT_TC_BRANCH_URL": str(request.get("branch_url") or "")},
        )
    except subprocess.TimeoutExpired:
        return {"status": "erro", "error_message": f"A importacao passou de {IMPORT_TIMEOUT_SECONDS // 60} minutos e foi interrompida."}
    # O .bat faz chcp 65001: a saida vem em UTF-8 e ja traz o fim do log do Python.
    fields = {"exit_code": result.returncode, "output": result.stdout.decode("utf-8", errors="replace")[-8000:]}
    if result.returncode == 0:
        return {"status": "concluido", **fields}
    return {"status": "erro", "error_message": f"O run_agent_tc_python.bat terminou com codigo {result.returncode}.", **fields}


# Resultado de importacao que a API nao recebeu (ex.: 502 durante o rebuild da API): reenviado
# no ciclo seguinte, senao o pedido fica "processando" ate o Bridge reiniciar.
IMPORT_RESULTS_PENDING: dict[str, dict] = {}


def handle_import(request: dict, api: Api, config: dict[str, object]) -> None:
    request_id = str(request["id"])
    log(f"Importacao {request_id}: {request.get('run_folder')}")
    try:
        fields = process_import(request, config)
    except Exception as exc:  # nunca deixar o pedido preso em "processando"
        fields = {"status": "erro", "error_message": "Falha inesperada no Bridge: " + str(exc)[:500]}
    log(f"Importacao {request_id}: {fields['status']}" + (f" - {fields['error_message']}" if fields.get("error_message") else ""))
    try:
        api.update_import(request_id, fields)
    except Exception as exc:
        IMPORT_RESULTS_PENDING[request_id] = fields
        log(f"Importacao {request_id}: a API nao recebeu o resultado ({exc}); reenvio no proximo ciclo.")


def send_pending_import_results(api: Api) -> None:
    for request_id, fields in list(IMPORT_RESULTS_PENDING.items()):
        api.update_import(request_id, fields)
        del IMPORT_RESULTS_PENDING[request_id]
        log(f"Importacao {request_id}: resultado reenviado.")


def run_imports_once(api: Api, config: dict[str, object]) -> int:
    send_pending_import_results(api)
    handled = 0
    for request in api.imports("requested"):
        claimed = api.claim_import(str(request["id"]))
        if not claimed:
            continue
        handle_import(claimed, api, config)
        handled += 1
    return handled


def run_loop() -> None:
    config = load_config()
    api, svn = Api(config), Svn(config)
    log("Regravacao Bridge iniciado (" + ("COMMIT ativo" if config["commit"] else "modo SIMULACAO") + ").")
    # Retoma o que ficou pela metade (Bridge caiu depois do claim). A conferencia por hash
    # reconhece o que ja tinha sido gravado ("ja_gravado").
    try:
        for request in api.processing():
            log(f"Retomando pedido {request['id']} que estava em processamento.")
            handle(request, api, svn, config)
    except Exception as exc:
        log("Nao foi possivel retomar pedidos em processamento: " + str(exc))
    # Importacao pela metade: reimportar a mesma pasta substitui o que ja tinha entrado.
    try:
        for request in api.imports("processing"):
            log(f"Retomando importacao {request['id']} que estava em processamento.")
            handle_import(request, api, config)
    except Exception as exc:
        log("Nao foi possivel retomar importacoes em processamento: " + str(exc))

    while True:
        try:
            run_once(api, svn, config)
            run_imports_once(api, config)
            if LAST_LOGGED_STATE.pop("loop_error", None) is not None:
                log("Loop principal normalizado.")
        except Exception as exc:
            log_change("loop_error", str(exc), "Erro no loop principal: " + str(exc))
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    setup_logging()
    try:
        run_loop()
    except Exception:
        LOGGER.exception("[" + now_iso() + "] Regravacao Bridge encerrado por erro fatal.")
        raise

"""MergeBridge: executa na D01 os merges entre branches do TC pedidos no dashboard.

Mantem uma copia de trabalho propria (MERGE_WC_DIR) e, para cada pedido (API Agent TC):
  previa   : troca a copia para o destino (svn switch), lista as revisoes da origem que ainda
             nao entraram (svn mergeinfo), simula o merge (svn merge --dry-run) e devolve
             revisoes, arquivos e conflitos previstos;
  execucao : faz o merge da origem (na revisao da previa, se houve previa; senao no estado
             atual do servidor) e, sem conflito, o commit. Com conflito desfaz tudo (svn revert)
             e nada e gravado. O dashboard pede o merge direto, sem previa.
Tambem publica na API, periodicamente, a lista de branches do projeto (svn ls).

Com MERGE_COMMIT diferente de 1 roda em modo simulacao: faz o merge e desfaz, sem commit.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import dotenv_values

POLL_INTERVAL_SECONDS = 15
HTTP_TIMEOUT_SECONDS = 60
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "logs" / "merge_bridge.log"
TORTOISE_BIN = Path(r"C:\Program Files\TortoiseSVN\bin")
DEFAULT_PROJECT_URL = "https://192.168.10.199:8443/svn/testcomplete/unico/ProjetoUnico"
DEFAULT_WC_DIR = r"C:\AgenteTC\merge_wc"
MAX_LISTED_REVISIONS = 300
MAX_LISTED_FILES = 3000

# Tempo maximo por comando (a copia do projeto tem ~2,5 GB).
TIMEOUTS = {"checkout": 4 * 3600, "switch": 3600, "merge": 3600, "commit": 3600, "default": 600}

# Commit recusado porque o destino mudou durante o merge.
OUT_OF_DATE_MARKERS = ("e160028", "e160024", "e170004", "e155011", "out of date", "out-of-date")

CONFLICT_MESSAGE = (
    "Conflito no merge: nada foi gravado. Faca o merge manualmente pelo TortoiseSVN "
    "(Merge > Merge a range of revisions), resolva os conflitos e faca o commit."
)

# Rodando por pythonw (tarefa agendada), cada svn.exe abriria uma janela de console.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

LOGGER = logging.getLogger("merge_bridge")
LAST_LOGGED_STATE: dict[str, object] = {}

# Linha de notificacao do svn merge: texto | propriedade | lock | conflito de arvore | caminho.
MERGE_LINE = re.compile(r"^([ ADUCGER])([ UCG])([ B])([ C]) (.+)$")
ACTIONS = {"A": "adicionado", "D": "excluido", "U": "alterado", "G": "alterado", "R": "substituido", "E": "existente", "C": "conflito"}


class SvnError(RuntimeError):
    pass


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


def is_out_of_date(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in OUT_OF_DATE_MARKERS)


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
        "project_url": get("MERGE_PROJECT_URL", DEFAULT_PROJECT_URL).rstrip("/"),
        "trunk_name": get("MERGE_TRUNK_NAME", "Unico"),
        "wc_dir": get("MERGE_WC_DIR", DEFAULT_WC_DIR),
        "commit": get("MERGE_COMMIT", "0") == "1",
        "branches_refresh_seconds": int(get("BRANCHES_REFRESH_SECONDS", "120")),
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

    def pending(self) -> list[dict]:
        return self._json("GET", "/bridge/merges/pending")

    def processing(self) -> list[dict]:
        return self._json("GET", "/bridge/merges/processing")

    def claim(self, request_id: str) -> dict | None:
        data = self._json("POST", f"/bridge/merges/{quote(request_id, safe='')}/claim", {})
        return data.get("merge") if data.get("claimed") else None

    def update(self, request_id: str, fields: dict) -> None:
        self._json("POST", f"/bridge/merges/{quote(request_id, safe='')}/update", fields)

    def publish_branches(self, branches: list[dict]) -> None:
        self._json("POST", "/bridge/merge/branches", {"branches": branches})


# ---------------------------------------------------------------- SVN

class Svn:
    def __init__(self, config: dict[str, object]):
        self.bin = Path(str(config["svn_bin"]))
        self.username = str(config.get("svn_username") or "")
        self.password = str(config.get("svn_password") or "")
        self.trust = str(config.get("trust_failures") or "")

    def _exe(self) -> str:
        candidate = self.bin / "svn.exe"
        if candidate.exists():
            return str(candidate)
        found = shutil.which("svn")
        if not found:
            raise SvnError(f"svn nao encontrado (SVN_BIN_DIR={self.bin}).")
        return found

    def run(self, args: list[str], *, kind: str = "default", check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
        command = [self._exe(), "--non-interactive"]
        if self.trust:
            command += ["--trust-server-cert-failures", self.trust]
        stdin = None
        if self.username:
            command += ["--username", self.username]
            if self.password:
                command.append("--password-from-stdin")
                stdin = (self.password + "\n").encode("utf-8")
        timeout = TIMEOUTS.get(kind, TIMEOUTS["default"])
        try:
            result = subprocess.run(command + args, input=stdin, capture_output=True, timeout=timeout, check=False, cwd=cwd,
                                    creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise SvnError(f"svn {args[0]} demorou mais de {timeout // 60} min.") from exc
        if check and result.returncode != 0:
            raise SvnError(f"svn {args[0]} falhou: " + self.err(result))
        return result

    @staticmethod
    def err(result: subprocess.CompletedProcess) -> str:
        return decode(result.stderr or result.stdout or b"").strip()[-800:]

    def xml(self, args: list[str], *, kind: str = "default") -> ET.Element:
        return ET.fromstring(self.run(args + ["--xml"], kind=kind).stdout)


def decode(data: bytes) -> str:
    """Saida de texto do svn (fora do --xml) vem na pagina de codigo do Windows."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("mbcs" if os.name == "nt" else "latin-1", errors="replace")


# ---------------------------------------------------------------- branches

def list_branches(svn: Svn, config: dict[str, object]) -> list[dict]:
    """Principal (trunk) + pastas de branches/, como o dashboard mostra."""
    project = str(config["project_url"])
    trunk_url = project + "/" + quote(str(config["trunk_name"]))
    info = svn.xml(["info", trunk_url]).find("entry/commit")
    branches = [{
        "name": str(config["trunk_name"]), "url": trunk_url, "kind": "trunk",
        "last_revision": info.get("revision") if info is not None else None,
        "last_author": info.findtext("author") if info is not None else None,
        "last_date": info.findtext("date") if info is not None else None,
    }]
    root = svn.xml(["list", project + "/branches"])
    entries = sorted(root.iter("entry"), key=lambda e: (e.findtext("name") or "").lower())
    for entry in entries:
        if entry.get("kind") != "dir":
            continue
        name = entry.findtext("name") or ""
        commit = entry.find("commit")
        branches.append({
            "name": name, "url": project + "/branches/" + quote(name), "kind": "branch",
            "last_revision": commit.get("revision") if commit is not None else None,
            "last_author": commit.findtext("author") if commit is not None else None,
            "last_date": commit.findtext("date") if commit is not None else None,
        })
    return branches


def allowed_url(url: str, config: dict[str, object]) -> bool:
    project = str(config["project_url"])
    trunk = project + "/" + quote(str(config["trunk_name"]))
    return url == trunk or (url.startswith(project + "/branches/") and "/" not in url[len(project + "/branches/"):].strip("/"))


# ---------------------------------------------------------------- copia de trabalho

def _force_remove(func, path, _exc):  # arquivos .svn sao somente leitura
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:  # Python da D01 pode ser mais antigo
        shutil.rmtree(path, onerror=_force_remove)


def prepare_wc(svn: Svn, config: dict[str, object], target_url: str) -> Path:
    """Copia limpa (sem alteracoes locais) no destino, atualizada no HEAD."""
    wc = Path(str(config["wc_dir"]))
    if (wc / ".svn").is_dir():
        try:
            svn.run(["cleanup", "--remove-unversioned", "--remove-ignored", str(wc)], kind="switch")
            svn.run(["revert", "-R", str(wc)], kind="switch")
            svn.run(["switch", "--ignore-externals", target_url, str(wc)], kind="switch")
            return wc
        except SvnError as exc:
            log(f"Copia de trabalho com problema ({exc}); fazendo checkout de novo.")
            remove_tree(wc)
    elif wc.exists() and any(wc.iterdir()):
        raise SvnError(f"MERGE_WC_DIR ({wc}) existe e nao e uma copia de trabalho do SVN; esvazie a pasta.")
    wc.parent.mkdir(parents=True, exist_ok=True)
    log(f"Checkout da copia de trabalho em {wc} (a primeira vez demora).")
    svn.run(["checkout", "--ignore-externals", target_url, str(wc)], kind="checkout")
    return wc


def clean_wc(svn: Svn, wc: Path) -> None:
    try:
        svn.run(["revert", "-R", str(wc)], kind="switch")
        svn.run(["cleanup", "--remove-unversioned", "--remove-ignored", str(wc)], kind="switch")
    except SvnError as exc:
        log("Nao foi possivel limpar a copia de trabalho: " + str(exc))


def last_changed_revision(svn: Svn, url: str) -> str:
    commit = svn.xml(["info", url]).find("entry/commit")
    if commit is None or not commit.get("revision"):
        raise SvnError(f"Nao foi possivel ler a revisao de {url}.")
    return commit.get("revision")


def eligible_revisions(svn: Svn, source: str, wc: Path) -> list[int]:
    out = decode(svn.run(["mergeinfo", "--show-revs", "eligible", source, str(wc)]).stdout)
    return sorted({int(m) for m in re.findall(r"r(\d+)", out)})


def revision_log(svn: Svn, source_url: str, revision: str, revisions: list[int]) -> list[dict]:
    """Autor, data e mensagem das revisoes (as mais recentes, ate MAX_LISTED_REVISIONS)."""
    wanted = revisions[-MAX_LISTED_REVISIONS:]
    entries: list[dict] = []
    for start in range(0, len(wanted), 100):
        chunk = ",".join(str(r) for r in wanted[start:start + 100])
        root = svn.xml(["log", "-c", chunk, f"{source_url}@{revision}"])
        for entry in root.iter("logentry"):
            entries.append({
                "revisao": entry.get("revision"),
                "autor": entry.findtext("author"),
                "data": entry.findtext("date"),
                "mensagem": (entry.findtext("msg") or "").strip()[:500],
            })
    return sorted(entries, key=lambda e: int(e["revisao"] or 0), reverse=True)


def parse_merge_output(text: str, wc: Path) -> tuple[list[dict], list[dict]]:
    """(arquivos, conflitos) a partir da saida do svn merge (com ou sem --dry-run)."""
    prefix = str(wc).replace("\\", "/").rstrip("/") + "/"
    files: list[dict] = []
    conflicts: list[dict] = []
    for line in text.splitlines():
        match = MERGE_LINE.match(line)
        if not match:
            continue
        text_status, prop_status, _, tree_status, path = match.groups()
        path = path.strip().replace("\\", "/")
        if path.startswith(prefix):
            path = path[len(prefix):]
        if path in (".", str(wc).replace("\\", "/")):
            continue  # so o registro de mergeinfo na raiz
        tipo = "arvore" if tree_status == "C" else "texto" if text_status == "C" else "propriedade" if prop_status == "C" else None
        if tipo:
            conflicts.append({"caminho": path, "tipo": tipo})
        files.append({"caminho": path, "acao": ACTIONS.get(text_status.strip() or "U", "alterado") if not tipo else "conflito"})
    return files, conflicts


def status_conflicts(svn: Svn, wc: Path) -> list[dict]:
    """Conflitos que ficaram na copia depois de um merge de verdade (svn status --xml)."""
    root = svn.xml(["status", str(wc)])
    prefix = str(wc).replace("\\", "/").rstrip("/") + "/"
    out = []
    for entry in root.iter("entry"):
        status = entry.find("wc-status")
        if status is None:
            continue
        tipo = ("arvore" if status.get("tree-conflicted") == "true" else "texto" if status.get("item") == "conflicted"
                else "propriedade" if status.get("props") == "conflicted" else None)
        if tipo:
            path = (entry.get("path") or "").replace("\\", "/")
            out.append({"caminho": path[len(prefix):] if path.startswith(prefix) else path, "tipo": tipo})
    return out


def has_local_changes(svn: Svn, wc: Path) -> bool:
    root = svn.xml(["status", str(wc)])
    return any(e.find("wc-status") is not None and e.find("wc-status").get("item") != "unversioned" for e in root.iter("entry"))


def default_message(request: dict, revisions: list[int], source_revision: str) -> str:
    if not revisions:
        revs = "(nenhuma)"
    elif len(revisions) <= 20:
        revs = ", ".join(f"r{r}" for r in revisions)
    else:
        revs = f"r{revisions[0]} a r{revisions[-1]} ({len(revisions)} revisões)"
    return "\n".join([
        f"Merge de {request.get('source_name')} para {request.get('target_name')} pelo dashboard (AgenteTC)",
        "",
        "Pedido: {pedido}",
        "Solicitado por: " + str(request.get("requested_by") or "(desconhecido)"),
        f"Origem: {request.get('source_name')} até r{source_revision}",
        "Revisões: " + revs,
    ])


def commit_message(request: dict, revisions: list[int] | None = None, source_revision: str = "") -> str:
    preview = request.get("preview_json") or {}
    text = str(request.get("commit_message") or "").strip() or str(preview.get("mensagem_padrao") or "").strip()
    if not text:
        text = default_message(request, revisions or [], str(preview.get("source_revision") or source_revision))
    request_id = str(request.get("id"))
    text = text.replace("\r\n", "\n").replace("{pedido}", request_id).rstrip()
    if request_id not in text:
        # Mensagem editada sem o numero: o Bridge precisa achar o proprio commit no log.
        text += f"\n\n(AgenteTC pedido {request_id})"
    return text + "\n"


def find_commit_of_request(svn: Svn, target_url: str, request_id: str) -> str | None:
    """Revisao do commit deste pedido no destino (para retomar se o Bridge caiu apos o commit)."""
    root = svn.xml(["log", "-l", "30", target_url])
    for entry in root.iter("logentry"):
        if request_id in (entry.findtext("msg") or ""):
            return entry.get("revision")
    return None


# ---------------------------------------------------------------- previa e execucao

def preview(request: dict, svn: Svn, config: dict[str, object]) -> dict:
    source_url, target_url = str(request["source_url"]), str(request["target_url"])
    if not (allowed_url(source_url, config) and allowed_url(target_url, config)):
        return {"status": "erro", "error_message": "Branch fora do projeto configurado (MERGE_PROJECT_URL)."}
    wc = prepare_wc(svn, config, target_url)
    try:
        source_revision = last_changed_revision(svn, source_url)
        target_revision = last_changed_revision(svn, target_url)
        source = f"{source_url}@{source_revision}"
        revisions = eligible_revisions(svn, source, wc)
        base = {"source_revision": source_revision, "target_revision": target_revision, "gerada_em": now_iso()}
        if not revisions:
            return {"status": "sem_alteracoes", "preview_json": {**base, "revisoes": [], "total_revisoes": 0},
                    "error_message": f"{request.get('target_name')} ja tem tudo de {request.get('source_name')} (ate r{source_revision})."}
        # Rodando de dentro da copia, o svn mostra os caminhos relativos a ela (Script/x.sd).
        result = svn.run(["merge", "--dry-run", "--accept", "postpone", source, "."], kind="merge", cwd=wc)
        files, conflicts = parse_merge_output(decode(result.stdout), wc)
        preview_json = {
            **base,
            "revisoes": revision_log(svn, source_url, source_revision, revisions),
            "total_revisoes": len(revisions),
            "arquivos": files[:MAX_LISTED_FILES],
            "total_arquivos": len(files),
            "conflitos": conflicts,
            "mensagem_padrao": default_message(request, revisions, source_revision),
        }
        if conflicts:
            return {"status": "conflito", "preview_json": preview_json,
                    "error_message": f"A previa encontrou {len(conflicts)} conflito(s). " + CONFLICT_MESSAGE}
        return {"status": "previa_pronta", "preview_json": preview_json}
    finally:
        clean_wc(svn, wc)


def execute(request: dict, svn: Svn, config: dict[str, object], *, retry: bool = True) -> dict:
    source_url, target_url = str(request["source_url"]), str(request["target_url"])
    if not (allowed_url(source_url, config) and allowed_url(target_url, config)):
        return {"status": "erro", "error_message": "Branch fora do projeto configurado (MERGE_PROJECT_URL)."}
    preview_json = request.get("preview_json") or {}
    source_revision = str(preview_json.get("source_revision") or "") or last_changed_revision(svn, source_url)
    source = f"{source_url}@{source_revision}"
    wc = prepare_wc(svn, config, target_url)
    try:
        revisions = eligible_revisions(svn, source, wc)
        if not revisions:
            done = find_commit_of_request(svn, target_url, str(request["id"]))
            if done:  # o Bridge caiu depois do commit: so registra
                return {"status": "concluido", "svn_revision": done, "result_json": {"retomado": True}}
            return {"status": "sem_alteracoes", "result_json": {},
                    "error_message": f"{request.get('target_name')} ja tinha tudo de {request.get('source_name')} (ate r{source_revision})."}
        result = svn.run(["merge", "--accept", "postpone", source, "."], kind="merge", cwd=wc)
        files, _ = parse_merge_output(decode(result.stdout), wc)
        conflicts = status_conflicts(svn, wc)
        if conflicts:
            return {"status": "conflito", "result_json": {"conflitos": conflicts, "arquivos": files[:MAX_LISTED_FILES]},
                    "error_message": f"O merge encontrou {len(conflicts)} conflito(s). " + CONFLICT_MESSAGE}
        if not config["commit"]:
            return {"status": "erro", "result_json": {"simulacao": True, "arquivos": files[:MAX_LISTED_FILES]},
                    "error_message": "Modo simulacao (MERGE_COMMIT=0): merge feito sem conflito e desfeito; nada foi gravado."}
        if not has_local_changes(svn, wc):
            return {"status": "sem_alteracoes", "result_json": {}, "error_message": "O merge nao alterou nada no destino."}
        with tempfile.TemporaryDirectory(prefix="merge_msg_") as temp_dir:
            message_file = Path(temp_dir) / "mensagem.txt"
            message_file.write_bytes(commit_message(request, revisions, source_revision).encode("utf-8"))
            committed = svn.run(["commit", "-F", str(message_file), "--encoding", "utf-8", str(wc)], kind="commit", check=False)
        if committed.returncode != 0:
            error = svn.err(committed)
            if is_out_of_date(error) and retry:
                log(f"Merge {request['id']}: destino mudou durante o merge; tentando de novo.")
                return execute(request, svn, config, retry=False)
            raise SvnError("Commit recusado pelo SVN: " + error)
        revision = find_commit_of_request(svn, target_url, str(request["id"]))
        if not revision:
            raise SvnError("Commit feito, mas a revisao nao foi encontrada no log do destino.")
        return {"status": "concluido", "svn_revision": revision,
                "result_json": {"arquivos": files[:MAX_LISTED_FILES], "total_arquivos": len(files),
                                "source_revision": source_revision, "total_revisoes": len(revisions)}}
    finally:
        clean_wc(svn, wc)


def handle(request: dict, api: Api, svn: Svn, config: dict[str, object]) -> None:
    request_id = str(request["id"])
    is_preview = request.get("status") in ("previa_solicitada", "previa_processando")
    try:
        fields = preview(request, svn, config) if is_preview else execute(request, svn, config)
    except Exception as exc:  # nunca deixar o pedido preso em processamento
        fields = {"status": "erro", "error_message": "Falha no MergeBridge: " + str(exc)[:800]}
    api.update(request_id, fields)
    log(f"Merge {request_id} ({'previa' if is_preview else 'execucao'}) {request.get('source_name')} -> "
        f"{request.get('target_name')}: {fields['status']}" + (f" (r{fields['svn_revision']})" if fields.get("svn_revision") else "")
        + (f" - {fields['error_message']}" if fields.get("error_message") else ""))


def refresh_branches(api: Api, svn: Svn, config: dict[str, object]) -> None:
    branches = list_branches(svn, config)
    api.publish_branches(branches)
    log_change("branches", len(branches), f"Lista de branches publicada ({len(branches)}).")


def run_once(api: Api, svn: Svn, config: dict[str, object]) -> int:
    handled = 0
    for request in api.pending():
        claimed = api.claim(str(request["id"]))
        if not claimed:
            continue
        handle(claimed, api, svn, config)
        handled += 1
    return handled


def run_loop() -> None:
    config = load_config()
    api, svn = Api(config), Svn(config)
    log("Merge Bridge iniciado (" + ("COMMIT ativo" if config["commit"] else "modo SIMULACAO") + f"), copia em {config['wc_dir']}.")
    try:
        for request in api.processing():
            log(f"Retomando merge {request['id']} que estava em processamento.")
            handle(request, api, svn, config)
    except Exception as exc:
        log("Nao foi possivel retomar merges em processamento: " + str(exc))

    last_refresh = 0.0
    while True:
        try:
            if time.monotonic() - last_refresh >= int(config["branches_refresh_seconds"]):
                refresh_branches(api, svn, config)
                last_refresh = time.monotonic()
            run_once(api, svn, config)
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
        LOGGER.exception("[" + now_iso() + "] Merge Bridge encerrado por erro fatal.")
        raise

"""Pre-definicoes de rodagem Jenkins: validacao e montagem das linhas salvas pela API."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

PRESET_MODES = ("simplificada", "configurada")
SIMPLIFIED_FIELDS = ("vm_name", "modulo", "versao")
MAX_NAME_LENGTH = 80
MAX_CONFIG_BYTES = 20_000


class RunPresetError(ValueError):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error


def build_run_preset(
    body: dict[str, Any],
    existing_presets: list[dict[str, Any]],
    *,
    current: dict[str, Any] | None = None,
    username: str | None = None,
) -> dict[str, Any]:
    """Valida o pedido e devolve a linha completa a gravar (nova ou atualizada)."""
    mode = str(body.get("modo") or body.get("mode") or (current or {}).get("mode") or "").strip()
    if mode not in PRESET_MODES:
        raise RunPresetError(HTTPStatus.BAD_REQUEST, "invalid_run_preset_mode", "Modo invalido. Use simplificada ou configurada.")
    if current and mode != current.get("mode"):
        raise RunPresetError(HTTPStatus.BAD_REQUEST, "run_preset_mode_locked", "O modo de uma pre-definicao nao pode ser trocado.")

    raw_name = body.get("nome", body.get("name"))
    name = " ".join(str(raw_name if raw_name is not None else (current or {}).get("name") or "").split())
    if not name:
        raise RunPresetError(HTTPStatus.BAD_REQUEST, "run_preset_name_required", "Informe um nome para a pre-definicao.")
    if len(name) > MAX_NAME_LENGTH:
        raise RunPresetError(HTTPStatus.BAD_REQUEST, "run_preset_name_too_long", f"O nome pode ter no maximo {MAX_NAME_LENGTH} caracteres.")

    if "config" in body or "config_json" in body or not current:
        config = _clean_config(mode, body.get("config", body.get("config_json")))
    else:
        config = current.get("config_json") or {}

    preset_id = str((current or {}).get("id") or uuid.uuid4())
    # Cada usuario tem as suas: o nome so precisa ser unico entre as pre-definicoes do mesmo dono.
    owner = _owner(current) if current else (username or "")
    for other in existing_presets:
        if (other.get("id") != preset_id and other.get("mode") == mode and _owner(other) == owner
                and str(other.get("name") or "").casefold() == name.casefold()):
            raise RunPresetError(HTTPStatus.CONFLICT, "run_preset_name_exists", "Voce ja tem uma pre-definicao com esse nome.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "id": preset_id,
        "name": name,
        "mode": mode,
        "config_json": config,
        "created_by": (current or {}).get("created_by") if current else username,
        "updated_by": username,
        "created_at": (current or {}).get("created_at") or now,
        "updated_at": now,
    }


def _owner(row: dict[str, Any] | None) -> str:
    return str((row or {}).get("created_by") or "")


def check_preset_permission(row: dict[str, Any], username: str | None, is_admin: bool, *, delete: bool) -> None:
    """Editar: so o dono. Excluir: o dono ou um administrador. Sem dono (antigas): ninguem edita."""
    if username and _owner(row) == username:
        return
    if delete and is_admin:
        return
    raise RunPresetError(HTTPStatus.FORBIDDEN, "run_preset_not_owner",
                         "So quem criou pode " + ("excluir" if delete else "alterar") + " esta pre-definicao.")


def _clean_config(mode: str, config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise RunPresetError(HTTPStatus.BAD_REQUEST, "invalid_run_preset_config", "A configuracao deve ser um objeto JSON.")
    if mode == "simplificada":
        branch = str(config.get("branch") or "").strip()
        config = {key: str(config.get(key) or "").strip() for key in SIMPLIFIED_FIELDS}
        # Branch do TC so fica salva quando escolhida; sem ela a rodagem mantem a branch da VM.
        if branch:
            config["branch"] = branch
    else:
        # A data/hora e definida no momento do envio, nunca fica salva.
        config = {key: value for key, value in config.items() if key != "data_hora"}
    if len(json.dumps(config, ensure_ascii=False).encode("utf-8")) > MAX_CONFIG_BYTES:
        raise RunPresetError(HTTPStatus.BAD_REQUEST, "run_preset_config_too_large", "A configuracao esta grande demais.")
    return config

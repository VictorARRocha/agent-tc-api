from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .constants import MODULE_BY_PREFIX


@dataclass(frozen=True)
class CaseInfo:
    id_caso_teste: str
    nome_mds: str
    grupo: str
    descricao: str
    test_moniker: str
    caminho_hierarquico: str
    script_name: str
    procedure_name: str


NODE_RE = re.compile(r"^\[(?P<id>\d+(?:\.\d+)*)\]\s*(?P<name>.*)$")


def module_for_node_id(node_id: str) -> dict[str, str]:
    prefix = node_id.split(".", 1)[0]
    return MODULE_BY_PREFIX.get(prefix, MODULE_BY_PREFIX["0"])


def _split_moniker(moniker: str) -> tuple[str, str]:
    if not moniker:
        return "", ""
    match = re.match(r"^\{[^}]+\}(?P<procedure>.+)$", moniker)
    if match:
        return "", match.group("procedure")
    parts = re.split(r"[.:|]", moniker)
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", moniker


class MdsIndex:
    def __init__(self, mds_path: Path):
        self.mds_path = mds_path
        self.by_id: dict[str, dict[str, object]] = {}
        self.project_variables: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        root = ET.parse(self.mds_path).getroot()
        self._load_project_variables(root)

        def walk(node: ET.Element, stack: list[dict[str, str]]) -> None:
            raw_name = node.attrib.get("name", "")
            match = NODE_RE.match(raw_name)
            current_stack = stack
            if match:
                node_id = match.group("id")
                node_name = (match.group("name").strip() or raw_name).strip()
                parent = stack[-1]["node_id"] if stack else None
                path_ids = [item["node_id"] for item in stack] + [node_id]
                path_names = [item["node_name"] for item in stack] + [node_name]
                module = module_for_node_id(node_id)
                has_child_ids = any(NODE_RE.match(child.attrib.get("name", "")) for child in list(node))
                is_group = node.attrib.get("group", "").lower() == "true" or has_child_ids
                script, procedure = _split_moniker(node.attrib.get("testMoniker", ""))
                self.by_id[node_id] = {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"Unico|{node_id}")),
                    "sistema": "Unico",
                    "modulo_codigo": node_id.split(".", 1)[0],
                    "modulo_nome": module["nome"],
                    "node_id": node_id,
                    "parent_node_id": parent,
                    "node_name": node_name,
                    "node_type": "grupo" if is_group else "caso",
                    "full_path_ids": path_ids,
                    "full_path_names": path_names,
                    "full_path_label": " > ".join(
                        f"[{node_id_part}] {node_name_part}"
                        for node_id_part, node_name_part in zip(path_ids, path_names)
                    ),
                    "script_name": script,
                    "procedure_name": procedure,
                    "description": node.attrib.get("description", ""),
                    "testMoniker": node.attrib.get("testMoniker", ""),
                    "mds_path": str(self.mds_path),
                }
                current_stack = stack + [{"node_id": node_id, "node_name": node_name}]
            for child in list(node):
                walk(child, current_stack)

        walk(root, [])

    def _load_project_variables(self, root: ET.Element) -> None:
        for variable in root.iter("Variable"):
            name = variable.attrib.get("Name") or variable.attrib.get("name")
            if not name:
                continue
            default = variable.find("DefValue")
            if default is None:
                self.project_variables[name] = ""
                continue
            for attr in ("IntValue", "StrValue", "FloatValue", "BoolValue"):
                if attr in default.attrib:
                    self.project_variables[name] = default.attrib[attr]
                    break
            else:
                self.project_variables[name] = ""

    def project_variable_int(self, name: str) -> int | None:
        raw = self.project_variables.get(name)
        if raw is None:
            return None
        raw = raw.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def case_info(self, case_id: str) -> CaseInfo:
        row = self.by_id.get(case_id)
        if not row:
            return CaseInfo(
                id_caso_teste=case_id,
                nome_mds="Nao encontrado no Unico.mds",
                grupo="Nao encontrado no Unico.mds",
                descricao="",
                test_moniker="",
                caminho_hierarquico="Nao encontrado no Unico.mds",
                script_name="",
                procedure_name="",
            )
        parent = self.by_id.get(str(row.get("parent_node_id")))
        return CaseInfo(
            id_caso_teste=case_id,
            nome_mds=str(row["node_name"]),
            grupo=str(parent["node_name"]) if parent else str(row["modulo_nome"]),
            descricao=str(row.get("description") or ""),
            test_moniker=str(row.get("testMoniker") or ""),
            caminho_hierarquico=str(row["full_path_label"]),
            script_name=str(row.get("script_name") or ""),
            procedure_name=str(row.get("procedure_name") or ""),
        )

    def hierarchy_rows(self) -> list[dict[str, object]]:
        return [
            {key: value for key, value in row.items() if key not in {"description", "testMoniker"}}
            for row in self.by_id.values()
        ]

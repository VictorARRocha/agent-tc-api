from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


class ProjectSuiteVariables:
    def __init__(self, project_suite_path: Path):
        self.project_suite_path = project_suite_path
        self.project_variables: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        root = ET.parse(self.project_suite_path).getroot()
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


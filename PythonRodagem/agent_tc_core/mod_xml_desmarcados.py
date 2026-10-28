"""Casos que o mod_xml.py desmarca em toda rodagem (aba Casos de teste: aparecem como desativados).

O Jenkins roda o mod_xml.py antes do TC; ele desmarca no .mds os casos de uma lista fixa no codigo:

    if socket.gethostname() != 'a09':
        ct_desmarcar = ct_desmarcar + ['[1.3.4]', '[1.6.16]', ...]

O arquivo e lido como texto (ast), sem executar nada. Fica de fora o que depende do pedido
(parametro ct_desmarcar e a divisao para rodar em paralelo).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

MOD_XML_PADRAO = r"C:\TC\Util Compartilhado\Modificador unico mds\mod_xml.py"
_CASO = re.compile(r"^\[(\d+(?:\.\d+)*)\]$")


def _eh_gethostname(no: ast.AST) -> bool:
    return isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) and no.func.attr == "gethostname"


def _strings_da_lista(no: ast.AST) -> list[str]:
    """Strings de listas literais dentro de uma expressao (ct_desmarcar + [...])."""
    out = []
    for item in ast.walk(no):
        if isinstance(item, (ast.List, ast.Tuple)):
            for elt in item.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.append(elt.value)
    return out


def ler_desmarcados(texto: str) -> dict[str, Any] | None:
    """{"casos": ["1.3.4", ...], "exceto_vms": ["a09"]}; None se o arquivo nao tiver a lista."""
    try:
        arvore = ast.parse(texto)
    except SyntaxError:
        return None
    for no in ast.walk(arvore):
        if not isinstance(no, ast.If) or not isinstance(no.test, ast.Compare):
            continue
        teste = no.test
        if not (_eh_gethostname(teste.left) and len(teste.ops) == 1 and isinstance(teste.ops[0], ast.NotEq)):
            continue
        exceto = [c.value for c in teste.comparators if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        casos: list[str] = []
        for instrucao in no.body:
            if (
                isinstance(instrucao, ast.Assign)
                and any(isinstance(alvo, ast.Name) and alvo.id == "ct_desmarcar" for alvo in instrucao.targets)
            ):
                for texto_caso in _strings_da_lista(instrucao.value):
                    achado = _CASO.match(texto_caso.strip())
                    if achado and achado.group(1) not in casos:
                        casos.append(achado.group(1))
        if casos:
            return {"casos": casos, "exceto_vms": [vm.lower() for vm in exceto]}
    return None


def ler_desmarcados_arquivo(caminho: str | Path | None = None) -> dict[str, Any] | None:
    arquivo = Path(caminho or MOD_XML_PADRAO)
    if not arquivo.is_file():
        return None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return ler_desmarcados(arquivo.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
    return None


def motivo(exceto_vms: list[str]) -> str:
    fora = f" (exceto na {', '.join(vm.upper() for vm in exceto_vms)})" if exceto_vms else ""
    return f"Desmarcado em toda rodagem pelo mod_xml.py{fora}"

"""Permissoes por usuario do dashboard.

As acoes abaixo exigem a permissao (o admin pode tudo, sem depender da lista).
O que cada um ve depende dos modulos (mais abaixo). A lista fica na coluna `permissions` do usuario (JSON); vazia = so ver.
"""
from __future__ import annotations

import re
from typing import Any

from .api_repository import OFFICIAL_MODULES, SLUG_BY_MODULE_ID
from .constants import MODULE_CODES_BY_ID

RODAGEM = "rodagem"    # solicitar/cancelar rodagens no Jenkins (completa e reexecucao) e salvar pre-definicoes
MERGE = "merge"        # merge entre branches do TC
REGRAVAR = "regravar"  # regravar arquivos base no SVN

PERMISSIONS = (RODAGEM, MERGE, REGRAVAR)

DENIED_MESSAGES = {
    RODAGEM: "Voce nao tem permissao para solicitar rodagens. Peca a um administrador.",
    MERGE: "Voce nao tem permissao para fazer merge. Peca a um administrador.",
    REGRAVAR: "Voce nao tem permissao para regravar arquivos. Peca a um administrador.",
}


def normalize_permissions(value: Any) -> list[str]:
    """Lista guardada no usuario -> permissoes conhecidas, sem repeticao e na ordem padrao.

    Valores desconhecidos ou um formato inesperado (ex.: coluna ainda nula) viram "sem permissao".
    """
    if not isinstance(value, (list, tuple)):
        return []
    wanted = {str(item) for item in value}
    return [permission for permission in PERMISSIONS if permission in wanted]


def invalid_permissions(value: Any) -> list[str] | None:
    """Itens invalidos de uma lista enviada pelo admin; None se o formato nem e lista."""
    if not isinstance(value, list):
        return None
    return [str(item) for item in value if str(item) not in PERMISSIONS]


def has_permission(identity: dict[str, Any] | None, permission: str) -> bool:
    if not identity:
        return False
    if (identity.get("role") or "") == "admin":
        return True
    return permission in normalize_permissions(identity.get("permissions"))


# ---------------------------------------------------------------- modulos
# Alem da permissao, as acoes de rodagem e regravacao so valem nos modulos do usuario, e cada
# um so ve os dados (rodagens, falhas, evidencias, pedidos) dos seus modulos. Lista na coluna `modules` (JSON) com os slugs
# (folha, fiscal, ...) ou ["*"] = todos os modulos (padrao de quem ja existia).

ALL_MODULES = "*"
MODULE_SLUGS = tuple(module["slug"] for module in OFFICIAL_MODULES)
MODULE_NAMES = {module["slug"]: module["nome"] for module in OFFICIAL_MODULES}
# Codigo do caso de teste (o numero antes do primeiro ponto: [2.1.3] -> 2) -> slug do modulo.
MODULE_BY_CODE = {code: SLUG_BY_MODULE_ID[module_id] for module_id, codes in MODULE_CODES_BY_ID.items() for code in codes}
# Rotinas gerais (ex.: [0.3] desmarcar, [0.4]/[0.5] restaurar base): nao sao de nenhum modulo.
NEUTRAL_CODES = {"0"}
_CASE_GROUP = re.compile(r"\[([^\[\]]*)\]")
_CASE_CODE = re.compile(r"^\s*(\d+)")


def normalize_modules(value: Any) -> list[str]:
    """Lista guardada no usuario -> ["*"] ou os slugs conhecidos, na ordem oficial.

    Sem valor (usuario anterior a coluna) = todos os modulos, como ficou combinado para quem ja existia.
    """
    if value is None:
        return [ALL_MODULES]
    if not isinstance(value, (list, tuple)):
        return []
    wanted = {str(item) for item in value}
    if ALL_MODULES in wanted:
        return [ALL_MODULES]
    return [slug for slug in MODULE_SLUGS if slug in wanted]


def invalid_modules(value: Any) -> list[str] | None:
    """Itens invalidos de uma lista enviada (admin ou cadastro); None se o formato nem e lista."""
    if not isinstance(value, list):
        return None
    return [str(item) for item in value if str(item) not in MODULE_SLUGS and str(item) != ALL_MODULES]


def modules_of_test_cases(test_cases: Any) -> tuple[list[str], list[str]]:
    """Modulos dos casos de teste de um pedido de rodagem e os codigos desconhecidos.

    "[3], [4], [7]" -> contabil; "[2.1.3], [2.1.4]" -> fiscal; "[0.4], [9.1.2]" -> gestao.
    """
    modules: list[str] = []
    unknown: list[str] = []
    for group in _CASE_GROUP.findall(str(test_cases or "")):
        for item in group.split(","):
            match = _CASE_CODE.match(item)
            if not match:
                if item.strip() and item.strip() not in unknown:
                    unknown.append(item.strip())  # sem numero: nao da para saber o modulo
                continue
            code = str(int(match.group(1)))
            if code in NEUTRAL_CODES:
                continue
            slug = MODULE_BY_CODE.get(code)
            if slug is None:
                if code not in unknown:
                    unknown.append(code)
            elif slug not in modules:
                modules.append(slug)
    return modules, unknown


def has_all_modules(identity: dict[str, Any] | None) -> bool:
    """Admin ou "todos os modulos": nenhuma acao e barrada por modulo."""
    if not identity:
        return False
    if (identity.get("role") or "") == "admin":
        return True
    return ALL_MODULES in normalize_modules(identity.get("modules"))


def denied_modules(identity: dict[str, Any] | None, modules: list[str]) -> list[str]:
    """Modulos da lista fora do acesso do usuario (admin e "todos os modulos" passam em tudo)."""
    if not identity:
        return list(modules)
    if (identity.get("role") or "") == "admin":
        return []
    allowed = normalize_modules(identity.get("modules"))
    if ALL_MODULES in allowed:
        return []
    return [slug for slug in modules if slug not in allowed]


def can_see_module(identity: dict[str, Any] | None, slug: str | None) -> bool:
    """Dado de um modulo (rodagem, falha, evidencia). Sem modulo identificado: so quem tem todos."""
    if has_all_modules(identity):
        return True
    return bool(slug) and not denied_modules(identity, [str(slug)])


def module_of_hierarchy_row(row: dict[str, Any]) -> str:
    """Modulo de uma linha da arvore de casos de teste (pelo id do modulo ou pelo codigo)."""
    slug = SLUG_BY_MODULE_ID.get(str(row.get("module_id") or ""))
    if slug:
        return slug
    code = str(row.get("module_code") or row.get("modulo_codigo") or "").strip()
    return MODULE_BY_CODE.get(str(int(code)) if code.isdigit() else code, "")


def can_see_test_cases(identity: dict[str, Any] | None, test_cases: Any) -> bool:
    """Pedido de rodagem: precisa ter todos os modulos dos casos ([0.x] nao conta; codigo desconhecido esconde)."""
    if has_all_modules(identity):
        return True
    modules, unknown = modules_of_test_cases(test_cases)
    return not unknown and not denied_modules(identity, modules)


def module_denied_message(modules: list[str]) -> str:
    names = ", ".join(MODULE_NAMES.get(slug, slug) for slug in modules)
    which = "ao modulo" if len(modules) == 1 else "aos modulos"
    return f"Voce nao tem acesso {which} {names}. Peca a um administrador."

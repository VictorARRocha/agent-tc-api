from __future__ import annotations

MODULE_BY_PREFIX = {
    "0": {"id": "mod_geral", "nome": "Geral", "sistema": "Unico"},
    "1": {"id": "mod_folha", "nome": "Folha", "sistema": "Unico"},
    "2": {"id": "mod_fiscal", "nome": "Fiscal", "sistema": "Unico"},
    "3": {"id": "mod_contabil", "nome": "Cont\u00e1bil", "sistema": "Unico"},
    "4": {"id": "mod_contabil", "nome": "Cont\u00e1bil", "sistema": "Unico"},
    "5": {"id": "mod_financeiro", "nome": "Financeiro", "sistema": "Unico"},
    "6": {"id": "mod_geral", "nome": "Geral", "sistema": "Unico"},
    "7": {"id": "mod_contabil", "nome": "Cont\u00e1bil", "sistema": "Unico"},
    "9": {"id": "mod_gestao", "nome": "Gest\u00e3o", "sistema": "Unico"},
    "16": {"id": "mod_suprema", "nome": "Suprema", "sistema": "Suprema"},
    "19": {"id": "mod_practice", "nome": "Practice", "sistema": "Practice"},
}

MODULE_CODES_BY_ID = {
    "mod_folha": ("1",),
    "mod_fiscal": ("2",),
    "mod_contabil": ("3", "4", "7"),
    "mod_financeiro": ("5",),
    "mod_geral": ("6",),
    "mod_gestao": ("9",),
    "mod_suprema": ("16",),
    "mod_practice": ("19",),
}

STATUS_QUEBRA = "Quebra de testes"
STATUS_DIFERENCA = "Diferen\u00e7a entre arquivos de compara\u00e7\u00e3o"
STATUS_AMBOS = "Quebra com diferen\u00e7a"
STATUS_SEM_SINAL = "Sem classifica\u00e7\u00e3o objetiva"

ARCHIVE_EXTENSIONS = {".rar", ".zip"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}
TEXT_EXTENSIONS = {".txt", ".log", ".csv", ".xml", ".htm", ".html", ".ini"}

BASE_MARKERS = ("_antigo", "_base", "_padrao", "_padr\u00e3o", "_esperado")
CURRENT_MARKERS = ("_atual", "_atualizado", "_gerado")

ERROR_FILE_NAMES = {"informacaoerro.txt", "erro.txt", "callstack.txt"}

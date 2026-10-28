"""Tempo de cada caso de teste: medicoes no banco comparadas com um tempo base por caso.

Fonte: o TC grava, a cada caso que passa, o tempo em S:\\Teste automatico\\Arquivos\\Performance\\<Sistema> Tempo CT.csv
(uma linha por caso: "3.1.2;00:00:17 - 25/06/2026-19:20:54;00:00:16 - 27/06/2026-04:15:58;...", com a data/hora de
inicio da rodagem). A pasta "Planilhas Tempo Padrao" tem o tempo base antigo de cada caso ("3.1.2;00:00:15").

Regras (combinadas com o time):
- base por caso; sem base, a primeira medicao vira a base;
- so quem pode regravar arquivos troca a base (com confirmacao no dashboard);
- avisa quando a diferenca passa do limite da faixa (tabela LIMITES), nunca menos de 15s.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .constants import MODULE_BY_PREFIX
from .performance import _read_text

FUSO = timezone(timedelta(hours=-3))
LIMITE_MINIMO_SEGUNDOS = 15
# (base a partir de, porcentagem): ate 1 min vale so os 15s.
LIMITES = ((600, 0.05), (300, 0.07), (120, 0.10), (60, 0.15))
TENDENCIA_RODAGENS = 10
PERFORMANCE_DIR_PADRAO = r"S:\Teste automatico\Arquivos\Performance"

_MEDICAO_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}) - (\d{2})/(\d{2})/(\d{4})-(\d{2}):(\d{2}):(\d{2})")
_BASE_RE = re.compile(r"(\d+(?:\.\d+)*);(\d{2}):(\d{2}):(\d{2})")
_ID_RE = re.compile(r"^\d+(?:\.\d+)*$")


def limite_segundos(base: int) -> int:
    """Diferenca (para mais ou para menos) a partir da qual o caso e marcado."""
    for a_partir, pct in LIMITES:
        if base >= a_partir:
            return max(LIMITE_MINIMO_SEGUNDOS, round(base * pct))
    return LIMITE_MINIMO_SEGUNDOS


def classificar(base: int, atual: int) -> str:
    diferenca = atual - base
    if base <= 0 or abs(diferenca) <= limite_segundos(base):
        return "igual"
    return "mais_lento" if diferenca > 0 else "mais_rapido"


def instante(valor: Any) -> datetime:
    """Data/hora do banco (datetime ou texto ISO) com fuso, para comparar."""
    if isinstance(valor, datetime):
        dt = valor
    else:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=FUSO)


def iso(dt: datetime) -> str:
    return dt.astimezone(FUSO).isoformat(timespec="seconds")


def medicao_id(node_id: str, medido_em: str) -> str:
    return f"{node_id}|{iso(instante(medido_em))}"


# --- planilhas do TC -------------------------------------------------------------------------

def ler_tempo_ct(texto: str) -> dict[str, list[tuple[str, int]]]:
    """Historico: caso -> [(inicio da rodagem ISO, segundos)]. Linhas separadas por CR (como o TC grava)."""
    out: dict[str, list[tuple[str, int]]] = {}
    for linha in re.split(r"[\r\n]+", texto.lstrip("\ufeff")):
        caso, _, resto = linha.partition(";")
        caso = caso.strip()
        if not _ID_RE.match(caso):
            continue
        for h, m, s, dia, mes, ano, hh, mm, ss in _MEDICAO_RE.findall(resto):
            segundos = int(h) * 3600 + int(m) * 60 + int(s)
            if segundos <= 0:
                continue
            quando = datetime(int(ano), int(mes), int(dia), int(hh), int(mm), int(ss), tzinfo=FUSO)
            out.setdefault(caso, []).append((iso(quando), segundos))
    return out


def ler_tempo_padrao(texto: str) -> dict[str, int]:
    """Base antiga: caso -> segundos (a ultima linha de um caso vale)."""
    out = {}
    for caso, h, m, s in _BASE_RE.findall(texto.lstrip("\ufeff")):
        segundos = int(h) * 3600 + int(m) * 60 + int(s)
        if segundos > 0:
            out[caso] = segundos
    return out


def arquivos_tempo_ct(pasta: str | Path | None) -> list[Path]:
    if not pasta or not Path(pasta).is_dir():
        return []
    return sorted(Path(pasta).glob("* Tempo CT.csv"))


def medicoes_da_rodagem(pasta: str | Path | None, inicio_rodagem: str) -> list[dict[str, Any]]:
    """Medicoes gravadas pelo TC com a mesma data/hora de inicio da rodagem (a do nome da pasta)."""
    alvo = iso(instante(inicio_rodagem))
    out = []
    for arquivo in arquivos_tempo_ct(pasta):
        for caso, medicoes in ler_tempo_ct(_read_text(arquivo)).items():
            for quando, segundos in medicoes:
                if quando == alvo:
                    out.append({"codigo_teste": caso, "segundos": segundos, "medido_em": quando})
    return out


def medicoes_do_sistema(medicoes: list[dict[str, Any]], sistema: str) -> list[dict[str, Any]]:
    """Fica so com os casos do sistema da rodagem (Unico, Suprema ou Practice).

    Cada sistema grava o seu '<Sistema> Tempo CT.csv'; uma rodagem de outro sistema que tenha
    comecado no mesmo segundo nao pode entrar nesta.
    """
    return [m for m in medicoes if sistema_do_caso(str(m.get("codigo_teste") or "")) == sistema]


def sistema_do_caso(codigo: str) -> str:
    prefixo = codigo.split(".", 1)[0]
    return MODULE_BY_PREFIX.get(prefixo, MODULE_BY_PREFIX["0"])["sistema"]


# --- linhas da aba Performance ---------------------------------------------------------------

def hms(segundos: int) -> str:
    sinal = "-" if segundos < 0 else ""
    s = abs(int(segundos))
    return f"{sinal}{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def linhas_performance(
    run_id: str,
    medicoes_rodagem: list[dict[str, Any]],
    bases: dict[str, dict[str, Any]],
    historico: Iterable[dict[str, Any]],
    nomes: dict[str, str],
) -> list[dict[str, Any]]:
    """Uma linha por caso medido na rodagem, no formato da aba Performance (+ base e tendencia)."""
    por_caso: dict[str, list[tuple[datetime, int]]] = {}
    for item in historico:
        por_caso.setdefault(str(item["node_id"]), []).append((instante(item["measured_at"]), int(item["seconds"])))
    out = []
    for medicao in medicoes_rodagem:
        caso = str(medicao["node_id"])
        atual = int(medicao["seconds"])
        quando = instante(medicao["measured_at"])
        base_row = bases.get(caso) or {}
        base = int(base_row.get("base_seconds") or 0)
        diferenca = atual - base if base else 0
        anteriores = sorted(m for m in por_caso.get(caso, []) if m[0] <= quando)
        tendencia = [segundos for _, segundos in anteriores[-TENDENCIA_RODAGENS:]] or [atual]
        out.append(
            {
                "id": f"tempo_{run_id}_{caso}",
                "id_atraso": f"tempo_{run_id}_{caso}",
                "run_id": run_id,
                "fk_rodagem": run_id,
                "codigo_teste": caso,
                "nome_teste": nomes.get(caso, ""),
                "tempo_padrao_segundos": base,
                "tempo_atual_segundos": atual,
                "base_segundos": base,
                "atual_segundos": atual,
                "delay_segundos": diferenca,
                "variacao_pct": (diferenca / base * 100) if base else 0.0,
                "tempo_padrao": hms(base) if base else None,
                "tempo_atual": hms(atual),
                "delay_detectado": hms(diferenca),
                "status": classificar(base, atual) if base else "igual",
                "limite_segundos": limite_segundos(base) if base else None,
                "base_origem": base_row.get("origin"),
                "base_definida_em": base_row.get("changed_at"),
                "base_definida_por": base_row.get("changed_by"),
                "tendencia": tendencia,
                "medido_em": iso(quando),
                "created_at": iso(quando),
            }
        )
    return sorted(out, key=lambda r: (-abs(r["delay_segundos"]) if r["status"] != "igual" else 0, r["codigo_teste"]))


def historico_do_caso(
    node_id: str,
    medicoes: list[dict[str, Any]],
    base_row: dict[str, Any] | None,
    rodagens: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Medicoes do caso (mais recente primeiro) com a versao da rodagem de cada uma.

    O Tempo CT.csv nao grava a versao: ela vem da rodagem do AgenteTC (pelo run_id ou, no historico
    importado, pela rodagem que comecou na mesma data/hora). Sem rodagem registrada, fica sem versao.
    """
    base = int((base_row or {}).get("base_seconds") or 0)
    itens = sorted(medicoes, key=lambda m: instante(m["measured_at"]), reverse=True)
    versao_por_id: dict[str, str] = {}
    versao_por_inicio: dict[str, str] = {}
    for rodagem in rodagens or []:
        versao = str(rodagem.get("version") or "")
        if not versao or not rodagem.get("started_at"):
            continue
        versao_por_id[str(rodagem.get("id"))] = versao
        versao_por_inicio.setdefault(iso(instante(rodagem["started_at"])), versao)
    return {
        "codigo_teste": node_id,
        "base_segundos": base or None,
        "base_origem": (base_row or {}).get("origin"),
        "base_anterior_segundos": (base_row or {}).get("previous_seconds"),
        "base_definida_em": (base_row or {}).get("changed_at"),
        "base_definida_por": (base_row or {}).get("changed_by"),
        "limite_segundos": limite_segundos(base) if base else None,
        "medicoes": [
            {
                "medido_em": iso(instante(m["measured_at"])),
                "segundos": int(m["seconds"]),
                "run_id": m.get("run_id"),
                "versao": versao_por_id.get(str(m.get("run_id"))) or versao_por_inicio.get(iso(instante(m["measured_at"]))),
                "status": classificar(base, int(m["seconds"])) if base else "igual",
            }
            for m in itens
        ],
    }


def linhas_medicao(medicoes: list[dict[str, Any]], run_id: str | None, criado_em: str) -> list[dict[str, Any]]:
    """Linhas da tabela testcase_times. Sem rodagem (importacao do historico), run_id nao vai na linha."""
    out = []
    for m in medicoes:
        caso = str(m["codigo_teste"]).strip()
        segundos = int(m["segundos"])
        if not _ID_RE.match(caso) or segundos <= 0:
            continue
        quando = iso(instante(m["medido_em"]))
        row = {"id": medicao_id(caso, quando), "node_id": caso, "measured_at": quando, "seconds": segundos, "created_at": criado_em}
        if run_id:
            row["run_id"] = run_id
        out.append(row)
    return out


def bases_novas(medicoes: list[dict[str, Any]], existentes: set[str], agora: str) -> list[dict[str, Any]]:
    """Primeira medicao de cada caso ainda sem base vira a base."""
    primeira: dict[str, dict[str, Any]] = {}
    for m in medicoes:
        caso = m["node_id"]
        if caso in existentes:
            continue
        if caso not in primeira or instante(m["measured_at"]) < instante(primeira[caso]["measured_at"]):
            primeira[caso] = m
    return [
        {"id": caso, "base_seconds": m["seconds"], "origin": "primeira_medicao", "changed_at": m["measured_at"] or agora}
        for caso, m in primeira.items()
    ]

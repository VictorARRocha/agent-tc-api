"""Carga inicial dos tempos dos casos (aba Performance): roda uma vez, numa maquina com o S: e o .env da VM.

- Base: "Planilhas Tempo Padrao\\<Sistema> Tempo Padrao.csv" (so entra em caso que ainda nao tem base no banco).
- Historico: "<Sistema> Tempo CT.csv" (todas as medicoes; caso sem base na planilha usa a primeira medicao).

Pode rodar de novo sem duplicar nada (cada medicao e unica por caso e inicio de rodagem).

Uso:
  py -3 cli\\agent_tc_importar_tempos.py --dry-run     (so conta)
  py -3 cli\\agent_tc_importar_tempos.py               (envia para a API do .env)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_tc_core.api_repository import RemoteApiRepository  # noqa: E402
from agent_tc_core.performance import _read_text  # noqa: E402
from agent_tc_core.tempos_casos import (  # noqa: E402
    PERFORMANCE_DIR_PADRAO,
    arquivos_tempo_ct,
    ler_tempo_ct,
    ler_tempo_padrao,
)

LOTE = 5000


def ler_pasta(pasta: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    bases: dict[str, int] = {}
    for arquivo in sorted((pasta / "Planilhas Tempo Padrão").glob("* Tempo Padrão.csv")):
        bases.update(ler_tempo_padrao(_read_text(arquivo)))
    medicoes = [
        {"codigo_teste": caso, "segundos": segundos, "medido_em": quando}
        for arquivo in arquivos_tempo_ct(pasta)
        for caso, itens in ler_tempo_ct(_read_text(arquivo)).items()
        for quando, segundos in itens
    ]
    # Mais antigas primeiro: o caso sem base na planilha fica com a primeira medicao de verdade.
    medicoes.sort(key=lambda m: str(m["medido_em"]))
    return bases, medicoes


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa a base e o historico de tempos dos casos para a API.")
    parser.add_argument("--pasta", default=PERFORMANCE_DIR_PADRAO, help="Pasta Performance do S:.")
    parser.add_argument("--env", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--dry-run", action="store_true", help="So le e conta, sem enviar.")
    args = parser.parse_args()

    pasta = Path(args.pasta)
    if not pasta.is_dir():
        print(f"Pasta nao encontrada: {pasta}")
        return 1
    bases, medicoes = ler_pasta(pasta)
    casos = {str(m["codigo_teste"]) for m in medicoes}
    print(f"Bases na planilha: {len(bases)} | medicoes: {len(medicoes)} | casos com medicao: {len(casos)}")
    if args.dry_run:
        return 0

    api = RemoteApiRepository(env_path=args.env)
    total = {"medicoes": 0, "bases_planilha": 0, "bases_primeira_medicao": 0}
    lotes = [medicoes[i : i + LOTE] for i in range(0, len(medicoes), LOTE)] or [[]]
    for numero, lote in enumerate(lotes, 1):
        # As bases da planilha vao no primeiro lote, antes de qualquer "primeira medicao".
        resposta = api._post_json("/testcase-times/import", {"bases": bases if numero == 1 else {}, "medicoes": lote})
        for chave in total:
            total[chave] += int(resposta.get(chave) or 0)
        print(f"Lote {numero}/{len(lotes)} enviado.")
    print(json.dumps(total, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

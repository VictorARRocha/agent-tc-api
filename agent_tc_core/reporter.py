from __future__ import annotations

import json
from pathlib import Path


def write_shadow_reports(output_dir: Path, payload: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "shadow_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "ia_input.json").write_text(
        json.dumps(payload["ia_input"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "shadow_summary.md").write_text(
        build_summary(payload), encoding="utf-8"
    )


def build_summary(payload: dict[str, object]) -> str:
    rodagem = payload["rodagem"]
    modulo = payload["modulo"]
    falhas = payload["falhas"]
    evidencias = payload["evidencias"]
    diffs = payload["diferencas_relatorio"]
    hierarchy = payload.get("testcase_hierarchy", [])

    lines = [
        "# Agent TC Shadow Report",
        "",
        f"- Rodagem: {rodagem['id_rodagem']}",
        f"- VM: {rodagem['vm_name']}",
        f"- Versao: {rodagem['versao']}",
        f"- Modulo: {modulo['nome']}",
        f"- Falhas analisadas: {len(falhas)}",
        f"- Evidencias preparadas: {len(evidencias)}",
        f"- Diferencas detectadas: {len(diffs)}",
        f"- Hierarquia MDS preparada: {len(hierarchy)}",
        "",
        "## Falhas",
        "",
        "| Caso | Nome MDS | Tipo Python | Evidencias |",
        "|---|---|---|---|",
    ]
    for falha in falhas:
        count = len([e for e in evidencias if e["fk_falha"] == falha["id_falha"]])
        lines.append(
            "| {id_caso_teste} | {nome_mds} | {tipo_detectado_python} | {count} |".format(
                count=count, **falha
            )
        )
    return "\n".join(lines) + "\n"

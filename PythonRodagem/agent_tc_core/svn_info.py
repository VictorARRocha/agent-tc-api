"""Origem SVN da rodagem: URL e revisao da copia de trabalho do projeto na VM.

Usado pela regravacao pelo dashboard para saber em qual branch (URL) gravar a
nova base. Qualquer falha (svn ausente, pasta sem .svn, timeout) devolve None:
a analise da rodagem nunca depende disto.
"""
from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

SVN_TIMEOUT_SECONDS = 20
TORTOISE_SVN = Path(r"C:\Program Files\TortoiseSVN\bin\svn.exe")


def find_svn() -> str | None:
    found = shutil.which("svn")
    if found:
        return found
    return str(TORTOISE_SVN) if TORTOISE_SVN.exists() else None


def parse_svn_info_xml(text: str) -> dict[str, str] | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    entry = root.find("entry")
    if entry is None:
        return None
    url = (entry.findtext("url") or "").strip()
    if not url:
        return None
    return {
        "url": url,
        "raiz": (entry.findtext("repository/root") or "").strip(),
        "revisao": (entry.get("revision") or "").strip(),
    }


def working_copy_info(path: str | Path) -> dict[str, str] | None:
    """URL, raiz do repositorio e revisao da copia de trabalho em `path`, ou None.

    O .svn pode estar numa pasta acima (Suprema e Practice: o .mds fica numa subpasta da
    copia de trabalho). O `svn info` roda em `path` mesmo, para a URL ser a do projeto,
    onde fica o Stores que a regravacao usa.
    """
    folder = Path(path)
    svn = find_svn()
    if not svn or not any((pasta / ".svn").exists() for pasta in (folder, *folder.parents)):
        return None
    try:
        result = subprocess.run(
            [svn, "info", "--xml", "--non-interactive", str(folder)],
            capture_output=True,
            timeout=SVN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_svn_info_xml(result.stdout.decode("utf-8", errors="replace"))

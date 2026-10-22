"""Licenze delle librerie distribuite negli installatori: packaging/build/THIRD_PARTY_LICENSES.txt.

Le licenze MIT, BSD e Apache chiedono di accompagnare i binari con i loro avvisi: il file finisce nel pacchetto.
Fa anche da controllo: se nel pacchetto entrerebbe una libreria GPL o LGPL, la build si ferma.
Uso: uv run --group build python packaging/third_party.py
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "build" / "THIRD_PARTY_LICENSES.txt"
# Strumenti di sviluppo e di build (non finiscono negli installatori) e librerie escluse dal pacchetto.
SKIP = ["pytest", "pytest-cov", "coverage", "pyinstaller", "pyinstaller-hooks-contrib", "pip-licenses", "prettytable",
        "wcwidth", "altgraph", "pefile", "macholib", "pywin32-ctypes", "setuptools", "iniconfig", "pluggy", "packaging",
        "wmt-pdf",
        "svglib"]  # LGPL: esclusa in wmt-pdf.spec (xhtml2pdf ne fa a meno, senza le immagini SVG)
# Licenze multiple o metadati incompleti, verificati a mano.
ACCEPTED = {
    "odfpy": "triplice licenza: si usa sotto Apache-2.0",
    "clr_loader": "MIT (parte di pythonnet; metadati senza licenza)",
}
COPYLEFT = ("GPL", "GENERAL PUBLIC")


def piplicenses(*args: str) -> str:
    env = os.environ | {"PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "piplicenses", "--ignore-packages", *SKIP, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          check=True).stdout


def main() -> int:
    rows = list(csv.DictReader(io.StringIO(piplicenses("--format=csv"))))
    blocked = [f"{r['Name']} ({r['License']})" for r in rows
               if r["Name"] not in ACCEPTED and (any(w in r["License"].upper() for w in COPYLEFT)
                                                 or r["License"] in ("UNKNOWN", ""))]
    if blocked:
        print("licenze da verificare prima di distribuire:\n- " + "\n- ".join(blocked), file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdfjs = (HERE.parent / "src" / "wmt_pdf" / "gui" / "static" / "vendor" / "PDFJS-LICENSE.txt").read_text(
        encoding="utf-8")
    notes = "".join(f"- {name}: {why}\n" for name, why in ACCEPTED.items())
    OUT.write_text(
        "wmt-pdf include le librerie seguenti, ciascuna con la sua licenza.\n"
        "wmt-pdf includes the following libraries, each under its own license.\n\n"
        f"Note:\n{notes}\n"
        "PDF.js (Mozilla, Apache-2.0)\n" + pdfjs + "\n\n"
        + piplicenses("--with-license-file", "--with-notice-file", "--no-license-path", "--format=plain-vertical"),
        encoding="utf-8",
    )
    print(f"{OUT}: {len(rows)} librerie ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

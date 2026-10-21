"""Tutti i testi da tradurre: t() e N_() nel Python e nel JS, catalogo degli strumenti, testi fissi dell'HTML.

Uso a mano: `uv run python tests/i18n_keys.py` stampa le chiavi che mancano in locales/en.json.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "wmt_pdf"
STATIC = SRC / "gui" / "static"
EN = SRC / "locales" / "en.json"
JS_CALL = re.compile(r'\bt\(\s*"((?:[^"\\]|\\.)*)"')
ATTRS = ("title", "placeholder", "aria-label")


def python_keys() -> set[str]:
    keys = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "N_") \
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
    return keys


def catalog_keys() -> set[str]:
    from wmt_pdf.gui.tools import _TOOLS, CATEGORIES

    keys = {name for name, _ in CATEGORIES.values()}
    for tool in _TOOLS:
        keys |= {tool.label, tool.description}
        for opt in tool.options:
            keys.add(opt.label)
            keys |= set(opt.labels)
            if opt.help:
                keys.add(opt.help)
    return keys


def js_keys() -> set[str]:
    keys = set()
    for path in STATIC.glob("*.js"):
        for match in JS_CALL.finditer(path.read_text(encoding="utf-8")):
            keys.add(json.loads(f'"{match.group(1)}"'))
    for kinds in ("KIND_NAMES", "SIDE_NAMES"):  # mappe tradotte quando si usano
        for path in STATIC.glob("*.js"):
            found = re.search(kinds + r" = \{([^}]*)\}", path.read_text(encoding="utf-8"))
            if found:
                keys |= set(re.findall(r'"([^"]+)"', found.group(1))[1::2])
    return keys


class _Texts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys: set[str] = set()
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for name, value in attrs:
            if name in ATTRS and value and re.search(r"[A-Za-zÀ-ÿ]{2,}", value):
                self.keys.add(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        text = data.strip()
        if text and not self.skip and re.search(r"[A-Za-zÀ-ÿ]{2,}", text):
            self.keys.add(text)


def html_keys() -> set[str]:
    keys = set()
    for path in STATIC.glob("*.html"):
        parser = _Texts()
        parser.feed(path.read_text(encoding="utf-8"))
        keys |= parser.keys
    return keys - {"wmt-pdf", "PDF", "English", "Italiano"}


def all_keys() -> set[str]:
    return python_keys() | catalog_keys() | js_keys() | html_keys()


if __name__ == "__main__":
    have = json.loads(EN.read_text(encoding="utf-8")) if EN.exists() else {}
    missing = sorted(all_keys() - set(have))
    json.dump({k: "" for k in missing}, sys.stdout, ensure_ascii=False, indent=1)
    print(f"\n{len(missing)} da tradurre", file=sys.stderr)

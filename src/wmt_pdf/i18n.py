"""Lingua dei messaggi (CLI e interfaccia): italiano o inglese.

I testi si scrivono in italiano nel codice, dentro t(); la chiave della traduzione è il testo italiano stesso, con i
segnaposto di str.format: t("pagina {n} fuori dal documento", n=3). Le traduzioni inglesi stanno in
locales/en.json; un testo senza traduzione resta in italiano.

La lingua: --language della CLI, altrimenti la variabile WPDF_LANG, altrimenti quella del sistema (italiano se il
sistema è in italiano, inglese per tutto il resto).
"""

from __future__ import annotations

import json
import locale
import os
import sys
from functools import cache
from pathlib import Path

LANGUAGES = ("it", "en")
LOCALES = Path(__file__).parent / "locales"

_current: str | None = None


def system_language() -> str:
    candidates = [os.environ.get(name, "") for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF  # type: ignore[attr-defined]
            candidates.insert(0, "it" if primary == 0x10 else "en")  # 0x10 = LANG_ITALIAN
        except (AttributeError, OSError):
            pass
    for value in candidates:
        value = value.lower()
        if value:
            return "it" if value.startswith(("it", "italian")) else "en"
    return "it"


def normalize(value: str | None) -> str | None:
    value = (value or "").strip().lower()[:2]
    return value if value in LANGUAGES else None


def set_language(value: str | None) -> str:
    global _current
    _current = normalize(value) or normalize(os.environ.get("WPDF_LANG")) or system_language()
    return _current


def language() -> str:
    return _current or set_language(None)


@cache
def catalog(lang: str) -> dict[str, str]:
    if lang == "it":
        return {}
    path = LOCALES / f"{lang}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def t(text: str, /, **values) -> str:
    """Il testo nella lingua corrente, con i valori al posto dei segnaposto."""
    translated = catalog(language()).get(text, text)
    if not values:
        return translated
    try:
        return translated.format(**values)
    except (KeyError, IndexError, ValueError):
        return text.format(**values)  # traduzione sbagliata: meglio l'italiano che un errore


def take_language_option(argv: list[str]) -> list[str]:
    """Toglie --language X (o --language=X) dagli argomenti e imposta la lingua; il resto passa ad argparse."""
    rest: list[str] = []
    chosen: str | None = None
    skip = False
    for i, arg in enumerate(argv):
        if skip:
            skip = False
            continue
        if arg == "--":
            rest.extend(argv[i:])
            break
        if arg == "--language" and i + 1 < len(argv):
            chosen, skip = argv[i + 1], True
        elif arg.startswith("--language="):
            chosen = arg.split("=", 1)[1]
        else:
            rest.append(arg)
    set_language(chosen)
    return rest


def N_(text: str) -> str:
    """Segna un testo da tradurre più avanti, quando si mostra: qui resta com'è (serve all'estrazione)."""
    return text

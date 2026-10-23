"""Barra compatta: una finestretta sempre in primo piano, agganciata a uno dei quattro lati dello schermo.

La finestra principale si nasconde; la barra ha i comandi più usati e accetta file trascinati. «Espandi» (o la
chiusura della barra) riporta la finestra principale, eventualmente con un comando da eseguire (aprire un file,
uno strumento, i recenti).
"""

from __future__ import annotations

import json
from typing import Any

SIDES = ("top", "right", "bottom", "left")
# Quanto basta ai pulsanti (40 px l'uno), senza spazio vuoto: larghezza, altezza.
HORIZONTAL = (460, 54)  # in alto e in basso
VERTICAL = (56, 460)  # a sinistra e a destra
# pywebview impone 200x100 a ogni finestra se non si dice altro: la barra verticale diventava larga 200 px e, a
# destra, finiva per metà fuori dallo schermo (in basso lo stesso con l'altezza).
MIN_SIZE = (40, 40)


def size_for(side: str) -> tuple[int, int]:
    return HORIZONTAL if side in ("top", "bottom") else VERTICAL


def work_area(screen: Any) -> tuple[int, int, int, int]:
    """Area utile dello schermo (senza barra delle applicazioni) se il sistema la dà, altrimenti lo schermo intero."""
    frame: Any = getattr(screen, "frame", None)
    try:
        return int(frame.X), int(frame.Y), int(frame.Width), int(frame.Height)  # Windows: Screen.WorkingArea
    except (AttributeError, TypeError, ValueError):
        return int(screen.x), int(screen.y), int(screen.width), int(screen.height)


def parse_area(area: Any) -> tuple[int, int, int, int] | None:
    """Area utile mandata dalla pagina: {x, y, width, height} in pixel CSS; None se manca o non ha senso."""
    if not isinstance(area, dict):
        return None
    try:
        x, y, width, height = (int(float(area[k])) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (100 <= width <= 100_000 and 100 <= height <= 100_000 and abs(x) <= 100_000 and abs(y) <= 100_000):
        return None
    return x, y, width, height


def geometry(side: str, area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Posizione e dimensioni della barra: centrata sul lato scelto, mai più grande dell'area utile."""
    ax, ay, aw, ah = area
    width, height = size_for(side)
    width, height = min(width, aw), min(height, ah)
    if side in ("top", "bottom"):
        x = ax + (aw - width) // 2
        y = ay if side == "top" else ay + ah - height
    else:
        y = ay + (ah - height) // 2
        x = ax if side == "left" else ax + aw - width
    return x, y, width, height


def screen_of(window: Any, screens: list) -> Any:
    """Lo schermo dove sta la finestra (il centro), altrimenti il primo."""
    try:
        cx, cy = window.x + window.width // 2, window.y + window.height // 2
    except (AttributeError, TypeError):
        return screens[0]
    for screen in screens:
        if screen.x <= cx < screen.x + screen.width and screen.y <= cy < screen.y + screen.height:
            return screen
    return screens[0]


# Azioni che la barra può chiedere alla finestra principale: solo queste, solo valori semplici.
COMMANDS = ("open", "recent")


def clean_action(action: Any, tool_ids: set[str]) -> dict | None:
    if not isinstance(action, dict):
        return None
    result: dict[str, Any] = {}
    if action.get("command") in COMMANDS:
        result["command"] = action["command"]
    if isinstance(action.get("tool"), str) and action["tool"] in tool_ids:
        result["tool"] = action["tool"]
    if isinstance(action.get("open_file"), str) and action["open_file"].isalnum():
        result["open_file"] = action["open_file"]
    if isinstance(action.get("open_doc"), str) and action["open_doc"].isalnum():
        result["open_doc"] = action["open_doc"][:64]  # documento già aperto sul server (doppio clic su un PDF)
    if isinstance(action.get("notice"), str) and action["notice"].strip():
        result["notice"] = action["notice"][:300]  # messaggio da mostrare (finisce in textContent)
    files = action.get("files")
    if isinstance(files, list):
        result["files"] = []
        for f in files[:50]:
            if not isinstance(f, dict) or not str(f.get("id", "")).isalnum():
                continue
            size = f.get("size")
            result["files"].append({
                "id": str(f["id"])[:64], "name": str(f.get("name", ""))[:255],
                "size": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
                "kind": str(f.get("kind", ""))[:16],
            })
    return result or None


def run_action_js(action: dict) -> str:
    return f"window.wpdfExternal && window.wpdfExternal({json.dumps(action)})"

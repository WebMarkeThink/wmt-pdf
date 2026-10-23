"""Una sola finestra di wmt-pdf: un secondo avvio (es. doppio clic su un altro PDF) passa il file a quella aperta.

L'istanza aperta scrive porta e token della sessione in istanza.json, nella cache dell'utente (solo lui la legge,
e il token è quello che la pagina usa già). Il secondo avvio lo legge, chiede all'istanza di aprire il file con
POST /api/external e chiude; se l'istanza non risponde (file rimasto da una chiusura brusca) parte normalmente.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def _path() -> Path:
    from wmt_pdf.trust import cache_dir

    return cache_dir() / "istanza.json"


def register(port: int, token: str) -> None:
    target = _path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass  # senza il file un secondo avvio apre un'altra finestra: scomodo, non grave


def unregister(port: int) -> None:
    target = _path()
    try:
        if json.loads(target.read_text(encoding="utf-8")).get("port") == port:
            target.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def forward(path: str | None, timeout: float = 3.0) -> bool:
    """Vero se un'istanza aperta ha preso il file (o, senza file, è solo venuta in primo piano)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        port, token = int(data["port"]), str(data["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not 0 < port < 65536:
        return False
    body = json.dumps({"path": str(Path(path).resolve()) if path else None}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/external", data=body, method="POST",
                                     headers={"X-Wpdf-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - istanza chiusa o bloccata: si apre una finestra nuova
        return False

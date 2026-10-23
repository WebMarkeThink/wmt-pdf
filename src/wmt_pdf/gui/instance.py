"""Una sola finestra di wmt-pdf: un secondo avvio (es. doppio clic su un altro PDF) passa il file a quella aperta.

L'istanza aperta scrive porta, token della sessione e pid in istanza.json, nella cache dell'utente (solo lui la
legge, e il token è quello che la pagina usa già). Il secondo avvio lo legge, chiede all'istanza di aprire il file con
POST /api/external e chiude; se l'istanza non c'è più (file rimasto da una chiusura brusca) parte normalmente.

Più file aperti insieme (selezione multipla + Invio) fanno partire più processi nello stesso istante: il primo prende
un lucchetto di avvio e apre la finestra, gli altri aspettano che sia pronta e le passano il loro file.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

STARTUP_STALE = 60  # secondi: un lucchetto più vecchio è di un avvio fallito


def _dir() -> Path:
    from wmt_pdf.trust import cache_dir

    return cache_dir()


def _path() -> Path:
    return _dir() / "istanza.json"


def _lock() -> Path:
    return _dir() / "istanza.avvio"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # su Windows no: lì 0 è CTRL_C_EVENT
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def forward(path: str | None, timeout: float = 5.0) -> bool:
    """Vero se un'istanza aperta ha preso il file (o, senza file, è solo venuta in primo piano)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        port, token, pid = int(data["port"]), str(data["token"]), int(data.get("pid", 0))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    if not 0 < port < 65536 or not _alive(pid):
        return False  # istanza chiusa male: la sua porta potrebbe essere di qualcun altro
    if sys.platform == "win32":
        with contextlib.suppress(Exception):  # chi ha ricevuto il doppio clic può cedere il primo piano all'istanza
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(pid)  # type: ignore[attr-defined]
    # abspath, non resolve: nessun accesso al disco (o alla rete) prima dei controlli dell'istanza.
    body = json.dumps({"path": os.path.abspath(path) if path else None}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/external", data=body, method="POST",
                                     headers={"X-Wpdf-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status in (200, 202)
    except Exception:  # noqa: BLE001 - istanza chiusa o bloccata: si apre una finestra nuova
        return False


def claim_startup() -> bool:
    """Vero per il primo di più avvii contemporanei: è lui ad aprire la finestra."""
    lock = _lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > STARTUP_STALE
            except OSError:
                stale = True
            if not stale:
                return False
            with contextlib.suppress(OSError):
                lock.unlink()  # avvio fallito senza togliere il lucchetto
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    return False


def release_startup() -> None:
    lock = _lock()
    with contextlib.suppress(OSError, ValueError):
        if int(lock.read_text().strip() or 0) == os.getpid():
            lock.unlink()


def wait_and_forward(path: str | None, seconds: float = 20.0) -> bool:
    """Per gli avvii contemporanei arrivati dopo il primo: aspetta la sua finestra e le passa il file."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if forward(path):
            return True
        if not _lock().exists() and not _path().exists():
            return False  # il primo avvio è fallito: si apre da soli
        time.sleep(0.3)
    return False

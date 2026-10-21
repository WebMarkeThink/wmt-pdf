"""Elenco europeo dei certificatori fidati (EU LOTL e liste nazionali), in cache locale.

`wpdf trust update` scarica e verifica (firma XML) la lista delle liste e le liste nazionali;
la verifica delle firme usa poi la copia locale, anche senza rete.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

from pyhanko_certvalidator.registry import SimpleTrustManager, TrustManager

from wmt_pdf.i18n import t
from wmt_pdf.source import CliError

CACHE_ENV = "WPDF_CACHE_DIR"
STALE_AFTER = dt.timedelta(days=30)


def cache_dir() -> Path:
    if os.environ.get(CACHE_ENV):
        return Path(os.environ[CACHE_ENV])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "wmt-pdf"


def _root() -> Path:
    return cache_dir() / "eutl"


class OfflineMiss(Exception):
    """Una lista manca dalla copia locale e non si può scaricare."""


class ExactFileCache:
    """Copia locale delle liste, byte per byte.

    La cache di pyHanko scrive in modalità testo: su Windows gli a capo cambiano, la firma XML
    della lista non torna più e la lista viene scartata. Qui si scrive e si legge senza conversioni.
    Con strict=True una lista mancante non si scarica: si segnala (modalità offline).
    """

    NAME = re.compile(r"[0-9a-f]{64}")

    def __init__(self, root: Path, strict: bool = False):
        self.root = root
        self.strict = strict
        root.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "index.json"
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {}
        # Solo nomi prodotti da noi: un indice manomesso non deve far leggere file fuori dalla cartella.
        self.index: dict[str, str] = {k: v for k, v in index.items()
                                      if isinstance(v, str) and self.NAME.fullmatch(v)}

    def __getitem__(self, key: str) -> str:
        try:
            with (self.root / self.index[key]).open("r", encoding="utf-8", newline="") as f:
                return f.read()
        except (KeyError, OSError):
            if self.strict:
                raise OfflineMiss(key) from None
            raise KeyError(key) from None

    def __setitem__(self, key: str, value: str) -> None:
        name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with (self.root / name).open("w", encoding="utf-8", newline="") as f:
            f.write(value)
        self.index[key] = name
        self.index_path.write_text(json.dumps(self.index), encoding="utf-8")


def _meta_path() -> Path:
    return _root() / "meta.json"


def status() -> dict | None:
    try:
        return json.loads(_meta_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def require_extra() -> None:
    import importlib.util

    for module in ("aiohttp", "signxml", "xsdata"):
        if importlib.util.find_spec(module) is None:
            raise CliError(t("firma e verifica avanzata non installate: `uv sync --extra firma` (oppure pip install 'wmt-pdf[firma]')"))


def update(territories: set[str] | None = None) -> dict:
    """Scarica l'elenco; territories limita alle nazioni indicate (codici ISO, es. IT)."""
    require_extra()
    root = _root()
    # Si scarica in una cartella nuova e la si sostituisce solo a download riuscito:
    # un aggiornamento fallito non deve lasciare senza la copia che funzionava.
    staging = root / "xml.nuovo"
    shutil.rmtree(staging, ignore_errors=True)
    cache = ExactFileCache(staging)
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)  # voci non interpretate: finiscono in meta.json
    try:
        registry, errors = asyncio.run(_build(cache, territories, offline=False))
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    current = root / "xml"
    shutil.rmtree(current, ignore_errors=True)
    os.replace(staging, current)
    meta = {
        "updated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "territories": sorted(t.upper() for t in territories) if territories else "tutte",
        "authorities": sum(1 for _ in registry.known_certificate_authorities),
        "errors": [str(e)[:200] for e in errors][:20],
    }
    root.mkdir(parents=True, exist_ok=True)
    _meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


async def _build(cache, territories, offline: bool):
    import aiohttp
    from pyhanko.sign.validation.qualified.eutl_fetch import (
        fetch_lotl,
        lotl_to_registry,
    )

    timeout = aiohttp.ClientTimeout(total=120, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        try:
            lotl = await fetch_lotl(client, cache)
            return await lotl_to_registry(lotl, client, cache=cache, only_territories=territories)
        except aiohttp.ClientError as exc:
            hint = t("nessuna copia locale: serve la rete") if offline else t("rete non raggiungibile")
            raise CliError(t("elenco europeo non disponibile ({hint}): {exc}", hint=hint, exc=exc)) from None


def load_registry(territories: set[str]):
    """Registro dei certificatori per le nazioni indicate, dalla copia locale; None se non c'è."""
    meta = status()
    if meta is None:
        return None
    require_extra()
    available = meta["territories"]
    if available != "tutte":
        territories = {t for t in territories if t.upper() in available}
    if not territories:
        return None
    cache = ExactFileCache(_root() / "xml", strict=True)
    incomplete = CliError(t("copia locale dell'elenco europeo incompleta: `wpdf trust update`"))
    try:
        registry, errors = asyncio.run(_build(cache, territories, offline=True))
    except OfflineMiss:
        raise incomplete from None
    # pyHanko raccoglie gli errori lista per lista: una lista mancante finisce lì, non come eccezione.
    if any(isinstance(e, OfflineMiss) or isinstance(getattr(e, "__cause__", None), OfflineMiss) for e in errors):
        raise incomplete
    return registry


def is_stale(meta: dict) -> bool:
    try:
        updated = dt.datetime.fromisoformat(meta["updated"])
    except (KeyError, ValueError):
        return True
    return dt.datetime.now(dt.UTC) - updated > STALE_AFTER


class CombinedTrustManager(TrustManager):
    """Fiducia nell'elenco europeo e/o in radici indicate dall'utente (per PKI private e prove)."""

    def __init__(self, registry=None, roots=()):
        from pyhanko.sign.validation.qualified.tsp import TSPTrustManager

        self.eu = TSPTrustManager(registry) if registry is not None else None
        self.custom = SimpleTrustManager.build(trust_roots=list(roots))
        self.custom_roots = {r.dump() for r in roots}

    def _managers(self) -> list[TrustManager]:
        return [m for m in (self.custom, self.eu) if m is not None]

    def is_root(self, cert) -> bool:
        return any(m.is_root(cert) for m in self._managers())

    def as_trust_anchor(self, authority):
        for manager in self._managers():
            anchor = manager.as_trust_anchor(authority)
            if anchor is not None:
                return anchor
        return None

    def find_potential_issuers(self, cert) -> Iterator:
        for manager in self._managers():
            yield from manager.find_potential_issuers(cert)


def load_roots(paths: list[str]) -> list:
    """Certificati radice da file PEM o DER (anche più certificati in un PEM)."""
    from asn1crypto import pem, x509

    roots = []
    for name in paths:
        path = Path(name)
        if not path.is_file():
            raise CliError(t("certificato radice non trovato: {path}", path=path))
        raw = path.read_bytes()
        try:
            if pem.detect(raw):
                roots += [x509.Certificate.load(der) for _, _, der in pem.unarmor(raw, multiple=True)]
            else:
                roots.append(x509.Certificate.load(raw))
            _ = [r.subject.native for r in roots]  # forza la lettura: errori qui, non durante la verifica
        except (ValueError, TypeError) as exc:
            raise CliError(t("{name}: certificato non leggibile ({exc})", name=path.name, exc=exc)) from None
    return roots

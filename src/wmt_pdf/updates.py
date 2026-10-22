"""Controllo degli aggiornamenti: l'ultima versione pubblicata tra le release di GitHub.

Si chiede a GitHub l'elenco delle release (repository pubblico, senza account) e si confronta con la versione
installata. Chi usa una beta vede anche le beta; chi usa una versione normale solo le versioni normali.
La risposta resta valida un giorno (cache dell'utente): l'interfaccia non interroga GitHub a ogni avvio.
Si disattiva con l'impostazione dell'interfaccia o con WPDF_NO_UPDATE_CHECK=1; `wpdf update` controlla sempre.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote

from wmt_pdf import __version__
from wmt_pdf.i18n import t
from wmt_pdf.source import CliError

REPO = "WebMarkeThink/wmt-pdf"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES = f"https://github.com/{REPO}/releases"
DISABLE_ENV = "WPDF_NO_UPDATE_CHECK"
CACHE_HOURS = 24
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|alpha|b|beta|rc)[-.]?(\d+))?$", re.IGNORECASE)
_STAGES = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}
_RELEASE_URL = re.compile(re.escape(RELEASES) + r"/tag/v?\d+\.\d+\.\d+(?:[-.]?(?:a|alpha|b|beta|rc)[-.]?\d+)?",
                          re.IGNORECASE)


def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """0.1.0b1, 0.1.0-beta.1 e v0.1.0-beta.1 sono la stessa versione; una versione normale viene dopo le sue beta."""
    match = _VERSION.match(text.strip())
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    rank = _STAGES[stage.lower()] if stage else 3
    return int(major), int(minor), int(patch), rank, int(number or 0)


def is_prerelease(text: str) -> bool:
    parsed = parse_version(text)
    return parsed is not None and parsed[3] < 3


@dataclass
class UpdateInfo:
    current: str
    latest: str | None = None  # tag della release più recente adatta (es. v0.1.0-beta.2)
    available: bool = False
    url: str = RELEASES
    how: str = ""  # come aggiornare: installatore o comando di uv
    checked_at: str = ""


def disabled_by_env() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in ("1", "true", "yes", "si", "sì")


def _cache_file():
    from wmt_pdf.trust import cache_dir

    return cache_dir() / "aggiornamenti.json"


MAX_RESPONSE = 2_000_000  # l'elenco delle release sta in poche decine di KB


def release_url(tag: str) -> str:
    """Link alla release ricostruito dal tag (mai preso da fuori): solo pagine del repository."""
    return f"{RELEASES}/tag/{quote(tag, safe='')}"


def is_release_url(url: object) -> bool:
    """La pagina delle release o quella di una versione: niente altro si apre (né «..», né altri siti o schemi)."""
    return isinstance(url, str) and (url == RELEASES or bool(_RELEASE_URL.fullmatch(url)))


def _fetch(timeout: float) -> list[dict]:
    request = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"wmt-pdf/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError(t("risposta troppo grande"))
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - rete, HTTP, JSON: per chi chiede è sempre «controllo non riuscito»
        raise CliError(t("controllo degli aggiornamenti non riuscito: {reason}", reason=exc)) from None
    return data if isinstance(data, list) else []


def _how(tag: str) -> str:
    if getattr(sys, "frozen", False):  # installatore (PyInstaller)
        return t("scarica l'installatore dalla pagina della release e installalo sopra quello attuale")
    return f'uv tool install --force "wmt-pdf[ocr,convert,firma] @ git+https://github.com/{REPO}@{tag}"'


def newest(releases: list[dict], current: str) -> dict | None:
    """La release più recente che fa per chi usa `current`: niente bozze, beta solo per chi è già in beta."""
    want_pre = is_prerelease(current)
    best, best_version = None, None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        version = parse_version(str(release.get("tag_name", "")))
        pre = version is not None and (version[3] < 3 or bool(release.get("prerelease")))  # tag o segno di GitHub
        if version is None or (pre and not want_pre):
            continue
        if best_version is None or version > best_version:
            best, best_version = release, version
    return best


def _info(current: str, tag: str | None, checked_at: str) -> UpdateInfo:
    """Esito costruito solo da valori controllati: tag che sia una versione, link e «disponibile» ricalcolati."""
    info = UpdateInfo(current=current, checked_at=checked_at)
    mine, theirs = parse_version(current), parse_version(tag) if isinstance(tag, str) else None
    if theirs is not None:
        info.latest = tag
        info.url = release_url(tag)  # type: ignore[arg-type]
        info.available = mine is not None and theirs > mine
        info.how = _how(tag) if info.available else ""  # type: ignore[arg-type]  # nella lingua di chi legge
    return info


def _from_cache(path, current: str, now: dt.datetime) -> UpdateInfo | None:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        checked = dt.datetime.fromisoformat(cached["checked_at"])
        age = now - checked
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if cached.get("current") != current or not dt.timedelta(0) <= age < dt.timedelta(hours=CACHE_HOURS):
        return None  # altra versione installata, scaduta o con una data nel futuro
    return _info(current, cached.get("latest"), checked.isoformat(timespec="seconds"))


def _save(path, info: UpdateInfo) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"current": info.current, "latest": info.latest, "checked_at": info.checked_at},
                                  indent=1), encoding="utf-8")
        os.replace(tmp, path)  # in un colpo: chi legge non trova mai un file a metà
    except OSError:
        pass  # senza cache si ricontrolla la prossima volta


def check(*, use_cache: bool = True, timeout: float = 5.0, current: str = __version__) -> UpdateInfo:
    now = dt.datetime.now(dt.UTC)
    cache = _cache_file()
    if use_cache:
        cached = _from_cache(cache, current, now)
        if cached is not None:
            return cached
    release = newest(_fetch(timeout), current)
    info = _info(current, str(release["tag_name"]) if release else None, now.isoformat(timespec="seconds"))
    _save(cache, info)
    return info

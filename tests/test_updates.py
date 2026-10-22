"""Controllo degli aggiornamenti: versioni, scelta della release, cache, CLI e interfaccia (GitHub simulato)."""

import json

import pytest

from wmt_pdf import updates
from wmt_pdf.cli import main

RELEASES = [
    {"tag_name": "v0.2.0", "draft": True, "html_url": "https://github.com/WebMarkeThink/wmt-pdf/releases/tag/v0.2.0"},
    {"tag_name": "v0.1.0-beta.2", "prerelease": True,
     "html_url": "https://github.com/WebMarkeThink/wmt-pdf/releases/tag/v0.1.0-beta.2"},
    {"tag_name": "v0.1.0-beta.1", "prerelease": True, "html_url": "https://esempio.invalid/altro"},
    {"tag_name": "non-una-versione"},
]


@pytest.fixture
def github(monkeypatch, tmp_path):
    """GitHub finto e cache in una cartella del test; conta le richieste."""
    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path / "cache"))
    calls = []

    def fake_fetch(timeout):
        calls.append(timeout)
        return calls_data[0]

    calls_data = [RELEASES]
    monkeypatch.setattr(updates, "_fetch", fake_fetch)
    return calls, calls_data


@pytest.mark.parametrize("a, b", [
    ("0.1.0b1", "v0.1.0-beta.2"),
    ("0.1.0-beta.2", "0.1.0rc1"),
    ("0.1.0rc1", "0.1.0"),
    ("0.1.0", "v0.1.1-alpha.1"),
    ("0.9.0", "0.10.0"),
])
def test_version_order(a, b):
    assert updates.parse_version(a) < updates.parse_version(b)
    assert updates.parse_version("0.1.0b1") == updates.parse_version("v0.1.0-beta.1")
    assert updates.parse_version("latest") is None


def test_beta_users_see_betas_others_only_releases():
    assert updates.newest(RELEASES, "0.1.0b1")["tag_name"] == "v0.1.0-beta.2"  # niente bozze
    assert updates.newest(RELEASES, "0.0.9") is None  # versione normale: le beta non contano


def test_check_finds_update_and_caches(github):
    calls, _ = github
    info = updates.check(current="0.1.0b1")
    assert info.available and info.latest == "v0.1.0-beta.2"
    assert info.url.endswith("/releases/tag/v0.1.0-beta.2") and "uv tool install --force" in info.how
    assert updates.check(current="0.1.0b1").available and len(calls) == 1  # dalla cache
    updates.check(current="0.1.0b1", use_cache=False)
    assert len(calls) == 2


def test_link_only_from_the_repository(github):
    _, data = github
    data[0] = [{"tag_name": "v9.0.0", "html_url": "https://esempio.invalid/scaricami"}]
    info = updates.check(current="0.1.0", use_cache=False)
    assert info.url == "https://github.com/WebMarkeThink/wmt-pdf/releases/tag/v9.0.0"


def test_up_to_date(github):
    info = updates.check(current="0.1.0b2")
    assert not info.available and info.latest == "v0.1.0-beta.2" and info.how == ""


def test_cli_update(capsys, github):
    assert main(["update"]) == 0
    out = capsys.readouterr().out
    assert "0.1.0" in out
    assert main(["--language", "en", "update", "--json"]) == 0
    assert set(json.loads(capsys.readouterr().out)) >= {"current", "latest", "available", "url"}


def test_cli_update_offline(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path))

    def offline(timeout):
        raise updates.CliError("controllo degli aggiornamenti non riuscito: rete assente")

    monkeypatch.setattr(updates, "_fetch", offline)
    assert main(["update"]) == 1
    assert "non riuscito" in capsys.readouterr().err


def test_gui_respects_setting_and_env(github, monkeypatch):
    from wmt_pdf.gui.documents import save_settings, settings
    from wmt_pdf.gui.server import update_status

    calls, _ = github
    assert settings()["update_check"] is True
    assert update_status()["enabled"] is True and len(calls) == 1
    save_settings({"update_check": False})
    assert update_status() == {"enabled": False}
    save_settings({"update_check": True})
    monkeypatch.setenv("WPDF_NO_UPDATE_CHECK", "1")
    assert update_status() == {"enabled": False}
    assert len(calls) == 1  # disattivato: nessuna richiesta a GitHub
    with pytest.raises(ValueError):
        save_settings({"update_check": "sì"})


def test_open_url_only_for_releases(monkeypatch):
    import webbrowser

    from wmt_pdf.gui.server import DesktopApi

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    api = DesktopApi(server=None)
    assert api.open_url("https://github.com/WebMarkeThink/wmt-pdf/releases/tag/v1.0.0")
    assert not api.open_url("https://esempio.invalid/") and not api.open_url("javascript:alert(1)")
    assert not api.open_url("https://github.com/WebMarkeThink/wmt-pdf/releases.evil.example")
    assert opened == ["https://github.com/WebMarkeThink/wmt-pdf/releases/tag/v1.0.0"]


@pytest.mark.parametrize("cached", [
    {"current": "0.1.0b1", "latest": 'x" & calc & "', "checked_at": "2026-01-01T00:00:00+00:00"},
    {"current": "0.1.0b1", "latest": "v0.1.0-beta.9", "url": "javascript:alert(1)", "available": "sì"},
    {"current": "0.1.0b1", "latest": "v0.1.0-beta.9", "checked_at": "2999-01-01T00:00:00+00:00"},
    "non un oggetto",
])
def test_tampered_cache_is_not_trusted(github, cached):
    import datetime as dt

    calls, _ = github
    path = updates._cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(cached, dict) and "checked_at" not in cached:
        cached["checked_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    path.write_text(json.dumps(cached), encoding="utf-8")
    info = updates.check(current="0.1.0b1")
    assert updates.is_release_url(info.url)
    assert info.latest is None or updates.parse_version(info.latest) is not None
    assert '"' not in info.how.split("@", 1)[-1].rstrip('"')  # niente da iniettare nel comando di uv
    assert isinstance(info.available, bool)
    if isinstance(cached, dict) and cached.get("latest") == "v0.1.0-beta.9" and "2999" not in cached["checked_at"]:
        assert info.url.endswith("/tag/v0.1.0-beta.9") and not calls  # dalla cache, ma con il link ricostruito
    else:
        assert calls  # cache scartata: si richiede a GitHub


def test_unusual_network_errors_become_a_message(monkeypatch, tmp_path):
    import http.client
    import urllib.request

    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path))

    def broken(*args, **kwargs):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    with pytest.raises(updates.CliError, match="non riuscito"):
        updates.check(use_cache=False)


def test_huge_response_is_refused(monkeypatch, tmp_path):
    import io
    import urllib.request

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(b"[" + b" " * 3_000_000 + b"]"))
    with pytest.raises(updates.CliError):
        updates._fetch(5)


def test_github_prerelease_flag_counts():
    releases = [{"tag_name": "v0.3.0", "prerelease": True}, {"tag_name": "v0.2.0"}]
    assert updates.newest(releases, "0.1.0")["tag_name"] == "v0.2.0"
    assert updates.newest(releases, "0.1.0b1")["tag_name"] == "v0.3.0"


@pytest.mark.parametrize("url", [
    "https://github.com/WebMarkeThink/wmt-pdf/releases/../../../attacker/repo/releases",
    "https://github.com/WebMarkeThink/wmt-pdf/releases/tag/%2e%2e/%2e%2e/x",
    "https://github.com/WebMarkeThink/wmt-pdf/releases/tag/v1.0.0?x=1",
    "http://github.com/WebMarkeThink/wmt-pdf/releases",
])
def test_release_url_allowlist(url):
    assert not updates.is_release_url(url)

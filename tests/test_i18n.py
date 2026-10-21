"""Lingue: traduzioni complete, messaggi sempre traducibili, CLI e interfaccia in inglese."""

import ast
import json
import string
import threading
import urllib.request

import pytest
from i18n_keys import EN, SRC, all_keys

from wmt_pdf.cli import main
from wmt_pdf.i18n import set_language, t, take_language_option


def english() -> dict:
    return json.loads(EN.read_text(encoding="utf-8"))


def test_every_text_has_an_english_translation():
    keys, have = all_keys(), english()
    assert sorted(keys - set(have)) == []  # nuovi testi: tests/i18n_keys.py li elenca
    assert sorted(set(have) - keys) == []  # traduzioni di testi che non esistono più
    assert all(value.strip() for value in have.values())


def _fields(text: str) -> set[str] | None:
    try:
        return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}
    except ValueError:
        return None


def test_placeholders_match():
    for key, value in english().items():
        if _fields(key) is not None and '{"' not in key:
            assert _fields(key) == _fields(value), key


def test_errors_are_always_translatable():
    """Un messaggio d'errore scritto senza t() resterebbe in italiano anche in inglese."""
    found = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("CliError", "ValueError", "P7mError") and node.args:
                arg = node.args[0]
                if isinstance(arg, (ast.JoinedStr, ast.Constant)) and isinstance(getattr(arg, "value", ""), str):
                    found.append(f"{path.name}:{node.lineno}")
    assert found == []


@pytest.mark.parametrize("argv, expected", [
    (["--language", "en", "info", "x.pdf"], ("en", ["info", "x.pdf"])),
    (["info", "--language=it", "x.pdf"], ("it", ["info", "x.pdf"])),
    (["info", "--", "--language", "x"], ("it", ["info", "--", "--language", "x"])),
])
def test_language_option(argv, expected):
    from wmt_pdf.i18n import language

    rest = take_language_option(argv)
    assert (language(), rest) == expected


def test_t_formats_and_falls_back():
    set_language("en")
    assert t("pagina {n} fuori dal documento (1-{count})", n=5, count=3) == "page 5 outside the document (1-3)"
    assert t("testo mai tradotto {x}", x=1) == "testo mai tradotto 1"
    set_language("it")
    assert t("pagina {n} fuori dal documento (1-{count})", n=5, count=3) == "pagina 5 fuori dal documento (1-3)"


def test_cli_in_english(capsys, tmp_path, monkeypatch):
    code = main(["--language", "en", "info", str(tmp_path / "manca.pdf")])
    assert code != 0 and "file not found" in capsys.readouterr().err
    monkeypatch.setenv("WPDF_LANG", "en")
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "Reads PDF and p7m files as compact text" in out and "--language" in out


def test_cli_english_output(capsys, text_pdf):
    assert main(["--language=en", "search", str(text_pdf), "zzzzzz"]) == 0
    assert "0 results" in capsys.readouterr().out


@pytest.fixture
def server(tmp_path):
    from wmt_pdf.gui.server import make_server

    srv = make_server(tmp_path / "lavoro", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _call(srv, path, body=None):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("X-Wpdf-Token", srv.token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw.startswith(("{", "[")) else raw


def test_gui_language_setting(server, text_pdf):
    assert _call(server, "/api/settings") == {"language": "it"}
    assert _call(server, "/api/i18n") == {}
    assert _call(server, "/api/settings", {"language": "en"}) == {"language": "en"}
    merge = next(tool for tool in _call(server, "/api/tools") if tool["id"] == "merge")
    assert merge["label"] == "Merge" and merge["category_label"] == "Organise"
    assert _call(server, "/api/i18n")["Apri"] == "Open"
    assert '<html lang="en">' in _call(server, "/")
    # I comandi eseguiti dalla GUI rispondono nella sua lingua.
    file_id = server.register(text_pdf)["id"]
    with pytest.raises(ValueError, match="Degrees: unexpected value"):
        server.execute("rotate", [file_id], {"angle": "45"})
    assert "Pages: 3" in server.execute("info", [file_id], {})["stdout"]
    _call(server, "/api/settings", {"language": "it"})
    assert '<html lang="it">' in _call(server, "/")


def test_gui_rejects_unknown_language(server):
    req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/settings",
                                 data=b'{"language": "xx"}', method="POST")
    req.add_header("X-Wpdf-Token", server.token)
    req.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    err.value.close()  # aperta, finirebbe nel garbage collector con un ResourceWarning in un punto qualsiasi
    assert err.value.code == 400

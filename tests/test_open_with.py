"""Aprire un PDF dal sistema: `wpdf gui FILE`, doppio clic con wmt-pdf già aperto (una sola finestra)."""

import json
import threading
import urllib.request

import pytest

from wmt_pdf.cli import main
from wmt_pdf.gui import instance
from wmt_pdf.gui.compact import clean_action


@pytest.fixture
def server(tmp_path, monkeypatch):
    from wmt_pdf.gui.server import make_server

    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path / "cache"))
    srv = make_server(tmp_path / "lavoro", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


class FakeApi:
    def __init__(self):
        self.actions = []

    def _show_external(self, action):
        self.actions.append(action)


def post_external(srv, body):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/external",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Wpdf-Token": srv.token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code


def test_external_opens_document_in_the_window(server, text_pdf):
    server.desktop, server.desktop_api = True, FakeApi()
    assert post_external(server, {"path": str(text_pdf)}) == 200
    action = server.desktop_api.actions[-1]
    doc = server.documents.get(action["open_doc"])
    assert doc.path == text_pdf.resolve() or doc.path == text_pdf
    assert post_external(server, {"path": None}) == 200 and server.desktop_api.actions[-1] is None  # solo in primo piano


def test_external_bad_file_becomes_a_notice(server, tmp_path):
    server.desktop, server.desktop_api = True, FakeApi()
    not_pdf = tmp_path / "nota.pdf"
    not_pdf.write_text("non sono un PDF")
    assert post_external(server, {"path": str(not_pdf)}) == 200
    assert "PDF" in server.desktop_api.actions[-1]["notice"]
    assert post_external(server, {"path": str(tmp_path / "manca.pdf")}) == 200
    assert "notice" in server.desktop_api.actions[-1]


def test_external_only_in_the_native_window(server, text_pdf):
    assert post_external(server, {"path": str(text_pdf)}) == 400  # nel browser no
    server.desktop, server.desktop_api = True, FakeApi()
    assert post_external(server, {"path": 42}) == 400


def test_forward_to_running_instance(server, text_pdf):
    server.desktop, server.desktop_api = True, FakeApi()
    assert not instance.forward(str(text_pdf))  # nessuna istanza registrata
    instance.register(server.server_address[1], server.token)
    assert instance.forward(str(text_pdf))
    assert "open_doc" in server.desktop_api.actions[-1]
    instance.unregister(server.server_address[1] + 1)  # di un'altra istanza: resta
    assert instance.forward(None)
    instance.unregister(server.server_address[1])
    assert not instance.forward(None)


def test_forward_with_stale_file_starts_normally(tmp_path, monkeypatch):
    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path))
    instance.register(1, "token-vecchio")  # istanza chiusa male: la porta non risponde
    assert not instance.forward(None, timeout=0.5)
    (tmp_path / "istanza.json").write_text("rovinato")
    assert not instance.forward(None)


def test_gui_command_hands_file_to_open_window(monkeypatch, text_pdf):
    calls = []
    monkeypatch.setattr(instance, "forward", lambda path: calls.append(path) or True)
    import wmt_pdf.gui.server as server_module

    monkeypatch.setattr(server_module, "serve", lambda *a, **k: pytest.fail("non deve aprire un'altra finestra"))
    assert main(["gui", str(text_pdf)]) == 0
    assert calls == [str(text_pdf)]


def test_gui_command_opens_file_when_alone(monkeypatch, text_pdf):
    seen = {}
    monkeypatch.setattr(instance, "forward", lambda path: False)
    import wmt_pdf.gui.server as server_module

    monkeypatch.setattr(server_module, "serve", lambda workdir, port, open_window, initial: seen.update(initial=initial))
    assert main(["gui", "--", str(text_pdf)]) == 0
    assert seen == {"initial": str(text_pdf)}


def test_actions_towards_the_page():
    assert clean_action({"open_doc": "abc123"}, set()) == {"open_doc": "abc123"}
    assert clean_action({"open_doc": "../x"}, set()) is None
    assert clean_action({"notice": "x" * 1000}, set()) == {"notice": "x" * 300}

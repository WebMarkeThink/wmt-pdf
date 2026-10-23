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
        self.event = threading.Event()

    def _show_external(self, action):
        self.actions.append(action)
        self.event.set()

    def wait(self):
        """L'apertura avviene dopo la risposta (202): si aspetta che arrivi alla finestra."""
        assert self.event.wait(10), "nessuna azione arrivata alla finestra"
        self.event.clear()
        return self.actions[-1]


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
    assert post_external(server, {"path": str(text_pdf)}) == 202
    action = server.desktop_api.wait()
    doc = server.documents.get(action["open_doc"])
    assert doc.path == text_pdf.resolve() or doc.path == text_pdf
    assert post_external(server, {"path": None}) == 202 and server.desktop_api.wait() is None  # solo in primo piano


def test_external_bad_file_becomes_a_notice(server, tmp_path):
    server.desktop, server.desktop_api = True, FakeApi()
    not_pdf = tmp_path / "nota.pdf"
    not_pdf.write_text("non sono un PDF")
    assert post_external(server, {"path": str(not_pdf)}) == 202
    assert "PDF" in server.desktop_api.wait()["notice"]
    assert post_external(server, {"path": str(tmp_path / "manca.pdf")}) == 202
    assert "notice" in server.desktop_api.wait()
    bat = tmp_path / "script.bat"
    bat.write_text("echo")
    assert post_external(server, {"path": str(bat)}) == 202
    assert "notice" in server.desktop_api.wait()
    assert not server.documents._is_allowed(bat)  # scartato prima di entrare tra i percorsi ammessi


def test_external_only_in_the_native_window(server, text_pdf):
    assert post_external(server, {"path": str(text_pdf)}) == 400  # nel browser no
    server.desktop, server.desktop_api = True, FakeApi()
    assert post_external(server, {"path": 42}) == 400


def test_forward_to_running_instance(server, text_pdf):
    server.desktop, server.desktop_api = True, FakeApi()
    assert not instance.forward(str(text_pdf))  # nessuna istanza registrata
    instance.register(server.server_address[1], server.token)
    assert instance.forward(str(text_pdf))
    assert "open_doc" in server.desktop_api.wait()
    instance.unregister(server.server_address[1] + 1)  # di un'altra istanza: resta
    assert instance.forward(None)
    instance.unregister(server.server_address[1])
    assert not instance.forward(None)


def test_forward_with_stale_file_starts_normally(tmp_path, monkeypatch):
    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path))
    instance.register(1, "token-vecchio")  # istanza chiusa male: la porta non risponde
    assert not instance.forward(None, timeout=0.5)
    (tmp_path / "istanza.json").write_text(json.dumps({"port": 8765, "token": "x", "pid": 999_999_999}))
    assert not instance.forward(None)  # processo che non c'è più: la porta potrebbe essere di altri
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


@pytest.mark.parametrize("raw", [
    "\\\\server\\cartella\\x.pdf", "//server/cartella/x.pdf", "\\/server/cartella/x.pdf",
    "/\\server\\cartella\\x.pdf",
])
def test_network_paths_refused(raw):
    import sys

    from wmt_pdf.gui.documents import _is_network, check_local_path

    if sys.platform != "win32" and "\\" in raw.replace("\\\\", ""):
        pytest.skip("barre rovesciate mescolate: solo Windows")
    assert _is_network(raw)
    with pytest.raises(ValueError):
        check_local_path(raw, for_open=True)


def test_startup_lock(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path))
    assert instance.claim_startup()
    assert not instance.claim_startup()  # un altro avvio contemporaneo aspetta
    instance.release_startup()
    assert instance.claim_startup()
    old = time.time() - instance.STARTUP_STALE - 5
    os.utime(tmp_path / "istanza.avvio", (old, old))  # lucchetto di un avvio fallito
    assert instance.claim_startup()
    instance.release_startup()
    assert not instance.wait_and_forward(None, seconds=1)  # nessuno sta aprendo: si apre da soli


def test_bring_front_keeps_maximized_window():
    from wmt_pdf.gui.server import DesktopApi

    class Window:
        def __init__(self):
            self.calls = []
            self.on_top = False

        def __getattr__(self, name):
            return lambda *a: self.calls.append(name)

    api = DesktopApi(server=None)
    api._window = Window()
    api._bring_front()
    assert "restore" not in api._window.calls and "show" in api._window.calls  # ingrandita: resta com'è
    assert api._window.on_top is False  # niente «sempre sopra» (bloccava la finestra)
    api._minimized = True
    api._bring_front()
    assert "restore" in api._window.calls

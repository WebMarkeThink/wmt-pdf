"""Barra compatta: posizione sui quattro lati e passaggio tra barra e finestra completa."""

import sys
import threading
import types
import urllib.request

import pytest

from wmt_pdf.gui.compact import (
    clean_action,
    geometry,
    run_action_js,
    screen_of,
    work_area,
)

AREA = (0, 0, 1920, 1040)  # schermo 1920x1080 con la barra delle applicazioni in basso


@pytest.mark.parametrize("side, expected", [
    ("top", (660, 0, 600, 56)),
    ("bottom", (660, 984, 600, 56)),
    ("left", (0, 220, 64, 600)),
    ("right", (1856, 220, 64, 600)),
])
def test_geometry_on_each_side(side, expected):
    assert geometry(side, AREA) == expected


def test_geometry_on_second_screen_and_small_screen():
    assert geometry("right", (1920, 0, 1280, 1000)) == (1920 + 1280 - 64, 200, 64, 600)
    assert geometry("left", (0, 0, 800, 500)) == (0, 0, 64, 500)  # mai più alta dello schermo


class Screen:
    def __init__(self, x, y, width, height, frame=None):
        self.x, self.y, self.width, self.height, self.frame = x, y, width, height, frame


class Rect:
    X, Y, Width, Height = 0, 0, 1920, 1040


def test_work_area_prefers_working_area():
    assert work_area(Screen(0, 0, 1920, 1080, Rect())) == (0, 0, 1920, 1040)
    assert work_area(Screen(0, 0, 1920, 1080, object())) == (0, 0, 1920, 1080)


def test_screen_of_window():
    screens = [Screen(0, 0, 1920, 1080), Screen(1920, 0, 1280, 1024)]
    window = types.SimpleNamespace(x=2000, y=100, width=800, height=600)
    assert screen_of(window, screens) is screens[1]
    assert screen_of(types.SimpleNamespace(), screens) is screens[0]


def test_clean_action_keeps_only_known_values():
    assert clean_action({"command": "open"}, {"merge"}) == {"command": "open"}
    assert clean_action({"command": "rm -rf"}, {"merge"}) is None
    assert clean_action({"tool": "nope"}, {"merge"}) is None
    action = clean_action({"tool": "merge", "files": [{"id": "abc123", "name": "a.pdf", "size": 10, "kind": "pdf"},
                                                      {"id": "../x"}, "x"]}, {"merge"})
    assert action == {"tool": "merge", "files": [{"id": "abc123", "name": "a.pdf", "size": 10, "kind": "pdf"}]}
    assert clean_action("non un dizionario", {"merge"}) is None
    assert clean_action({"open_file": "a'b"}, set()) is None
    assert run_action_js({"command": "open"}) == 'window.wpdfExternal && window.wpdfExternal({"command": "open"})'


@pytest.fixture
def server(tmp_path):
    from wmt_pdf.gui.server import make_server

    srv = make_server(tmp_path / "lavoro", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.x, self.y, self.width, self.height = 100, 100, 1280, 840
        self.events = types.SimpleNamespace(closing=Event(), closed=Event())
        self.calls = []

    def __getattr__(self, name):  # hide, show, destroy, resize, move, evaluate_js
        return lambda *args: self.calls.append((name, *args))


@pytest.fixture
def desktop(server, monkeypatch):
    from wmt_pdf.gui.server import DesktopApi

    created = []

    def create_window(title, url, **kwargs):
        window = FakeWindow(url=url, **kwargs)
        created.append(window)
        return window

    fake = types.SimpleNamespace(create_window=create_window, screens=[Screen(0, 0, 1920, 1080, Rect())])
    monkeypatch.setitem(sys.modules, "webview", fake)
    api = DesktopApi(server)
    main = FakeWindow()
    api.attach_window(main)
    return api, main, created


def test_compact_dock_and_expand(desktop):
    api, main, created = desktop
    assert api.compact("left") == "left"
    bar = created[0]
    assert bar.kwargs["on_top"] and bar.kwargs["frameless"] and "/compact?side=left" in bar.kwargs["url"]
    assert (bar.kwargs["x"], bar.kwargs["y"], bar.kwargs["width"], bar.kwargs["height"]) == (0, 220, 64, 600)
    assert ("hide",) in main.calls
    assert api.dock("bottom") == "bottom"
    assert ("resize", 600, 56) in bar.calls and ("move", 660, 984) in bar.calls
    assert api.dock("in diagonale") == ""
    api.expand({"tool": "merge", "files": [{"id": "abc", "name": "a.pdf"}]})
    assert ("show",) in main.calls and ("destroy",) in bar.calls
    js = next(c[1] for c in main.calls if c[0] == "evaluate_js")
    assert '"tool": "merge"' in js


def test_closing_the_bar_brings_back_the_window(desktop):
    api, main, created = desktop
    api.compact("top")
    main.calls.clear()
    created[0].events.closed.handlers[0]()  # chiusa con Alt+F4
    assert ("show",) in main.calls
    api.expand({"command": "rm"})  # nessuna barra, azione non valida: solo la finestra
    assert not [c for c in main.calls if c[0] == "evaluate_js"]


def test_compact_page_has_token(server):
    url = f"http://127.0.0.1:{server.server_address[1]}/compact?side=right"
    with urllib.request.urlopen(url) as resp:
        html = resp.read().decode("utf-8")
    assert server.token in html and "compact.js" in html


def test_area_from_the_page_wins_and_is_validated(desktop):
    from wmt_pdf.gui.compact import parse_area

    assert parse_area({"x": 0, "y": 0, "width": 1280, "height": 680}) == (0, 0, 1280, 680)
    assert parse_area({"x": "a", "y": 0, "width": 1280, "height": 680}) is None
    assert parse_area({"x": 0, "y": 0, "width": 10, "height": 680}) is None  # troppo piccola: valori assurdi
    assert parse_area([1, 2]) is None
    api, _, created = desktop
    # Schermo in scala 150%: la pagina vede 1280x680 pixel CSS, ed è in queste unità che pywebview sposta.
    api.compact("bottom", {"x": 0, "y": 0, "width": 1280, "height": 680})
    bar = created[0]
    assert (bar.kwargs["x"], bar.kwargs["y"]) == (340, 624)
    api.compact("top", {"x": 0, "y": 0, "width": 1280, "height": 680})  # già aperta: si sposta, non se ne apre un'altra
    assert len(created) == 1 and ("move", 340, 0) in bar.calls


def test_malformed_action_is_cleaned_before_closing_the_bar(desktop):
    api, main, created = desktop
    api.compact("top")
    api.expand({"tool": "merge", "files": [{"id": "abc", "name": "x" * 1000, "size": "molto"}]})
    js = next(c[1] for c in main.calls if c[0] == "evaluate_js")
    assert '"size": 0' in js and "x" * 256 not in js and ("destroy",) in created[0].calls


def test_report_label_stays_readable():
    from wmt_pdf.verify import Report

    report = Report(kind="PDF", label="", label_key="campo {field}", label_values={"field": "Firma1"})
    assert report.label == "campo Firma1"  # così nel JSON di verify, come prima

"""Fase 6: server locale dell'interfaccia grafica."""

import json
import threading
import urllib.error
import urllib.request

import pikepdf
import pytest

from wmt_pdf.gui.server import make_server
from wmt_pdf.gui.tools import TOOLS, build_args


@pytest.fixture
def server(tmp_path):
    srv = make_server(tmp_path / "lavoro", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, headers=None, token=True):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("X-Wpdf-Token", srv.token)
    if isinstance(body, dict):
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def upload(srv, path):
    status, body, _ = call(srv, "POST", "/api/upload", path.read_bytes(),
                           {"X-File-Name": path.name, "Content-Type": "application/octet-stream"})
    assert status == 200, body
    return json.loads(body)["id"]


def test_index_has_token_and_tools(server):
    status, body, headers = call(server, "GET", "/", token=False)
    assert status == 200
    html = body.decode()
    assert server.token in html
    assert "wmt-pdf" in html
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
    status, body, _ = call(server, "GET", "/api/tools")
    tools = json.loads(body)
    ids = {t["id"] for t in tools}
    assert {"merge", "split", "optimize", "convert", "sign", "verify", "ocr", "watermark"} <= ids
    assert all(t["label"] and t["icon"] for t in tools)


def test_api_requires_token(server):
    status, _, _ = call(server, "GET", "/api/tools", token=False)
    assert status == 403
    status, _, _ = call(server, "POST", "/api/run", {"tool": "info", "inputs": []}, token=False)
    assert status == 403


def test_rejects_foreign_origin(server):
    status, _, _ = call(server, "POST", "/api/run", {"tool": "info", "inputs": []},
                        {"Origin": "https://sito-malevolo.example"})
    assert status == 403


def test_merge_through_api(server, text_pdf, table_pdf):
    a, b = upload(server, text_pdf), upload(server, table_pdf)
    status, body, _ = call(server, "POST", "/api/run", {"tool": "merge", "inputs": [a, b], "options": {}})
    result = json.loads(body)
    assert status == 200 and result["code"] == 0, result
    out = result["outputs"][0]
    assert out["name"].endswith(".pdf")
    status, pdf_bytes, headers = call(server, "GET", f"/api/file/{out['id']}")
    assert status == 200 and headers["Content-Type"] == "application/pdf"
    path = server.workdir / "check.pdf"
    path.write_bytes(pdf_bytes)
    with pikepdf.open(path) as pdf:
        assert len(pdf.pages) == 4


def test_info_text_result(server, text_pdf):
    a = upload(server, text_pdf)
    _, body, _ = call(server, "POST", "/api/run", {"tool": "info", "inputs": [a]})
    result = json.loads(body)
    assert result["code"] == 0 and "Pagine: 3" in result["stdout"]
    assert result["outputs"] == []


def test_split_returns_many_outputs(server, text_pdf):
    a = upload(server, text_pdf)
    _, body, _ = call(server, "POST", "/api/run", {"tool": "split", "inputs": [a], "options": {"every": 1}})
    result = json.loads(body)
    assert result["code"] == 0, result
    assert len(result["outputs"]) == 3


def test_error_is_reported_not_crash(server, text_pdf):
    a = upload(server, text_pdf)
    _, body, _ = call(server, "POST", "/api/run", {"tool": "pages", "inputs": [a], "options": {"keep": "9"}})
    result = json.loads(body)
    assert result["code"] == 1 and "fuori dal documento" in result["stderr"]


def test_unknown_tool_and_bad_ids(server, text_pdf):
    status, _, _ = call(server, "POST", "/api/run", {"tool": "rm -rf", "inputs": []})
    assert status == 400
    status, _, _ = call(server, "POST", "/api/run", {"tool": "info", "inputs": ["../../etc/passwd"]})
    assert status == 400
    status, _, _ = call(server, "GET", "/api/file/..%2F..%2Fsegreto")
    assert status in (400, 404)


def test_upload_name_is_sanitised(server):
    status, body, _ = call(server, "POST", "/api/upload", b"%PDF-1.4",
                           {"X-File-Name": "..\\..\\evil.pdf", "Content-Type": "application/octet-stream"})
    assert status == 200
    item = json.loads(body)
    assert item["name"] == "evil.pdf"
    assert not (server.workdir.parent / "evil.pdf").exists()


def test_options_are_validated(tmp_path):
    tool = TOOLS["optimize"]
    with pytest.raises(ValueError):
        build_args(tool, [tmp_path / "a.pdf"], {"profile": "--force; cattivo"}, tmp_path / "out")
    with pytest.raises(ValueError):
        build_args(TOOLS["merge"], [tmp_path / "a.pdf"], {"--evil": "1"}, tmp_path / "out")


def test_secret_option_goes_through_env(tmp_path):
    tool = TOOLS["protect"]
    argv, env = build_args(tool, [tmp_path / "a.pdf"], {"new_password": "segreta"}, tmp_path / "out")
    assert "segreta" not in " ".join(argv)
    assert "segreta" in env.values()


def test_rejects_foreign_host_header(server):
    """DNS rebinding: un nome di dominio che punta a 127.0.0.1 non deve ottenere la pagina col token."""
    status, body, _ = call(server, "GET", "/", token=False, headers={"Host": "sito-malevolo.example"})
    assert status == 403 and server.token.encode() not in body


def test_rejects_other_local_port_origin(server):
    status, _, _ = call(server, "POST", "/api/run", {"tool": "info", "inputs": []},
                        {"Origin": "http://127.0.0.1:9"})
    assert status == 403


def test_malformed_run_body(server):
    status, _, _ = call(server, "POST", "/api/run", b"[1]", {"Content-Type": "application/json"})
    assert status == 400
    status, _, _ = call(server, "POST", "/api/run", {"tool": "info", "inputs": "abc", "options": []})
    assert status == 400


def test_fill_csv_many_rows_through_api(server, tmp_path):
    from reportlab.pdfgen import canvas

    form = tmp_path / "modulo.pdf"
    c = canvas.Canvas(str(form))
    c.acroForm.textfield(name="nome", x=100, y=700, width=200, height=20)
    c.showPage()
    c.save()
    data = tmp_path / "elenco.csv"
    data.write_text("nome\nAnna\nBruno\nCarla\n", encoding="utf-8")
    pdf_id, csv_id = upload(server, form), upload(server, data)
    _, body, _ = call(server, "POST", "/api/run", {"tool": "fill", "inputs": [pdf_id], "options": {"data": csv_id}})
    result = json.loads(body)
    assert result["code"] == 0, result
    assert len(result["outputs"]) == 3


def test_invalid_choice_is_refused_by_catalog(server, text_pdf):
    a = upload(server, text_pdf)
    status, _, _ = call(server, "POST", "/api/run", {"tool": "rotate", "inputs": [a], "options": {"angle": "45"}})
    assert status == 400  # 45 non è tra le scelte: non arriva nemmeno a wpdf


# --- documenti aperti ------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path / "cache"))


def post(srv, path, body):
    status, raw, _ = call(srv, "POST", path, body)
    return status, json.loads(raw)


def test_document_edit_undo_redo(server, text_pdf, cache):
    file_id = upload(server, text_pdf)
    status, doc = post(server, "/api/doc/open", {"file_id": file_id})
    assert status == 200 and doc["pages"] == 3 and not doc["can_undo"] and not doc["modified"]
    status, result = post(server, f"/api/doc/{doc['id']}/apply",
                          {"tool": "addtext", "options": {"page": 1, "at": "72,600", "text": "Nota nuova"}})
    assert result["code"] == 0, result
    state = result["document"]
    assert state["can_undo"] and state["modified"] and state["version"] == 2
    _, objs, _ = call(server, "GET", f"/api/doc/{doc['id']}/textobjects?page=1")
    assert any(o["text"] == "Nota nuova" for o in json.loads(objs))
    _, state = post(server, f"/api/doc/{doc['id']}/undo", {})
    assert state["version"] == 1 and state["can_redo"] and not state["modified"]
    _, objs, _ = call(server, "GET", f"/api/doc/{doc['id']}/textobjects?page=1")
    assert not any(o["text"] == "Nota nuova" for o in json.loads(objs))
    _, state = post(server, f"/api/doc/{doc['id']}/redo", {})
    assert state["version"] == 2


def test_document_failed_edit_keeps_version(server, text_pdf, cache):
    _, doc = post(server, "/api/doc/open", {"file_id": upload(server, text_pdf)})
    _, result = post(server, f"/api/doc/{doc['id']}/apply",
                     {"tool": "edittext", "options": {"page": 1, "at": "500,50", "text": "x"}})
    assert result["code"] == 1 and "nessun testo" in result["stderr"]
    assert result["document"]["version"] == 1


def test_browser_mode_cannot_use_paths(server, text_pdf, cache):
    status, body = post(server, "/api/doc/open", {"path": str(text_pdf)})
    assert status == 400 and "finestra" in body["error"]
    _, doc = post(server, "/api/doc/open", {"file_id": upload(server, text_pdf)})
    status, body = post(server, f"/api/doc/{doc['id']}/save", {"path": str(text_pdf.parent / "x.pdf")})
    assert status == 400


def test_desktop_open_save_and_recent(server, tmp_path, text_pdf, cache):
    import shutil

    server.desktop = True
    original = tmp_path / "originale.pdf"
    shutil.copy(text_pdf, original)
    server.documents.allow(str(original))  # come se l'utente l'avesse scelto nella finestra «Apri»
    server.documents.allow(str(tmp_path / "copia"))
    _, doc = post(server, "/api/doc/open", {"path": str(original)})
    assert doc["path"] == str(original)
    _, result = post(server, f"/api/doc/{doc['id']}/apply", {"tool": "rotate", "options": {"angle": "90"}})
    assert result["code"] == 0
    assert original.read_bytes() == text_pdf.read_bytes()  # l'originale cambia solo con «Salva»
    copy = tmp_path / "copia"
    _, state = post(server, f"/api/doc/{doc['id']}/save", {"path": str(copy)})
    assert state["name"] == "copia.pdf" and not state["modified"]
    with pikepdf.open(tmp_path / "copia.pdf") as pdf:
        assert int(pdf.pages[0].Rotate) == 90
    _, body, _ = call(server, "GET", "/api/recent")
    names = [r["name"] for r in json.loads(body)["files"]]
    assert names[:2] == ["copia.pdf", "originale.pdf"]


def test_document_unknown(server):
    status, _ = post(server, "/api/doc/abc/undo", {})
    assert status == 400


def test_desktop_paths_must_be_chosen(server, tmp_path, text_pdf, cache):
    server.desktop = True
    status, body = post(server, "/api/doc/open", {"path": str(text_pdf)})
    assert status == 400 and "non scelto" in body["error"]
    status, body = post(server, "/api/doc/open", {"path": r"\\127.0.0.9\share\a.pdf"})
    assert status == 400 and "rete" in body["error"]
    secret = tmp_path / "segreto.txt"
    secret.write_text("x", encoding="utf-8")
    server.documents.allow(str(secret))
    status, body = post(server, "/api/doc/open", {"path": str(secret)})
    assert status == 400 and "PDF" in body["error"]
    _, doc = post(server, "/api/doc/open", {"file_id": upload(server, text_pdf)})
    status, body = post(server, f"/api/doc/{doc['id']}/save", {"path": str(tmp_path / "altro.pdf")})
    assert status == 400 and not (tmp_path / "altro.pdf").exists()


def test_p7m_is_never_overwritten(server, tmp_path, p7m_file, cache):
    import shutil

    server.desktop = True
    signed = tmp_path / "contratto.pdf.p7m"
    shutil.copy(p7m_file, signed)
    server.documents.allow(str(signed))
    _, doc = post(server, "/api/doc/open", {"path": str(signed)})
    post(server, f"/api/doc/{doc['id']}/apply", {"tool": "rotate", "options": {"angle": "90"}})
    status, body = post(server, f"/api/doc/{doc['id']}/save", {})
    assert status == 400 and "Salva con nome" in body["error"]
    assert signed.read_bytes() == p7m_file.read_bytes()


def test_modified_after_undo_and_new_edit(server, tmp_path, text_pdf, cache):
    server.desktop = True
    target = tmp_path / "salvato.pdf"
    server.documents.allow(str(target))
    _, doc = post(server, "/api/doc/open", {"file_id": upload(server, text_pdf)})
    did = doc["id"]
    post(server, f"/api/doc/{did}/apply", {"tool": "rotate", "options": {"angle": "90"}})
    _, state = post(server, f"/api/doc/{did}/save", {"path": str(target)})
    assert not state["modified"]
    post(server, f"/api/doc/{did}/undo", {})
    _, result = post(server, f"/api/doc/{did}/apply", {"tool": "rotate", "options": {"angle": "180"}})
    assert result["document"]["modified"] is True


def test_desktop_api_exposes_only_methods(server):
    """pywebview esplora ricorsivamente gli attributi pubblici di js_api: la finestra non deve essere tra questi."""
    import inspect

    from wmt_pdf.gui.server import DesktopApi

    class Event:
        def __iadd__(self, handler):
            return self

    class FakeWindow:
        class events:
            closing = Event()

    api = DesktopApi(server)
    api.attach_window(FakeWindow())
    public = [name for name in dir(api) if not name.startswith("_")]
    assert public == ["attach_window", "open_dialog", "save_dialog"]
    assert all(inspect.ismethod(getattr(api, name)) for name in public)


def test_document_merge_with_other_files(server, text_pdf, table_pdf, cache):
    _, doc = post(server, "/api/doc/open", {"file_id": upload(server, text_pdf)})
    other = upload(server, table_pdf)
    _, result = post(server, f"/api/doc/{doc['id']}/apply",
                     {"tool": "merge", "options": {"bookmarks": False}, "files": [other], "position": "end"})
    assert result["code"] == 0, result
    merged = result["document"]
    assert merged["pages"] > doc["pages"] and merged["version"] == 2 and merged["name"] == doc["name"]
    _, result = post(server, f"/api/doc/{doc['id']}/apply",
                     {"tool": "merge", "options": {}, "files": [other], "position": "start"})
    assert result["code"] == 0 and result["document"]["pages"] > merged["pages"]


@pytest.mark.parametrize("body", [
    {"tool": "rotate", "options": {"angle": "90"}, "files": ["x"]},  # file in più solo per «Unisci»
    {"tool": "merge", "options": {}, "files": "non una lista"},
    {"tool": "merge", "options": {}, "files": ["0" * 32]},
    {"tool": "merge", "options": {}, "files": [], "position": "in mezzo"},
])
def test_document_merge_bad_requests(server, text_pdf, cache, body):
    _, doc = post(server, "/api/doc/open", {"file_id": upload(server, text_pdf)})
    status, _ = post(server, f"/api/doc/{doc['id']}/apply", body)
    assert status == 400


def test_document_close(server, text_pdf, cache):
    file_id = upload(server, text_pdf)
    _, doc = post(server, "/api/doc/open", {"file_id": file_id})
    _, result = post(server, f"/api/doc/{doc['id']}/apply", {"tool": "rotate", "options": {"angle": "90"}})
    edited = result["document"]["file_id"]
    status, body = post(server, f"/api/doc/{doc['id']}/close", {})
    assert status == 200 and body == {"closed": True}
    status, _ = post(server, f"/api/doc/{doc['id']}/undo", {})
    assert status == 400  # il documento non c'è più
    assert call(server, "GET", f"/api/file/{edited}")[0] != 200  # versioni intermedie tolte
    assert call(server, "GET", f"/api/file/{file_id}")[0] == 200  # il file caricato resta (es. «Apri» dai risultati)

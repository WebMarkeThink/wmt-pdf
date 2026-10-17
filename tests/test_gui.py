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

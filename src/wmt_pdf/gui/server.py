"""Server locale dell'interfaccia: pagine statiche e un'API minima sopra wpdf.

Sicurezza:
- ascolta solo su 127.0.0.1;
- ogni chiamata all'API vuole il token casuale della sessione (scritto nella pagina) e un'origine locale:
  un sito aperto nel browser non può usare l'API;
- i file stanno in una cartella di lavoro e si indicano per id, mai per percorso;
- i comandi sono solo quelli del catalogo (tools.py), con opzioni controllate.
"""

from __future__ import annotations

import contextlib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from wmt_pdf.gui.tools import TOOLS, build_args, catalog

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 500 * 1024 * 1024
MAX_SESSION = 4 * 1024 * 1024 * 1024  # spazio complessivo dei file caricati in una sessione
CHUNK = 1024 * 1024
EXIT_USAGE = 64  # argomenti non accettati (codice distinto da 2 = firma non valida)
ID = re.compile(r"[0-9a-f]{32}")
# wasm-unsafe-eval: PDF.js decodifica le immagini delle scansioni (JBIG2, JPEG 2000) in WebAssembly.
CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "worker-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, workdir: Path):
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(24)
        self.workdir = workdir
        self.files: dict[str, Path] = {}
        self.uploaded = 0
        self.lock = threading.Lock()  # wpdf scrive su stdout: un comando alla volta
        (workdir / "file").mkdir(parents=True, exist_ok=True)

    def register(self, path: Path) -> dict:
        file_id = uuid.uuid4().hex
        self.files[file_id] = path
        return {"id": file_id, "name": path.name, "size": path.stat().st_size, "kind": _kind(path)}

    def resolve(self, file_id: str) -> Path:
        if not isinstance(file_id, str) or not ID.fullmatch(file_id) or file_id not in self.files:
            raise ValueError("file sconosciuto")
        return self.files[file_id]


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        return "image"
    return "file"


def _safe_name(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(" .")
    return base[:150] or "file"


def make_server(workdir: Path, port: int = 0) -> GuiServer:
    workdir.mkdir(parents=True, exist_ok=True)
    return GuiServer(("127.0.0.1", port), workdir)


class Handler(BaseHTTPRequestHandler):
    server: GuiServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return  # niente righe di log a ogni richiesta

    # --- risposte ---

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _local_host(self) -> bool:
        """Contro il DNS rebinding: si risponde solo a chi chiama 127.0.0.1 o localhost su questa porta."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _authorized(self) -> bool:
        if not secrets.compare_digest(self.headers.get("X-Wpdf-Token", ""), self.server.token):
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != self.server.server_address[1]:
                return False
        return True

    # --- GET ---

    def do_GET(self) -> None:
        if not self._local_host():
            return self._error(403, "host non ammesso")
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.server.token)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._static(unquote(path[len("/static/"):]))
        if not path.startswith("/api/"):
            return self._error(404, "pagina inesistente")
        if not self._authorized():
            return self._error(403, "accesso negato")
        if path == "/api/tools":
            return self._json(200, catalog())
        if path.startswith("/api/file/"):
            return self._file(unquote(path[len("/api/file/"):]))
        return self._error(404, "risorsa inesistente")

    def _static(self, rel: str) -> None:
        target = (STATIC / rel).resolve()
        if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
            return self._error(404, "file inesistente")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".mjs":
            content_type = "text/javascript"
        self._send(200, target.read_bytes(), content_type)

    def _file(self, file_id: str) -> None:
        try:
            path = self.server.resolve(file_id)
        except ValueError:
            return self._error(404, "file sconosciuto")
        download = "download=1" in (urlparse(self.path).query or "")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        quoted = path.name.encode("ascii", "replace").decode().replace('"', "_")
        disposition = f'{"attachment" if download else "inline"}; filename="{quoted}"'
        size = path.stat().st_size
        self.send_response(200)
        for key, value in (("Content-Type", content_type), ("Content-Length", str(size)), ("Cache-Control", "no-store"),
                           ("X-Content-Type-Options", "nosniff"), ("Content-Disposition", disposition)):
            self.send_header(key, value)
        self.end_headers()
        with path.open("rb") as f:  # a pezzi: un file grande non finisce tutto in memoria
            shutil.copyfileobj(f, self.wfile, CHUNK)

    # --- POST ---

    def do_POST(self) -> None:
        if not self._local_host():
            return self._error(403, "host non ammesso")
        path = urlparse(self.path).path
        if not self._authorized():
            return self._error(403, "accesso negato")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            return self._error(411, "lunghezza della richiesta mancante")
        if length > MAX_UPLOAD:
            return self._error(413, "file troppo grande")
        if path == "/api/upload":
            if self.server.uploaded + length > MAX_SESSION:
                return self._error(413, "spazio della sessione esaurito: chiudi e riapri wmt-pdf")
            return self._upload(length)
        if path == "/api/run":
            if length > 1024 * 1024:
                return self._error(413, "richiesta troppo grande")
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._error(400, "richiesta non valida")
            if not isinstance(request, dict):
                return self._error(400, "richiesta non valida")
            return self._run(request)
        return self._error(404, "risorsa inesistente")

    def _upload(self, length: int) -> None:
        name = _safe_name(unquote(self.headers.get("X-File-Name", "file")))
        folder = self.server.workdir / "file" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / name
        remaining = length
        with target.open("wb") as f:  # su disco a pezzi, non tutto in memoria
            while remaining > 0:
                chunk = self.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        self.server.uploaded += length
        self._json(200, self.server.register(target))

    def _run(self, request: dict) -> None:
        tool = TOOLS.get(str(request.get("tool")))
        if tool is None:
            return self._error(400, "strumento sconosciuto")
        try:
            raw_inputs = request.get("inputs") or []
            raw_options = request.get("options") or {}
            if not isinstance(raw_inputs, list) or not isinstance(raw_options, dict):
                raise ValueError("richiesta non valida")  # noqa: TRY004 - diventa una risposta 400, come gli altri ValueError
            inputs = [self.server.resolve(i) for i in raw_inputs]
            options = dict(raw_options)
            for opt in tool.options:  # le opzioni di tipo file arrivano come id
                if opt.kind == "file" and options.get(opt.name):
                    options[opt.name] = self.server.resolve(options[opt.name])
            outdir = self.server.workdir / "file" / uuid.uuid4().hex
            outdir.mkdir(parents=True)
            argv, env = build_args(tool, inputs, options, outdir)
        except ValueError as exc:
            return self._error(400, str(exc))
        code, stdout, stderr = self._invoke(argv, env)
        # Con un errore si scartano anche i file scritti a metà; 2 e 3 (verifica firme) non sono errori.
        produced = sorted(p for p in outdir.rglob("*") if p.is_file()) if code in (0, 2, 3) else []
        outputs = [self.server.register(p) for p in produced]
        if not outputs:
            shutil.rmtree(outdir, ignore_errors=True)
        # Nei messaggi l'utente vede i nomi dei file, non i percorsi della cartella di lavoro.
        stdout = stdout.replace(str(outdir) + os.sep, "").replace(str(self.server.workdir), "…")
        stderr = stderr.replace(str(outdir) + os.sep, "").replace(str(self.server.workdir), "…")
        self._json(200, {"code": code, "stdout": stdout, "stderr": stderr, "outputs": outputs})

    def _invoke(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        from wmt_pdf.cli import main

        with self.server.lock:
            # Le password restano nell'ambiente solo per la durata del comando (un comando alla volta).
            for key, value in env.items():
                os.environ[key] = value
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = main(argv)
                    except SystemExit:  # argparse rifiuta gli argomenti
                        code = EXIT_USAGE
                    except Exception as exc:  # noqa: BLE001 - l'interfaccia deve sempre ricevere una risposta
                        print(f"wpdf: errore interno ({type(exc).__name__}): {exc}", file=err)
                        code = 1
            finally:
                for key in env:
                    os.environ.pop(key, None)
            return code, out.getvalue(), err.getvalue()


def serve(workdir: Path, port: int = 0, open_window: bool = True) -> None:
    """Avvia il server e apre l'interfaccia: finestra nativa se c'è pywebview, altrimenti il browser."""
    server = make_server(workdir, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"wmt-pdf: interfaccia su {url} (Ctrl+C per chiudere)")
    try:
        if open_window:
            try:
                import webview  # pywebview, extra "gui"

                webview.settings["ALLOW_DOWNLOADS"] = True  # senza, «Salva» non fa nulla nella finestra
                webview.create_window("wmt-pdf", url, width=1280, height=840, min_size=(900, 600))
                webview.start()
                return
            except ImportError:
                import webbrowser

                webbrowser.open(url)
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()

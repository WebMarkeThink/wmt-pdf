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
from typing import Any
from urllib.parse import unquote, urlparse

from wmt_pdf.gui.documents import DocumentStore, forget, recent, save_settings, settings
from wmt_pdf.gui.tools import TOOLS, build_args, catalog
from wmt_pdf.i18n import t
from wmt_pdf.source import CliError

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
        self.documents = DocumentStore(self)
        self.desktop = False  # True nella finestra nativa: si possono aprire e salvare file per percorso
        self.language = "it"  # lingua dell'interfaccia e dei comandi che esegue (vedi set_language)

    def register(self, path: Path) -> dict:
        file_id = uuid.uuid4().hex
        self.files[file_id] = path
        return {"id": file_id, "name": path.name, "size": path.stat().st_size, "kind": _kind(path)}

    def discard(self, file_id: str) -> None:
        """Toglie un file prodotto che non serve più (versioni annullate e poi sostituite)."""
        path = self.files.pop(file_id, None)
        if path is not None and path.is_relative_to(self.workdir):
            path.unlink(missing_ok=True)

    def resolve(self, file_id: str) -> Path:
        if not isinstance(file_id, str) or not ID.fullmatch(file_id) or file_id not in self.files:
            raise ValueError(t("file sconosciuto"))
        return self.files[file_id]

    def execute(self, tool_id, raw_inputs, raw_options) -> dict:
        """Esegue uno strumento del catalogo; ValueError per richieste non valide."""
        tool = TOOLS.get(str(tool_id))
        if tool is None:
            raise ValueError(t("strumento sconosciuto"))
        if not isinstance(raw_inputs, list) or not isinstance(raw_options, dict):
            raise ValueError(t("richiesta non valida"))  # noqa: TRY004 - diventa una risposta 400
        inputs = [self.resolve(i) for i in raw_inputs]
        options = dict(raw_options)
        for opt in tool.options:  # le opzioni di tipo file arrivano come id
            if opt.kind == "file" and options.get(opt.name):
                options[opt.name] = self.resolve(options[opt.name])
        outdir = self.workdir / "file" / uuid.uuid4().hex
        outdir.mkdir(parents=True)
        argv, env = build_args(tool, inputs, options, outdir)
        code, stdout, stderr = self.invoke(argv, env)
        # Con un errore si scartano anche i file scritti a metà; 2 e 3 (verifica firme) non sono errori.
        produced = sorted(p for p in outdir.rglob("*") if p.is_file()) if code in (0, 2, 3) else []
        outputs = [self.register(p) for p in produced]
        if not outputs:
            shutil.rmtree(outdir, ignore_errors=True)
        # Nei messaggi l'utente vede i nomi dei file, non i percorsi della cartella di lavoro.
        stdout = stdout.replace(str(outdir) + os.sep, "").replace(str(self.workdir), "…")
        stderr = stderr.replace(str(outdir) + os.sep, "").replace(str(self.workdir), "…")
        return {"code": code, "stdout": stdout, "stderr": stderr, "outputs": outputs}

    def set_language(self, language: str) -> None:
        from wmt_pdf.i18n import set_language

        self.language = set_language(language)  # anche per i messaggi del server stesso

    def invoke(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        from wmt_pdf.cli import main

        with self.lock:
            # Le password restano nell'ambiente solo per la durata del comando (un comando alla volta).
            for key, value in env.items():
                os.environ[key] = value
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = main([f"--language={self.language}", *argv])
                    except SystemExit:  # argparse rifiuta gli argomenti
                        code = EXIT_USAGE
                    except Exception as exc:  # noqa: BLE001 - l'interfaccia deve sempre ricevere una risposta
                        print(t("wpdf: errore interno ({name}): {exc}", name=type(exc).__name__, exc=exc), file=err)
                        code = 1
            finally:
                for key in env:
                    os.environ.pop(key, None)
                self.set_language(self.language)  # main() ha impostato quella del comando: può essere cambiata
            return code, out.getvalue(), err.getvalue()


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
    server = GuiServer(("127.0.0.1", port), workdir)
    server.set_language(settings()["language"])
    return server


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
            return self._error(403, t("host non ammesso"))
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/compact"):
            page = "compact.html" if path == "/compact" else "index.html"
            html = (STATIC / page).read_text(encoding="utf-8").replace("__TOKEN__", self.server.token)
            html = html.replace('<html lang="it">', f'<html lang="{self.server.language}">', 1)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._static(unquote(path[len("/static/"):]))
        if not path.startswith("/api/"):
            return self._error(404, t("pagina inesistente"))
        if not self._authorized():
            return self._error(403, t("accesso negato"))
        if path == "/api/tools":
            return self._json(200, catalog())
        if path == "/api/recent":
            return self._json(200, {"desktop": self.server.desktop, "files": recent() if self.server.desktop else []})
        if path == "/api/settings":
            return self._json(200, settings())
        if path == "/api/i18n":
            from wmt_pdf.i18n import catalog as messages

            return self._json(200, messages(self.server.language))
        if path.startswith("/api/doc/"):
            return self._doc_get(path[len("/api/doc/"):])
        if path.startswith("/api/file/"):
            return self._file(unquote(path[len("/api/file/"):]))
        return self._error(404, t("risorsa inesistente"))

    def _static(self, rel: str) -> None:
        target = (STATIC / rel).resolve()
        if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
            return self._error(404, t("file inesistente"))
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".mjs":
            content_type = "text/javascript"
        self._send(200, target.read_bytes(), content_type)

    def _file(self, file_id: str) -> None:
        try:
            path = self.server.resolve(file_id)
        except ValueError:
            return self._error(404, t("file sconosciuto"))
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
            return self._error(403, t("host non ammesso"))
        path = urlparse(self.path).path
        if not self._authorized():
            return self._error(403, t("accesso negato"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            return self._error(411, t("lunghezza della richiesta mancante"))
        if length > MAX_UPLOAD:
            return self._error(413, t("file troppo grande"))
        if path == "/api/upload":
            if self.server.uploaded + length > MAX_SESSION:
                return self._error(413, t("spazio della sessione esaurito: chiudi e riapri wmt-pdf"))
            return self._upload(length)
        if length > 1024 * 1024:
            return self._error(413, t("richiesta troppo grande"))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(400, t("richiesta non valida"))
        if not isinstance(request, dict):
            return self._error(400, t("richiesta non valida"))
        try:
            if path == "/api/run":
                return self._json(200, self.server.execute(request.get("tool"), request.get("inputs") or [],
                                                           request.get("options") or {}))
            if path.startswith("/api/doc"):
                return self._doc_post(path, request)
            if path == "/api/settings":
                values = save_settings(request)
                self.server.set_language(values["language"])
                return self._json(200, values)
            if path == "/api/recent/forget":
                if not self.server.desktop:
                    return self._error(400, t("i file recenti esistono solo nella finestra di wmt-pdf"))
                forget(str(request.get("path", "")))
                return self._json(200, {"files": recent()})
        except ValueError as exc:
            return self._error(400, str(exc))
        except OSError as exc:  # disco: file bloccato, permessi, spazio
            return self._error(400, t("operazione sul disco non riuscita: {reason}", reason=exc.strerror or exc))
        return self._error(404, t("risorsa inesistente"))

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

    # --- documenti ---

    def _doc_get(self, rest: str) -> None:
        store = self.server.documents
        doc_id, _, action = rest.partition("/")
        try:
            doc = store.get(doc_id)
            if action == "":
                return self._json(200, store.state(doc))
            if action in ("objects", "textobjects"):
                from wmt_pdf.objects import list_page_objects
                from wmt_pdf.textedit import list_objects

                query = dict(part.split("=", 1) for part in (urlparse(self.path).query or "").split("&") if "=" in part)
                page = int(query.get("page", "1"))
                reader = list_page_objects if action == "objects" else list_objects
                items = reader(store.current(doc).read_bytes(), page)
                return self._json(200, [o.__dict__ for o in items])
        except (ValueError, CliError) as exc:
            return self._error(400, str(exc))
        return self._error(404, t("risorsa inesistente"))

    def _doc_post(self, path: str, request: dict) -> None:
        with self.server.documents.lock:
            return self._doc_post_locked(path, request)

    def _doc_post_locked(self, path: str, request: dict) -> None:
        store = self.server.documents
        if path == "/api/doc/open":
            if request.get("path"):
                if not self.server.desktop:
                    raise ValueError(t("aprire per percorso è possibile solo nella finestra di wmt-pdf"))
                doc = store.open_path(request["path"])
            else:
                doc = store.open_file(str(request.get("file_id", "")))
            return self._json(200, store.state(doc))
        doc_id, _, action = path[len("/api/doc/"):].partition("/")
        doc = store.get(doc_id)
        if action == "apply":
            inputs = [doc.versions[doc.index]]
            extra, position = request.get("files"), request.get("position", "end")
            if extra is not None:  # «Unisci»: altri file prima o dopo il documento aperto
                if request.get("tool") != "merge" or not isinstance(extra, list) or position not in ("start", "end"):
                    raise ValueError(t("file aggiunti ammessi solo per «Unisci», all'inizio o in fondo"))
                for file_id in extra:
                    self.server.resolve(file_id)
                inputs = extra + inputs if position == "start" else inputs + extra
            result = self.server.execute(request.get("tool"), inputs, request.get("options") or {})
            pdfs = [o for o in result["outputs"] if o["kind"] == "pdf"]
            if result["code"] == 0 and len(pdfs) == 1:
                store.push(doc, pdfs[0]["id"])  # una modifica del documento: diventa la nuova versione
                result["outputs"] = []
            result["document"] = store.state(doc)
            return self._json(200, result)
        if action == "close":
            store.close(doc)
            return self._json(200, {"closed": True})
        if action == "undo":
            store.undo(doc)
        elif action == "redo":
            store.redo(doc)
        elif action == "save":
            if not self.server.desktop and (request.get("path") or doc.path):
                raise ValueError(t("salvare su disco è possibile solo nella finestra di wmt-pdf: usa «Scarica»"))
            store.save(doc, request.get("path"))
        else:
            return self._error(404, t("azione sconosciuta"))
        return self._json(200, store.state(doc))


def serve(workdir: Path, port: int = 0, open_window: bool = True) -> None:
    """Avvia il server e apre l'interfaccia: finestra nativa se c'è pywebview, altrimenti il browser."""
    server = make_server(workdir, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(t("wmt-pdf: interfaccia su {url} (Ctrl+C per chiudere)", url=url))
    try:
        if open_window:
            try:
                import webview  # pywebview, extra "gui"

                webview.settings["ALLOW_DOWNLOADS"] = True  # senza, «Scarica» non fa nulla nella finestra
                server.desktop = True
                api = DesktopApi(server)
                window = webview.create_window("wmt-pdf", url, width=1280, height=840, min_size=(900, 600),
                                               js_api=api)
                api.attach_window(window)
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


class DesktopApi:
    """Funzioni della finestra nativa chiamabili dalla pagina: finestre di dialogo vere per Apri e Salva con nome.

    I percorsi scelti qui finiscono nell'elenco di quelli ammessi dal server.
    """

    # Tutto ciò che non serve alla pagina va con «_»: pywebview espone (ed esplora ricorsivamente) gli attributi
    # pubblici di js_api, e la finestra ha migliaia di oggetti nativi: la pagina restava bloccata.
    _window: Any = None  # finestra di pywebview, assegnata dopo create_window

    _compact: Any = None  # barra compatta, quando c'è
    _compact_lock = threading.Lock()  # un doppio clic su «Compatta» non deve aprire due barre

    def __init__(self, server: GuiServer):
        self._server = server

    def attach_window(self, window) -> None:
        """Collega la finestra (non è un attributo pubblico: pywebview lo esplorerebbe all'infinito). Una volta sola."""
        if self._window is not None:
            return
        self._window = window
        window.events.closing += self._confirm_close

    def open_dialog(self):
        import webview

        types = ("Documenti PDF (*.pdf;*.p7m)",)
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=types)
        if not result:
            return None
        path = str(result[0])
        self._server.documents.allow(path)
        return path

    def save_dialog(self, name: str):
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=str(name or "documento.pdf"),
                                                file_types=("Documenti PDF (*.pdf)",))
        if not result:
            return None
        path = str(result if isinstance(result, str) else result[0])
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self._server.documents.allow(path)
        return path

    # --- barra compatta ---

    def _area(self, area, window) -> tuple[int, int, int, int]:
        """Area utile dello schermo: quella che dà la pagina (screen.avail*, già nelle unità di move e resize anche
        con lo schermo in scala), altrimenti quella del sistema."""
        import webview

        from wmt_pdf.gui.compact import parse_area, screen_of, work_area

        return parse_area(area) or work_area(screen_of(window, webview.screens))

    def compact(self, side: str = "top", area=None) -> str:
        """Nasconde la finestra principale e mostra la barra compatta sul lato scelto (top, right, bottom, left)."""
        import webview

        from wmt_pdf.gui.compact import SIDES, geometry

        side = side if side in SIDES else "top"
        with self._compact_lock:
            if self._compact is not None:
                self.dock(side, area)
            else:
                x, y, width, height = geometry(side, self._area(area, self._window))
                port = self._server.server_address[1]
                self._compact = webview.create_window(
                    "wmt-pdf", f"http://127.0.0.1:{port}/compact?side={side}", js_api=self, x=x, y=y, width=width,
                    height=height, frameless=True, easy_drag=False, on_top=True, resizable=False,
                    background_color="#161b26")
                self._compact.events.closed += self._compact_closed
        self._window.hide()
        return side

    def dock(self, side: str, area=None) -> str:
        """Sposta la barra compatta su un altro lato dello schermo in cui si trova."""
        from wmt_pdf.gui.compact import SIDES, geometry

        if self._compact is None or side not in SIDES:
            return ""
        x, y, width, height = geometry(side, self._area(area, self._compact))
        self._compact.resize(width, height)
        self._compact.move(x, y)
        return side

    def expand(self, action=None) -> None:
        """Torna alla finestra completa; con un'azione (aprire un file, uno strumento, i recenti) la esegue lì."""
        from wmt_pdf.gui.compact import clean_action, run_action_js

        cleaned = clean_action(action, set(TOOLS))  # prima di tutto: un'azione malformata non lascia nulla a metà
        compact, self._compact = self._compact, None
        self._window.show()
        if compact is not None:
            compact.destroy()
        if cleaned:
            self._window.evaluate_js(run_action_js(cleaned))

    def _compact_closed(self) -> None:
        # Chiusa con Alt+F4 o simili: la finestra principale non deve restare nascosta per sempre.
        if self._compact is not None:
            self._compact = None
            self._window.show()

    def _confirm_close(self):
        """Alla chiusura della finestra, conferma se ci sono modifiche non salvate (False annulla la chiusura)."""
        store = self._server.documents
        unsaved = [d.name for d in store.docs.values() if d.index != d.saved_index]
        if not unsaved:
            return True
        return self._window.create_confirmation_dialog(
            t("Modifiche non salvate"),
            t("{names}: le modifiche non salvate andranno perse. Chiudere?", names=", ".join(unsaved)))

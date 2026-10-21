"""Documenti aperti nell'interfaccia: versioni (annulla e ripeti), salvataggio, file recenti."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from wmt_pdf.source import CliError

MAX_RECENT = 15
MAX_VERSIONS = 50


@dataclass
class Document:
    id: str
    name: str
    path: Path | None  # file sul disco (finestra nativa); None se caricato dal browser
    versions: list[str] = field(default_factory=list)  # id dei file nella cartella di lavoro
    index: int = 0
    saved_index: int = 0
    origin: str | None = None  # file caricato da cui è stato aperto: non è del documento, resta alla chiusura


def _recent_path() -> Path:
    from wmt_pdf.trust import cache_dir

    return cache_dir() / "recenti.json"


def recent() -> list[dict]:
    try:
        items = json.loads(_recent_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    result = []
    for item in items if isinstance(items, list) else []:
        path = Path(str(item.get("path", "")))
        if path.is_file():  # i file spostati o cancellati spariscono dall'elenco
            result.append({"path": str(path), "name": path.name, "opened": item.get("opened")})
    return result[:MAX_RECENT]


def remember(path: Path) -> None:
    items = [r for r in recent() if Path(r["path"]) != path]
    items.insert(0, {"path": str(path), "name": path.name,
                     "opened": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
    target = _recent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(items[:MAX_RECENT], ensure_ascii=False, indent=1), encoding="utf-8")


def forget(path: str) -> None:
    items = [r for r in recent() if r["path"] != path]
    target = _recent_path()
    if target.parent.exists():
        target.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def check_local_path(raw_path: str, for_open: bool) -> Path:
    """Solo percorsi locali assoluti; niente percorsi di rete (su Windows aprirli invia le credenziali)."""
    text = str(raw_path or "")
    if text.startswith(("\\\\", "//")) or not text:
        raise ValueError("percorsi di rete non ammessi")
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("percorso non valido")
    if for_open:
        if path.suffix.lower() not in (".pdf", ".p7m"):
            raise ValueError("si aprono solo file PDF o p7m")
        if not path.is_file():
            raise ValueError("file non trovato")
    return path


class DocumentStore:
    def __init__(self, server):
        self.server = server
        self.docs: dict[str, Document] = {}
        self.lock = threading.RLock()  # il server risponde in parallelo: le versioni cambiano una alla volta
        # Percorsi che l'utente ha scelto nelle finestre di dialogo (o tra i recenti): solo questi si leggono o
        # scrivono. Così nemmeno un altro programma che conosca l'indirizzo locale può usare file a piacere.
        self.allowed: set[str] = set()

    def allow(self, path: str) -> None:
        self.allowed.add(str(Path(path).resolve()).casefold())

    def _is_allowed(self, path: Path) -> bool:
        key = str(path.resolve()).casefold()
        return key in self.allowed or any(str(Path(r["path"]).resolve()).casefold() == key for r in recent())

    def get(self, doc_id: str) -> Document:
        doc = self.docs.get(doc_id) if isinstance(doc_id, str) else None
        if doc is None:
            raise ValueError("documento sconosciuto")
        return doc

    def open_file(self, file_id: str) -> Document:
        path = self.server.resolve(file_id)
        doc = Document(uuid.uuid4().hex, path.name, None, [file_id], origin=file_id)
        self.docs[doc.id] = doc
        return doc

    def open_path(self, raw_path: str) -> Document:
        path = check_local_path(raw_path, for_open=True)
        if not self._is_allowed(path):
            raise ValueError("file non scelto dalla finestra «Apri» né tra i recenti")
        with path.open("rb") as f:
            head = f.read(1024)
        if path.suffix.lower() == ".pdf" and b"%PDF" not in head:
            raise ValueError("il file non è un PDF")
        if path.stat().st_size > 500 * 1024 * 1024:
            raise ValueError("file troppo grande")
        copy_dir = self.server.workdir / "file" / uuid.uuid4().hex
        copy_dir.mkdir(parents=True)
        copy = copy_dir / path.name
        shutil.copyfile(path, copy)  # si lavora su una copia: l'originale cambia solo con «Salva»
        info = self.server.register(copy)
        doc = Document(uuid.uuid4().hex, path.name, path, [info["id"]])
        self.docs[doc.id] = doc
        remember(path)
        return doc

    def close(self, doc: Document) -> None:
        """Chiude il documento: le sue versioni nella cartella di lavoro non servono più."""
        self.docs.pop(doc.id, None)
        for file_id in doc.versions:
            if file_id != doc.origin:
                self.server.discard(file_id)

    def current(self, doc: Document) -> Path:
        return self.server.resolve(doc.versions[doc.index])

    def state(self, doc: Document) -> dict:
        from wmt_pdf.source import load_source

        try:
            source = load_source(self.current(doc))
            pdf = source.open()
            pages = len(pdf)
            pdf.close()
        except CliError:
            pages = 0
        return {
            "id": doc.id, "name": doc.name, "path": str(doc.path) if doc.path else None,
            "file_id": doc.versions[doc.index], "pages": pages,
            "can_undo": doc.index > 0, "can_redo": doc.index < len(doc.versions) - 1,
            "modified": doc.index != doc.saved_index, "version": doc.index + 1,
        }

    def push(self, doc: Document, file_id: str) -> None:
        """Nuova versione dopo una modifica: le versioni «ripetibili» oltre la corrente si perdono, come ovunque."""
        if doc.saved_index > doc.index:
            doc.saved_index = -1  # la versione salvata era tra quelle annullate: ora nessuna corrisponde al file
        for dropped in doc.versions[doc.index + 1:]:
            self.server.discard(dropped)
        doc.versions = doc.versions[: doc.index + 1] + [file_id]
        if len(doc.versions) > MAX_VERSIONS:
            drop = len(doc.versions) - MAX_VERSIONS
            for dropped in doc.versions[:drop]:
                self.server.discard(dropped)
            doc.versions = doc.versions[drop:]
            doc.saved_index -= drop
        doc.index = len(doc.versions) - 1

    def undo(self, doc: Document) -> None:
        if doc.index > 0:
            doc.index -= 1

    def redo(self, doc: Document) -> None:
        if doc.index < len(doc.versions) - 1:
            doc.index += 1

    def save(self, doc: Document, raw_path: str | None) -> Path:
        if raw_path:
            target = check_local_path(raw_path, for_open=False)
            if not self._is_allowed(target):
                raise ValueError("percorso non scelto dalla finestra «Salva con nome»")
            if target.suffix.lower() != ".pdf":
                target = target.with_name(target.name + ".pdf")
        elif doc.path is not None:
            if doc.path.suffix.lower() != ".pdf":
                # Un .p7m è una busta firmata: riscriverlo con il PDF modificato cancellerebbe la firma.
                raise ValueError("il documento viene da un file firmato (.p7m): usa «Salva con nome» per un PDF nuovo")
            target = doc.path
        else:
            raise ValueError("serve un percorso: usa «Salva con nome»")
        tmp = target.with_name(f".{target.name}.wmt-pdf.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.current(doc), tmp)
            tmp.replace(target)  # sostituzione in un colpo: niente file a metà se qualcosa va storto
        except OSError as exc:
            reason = "il file è aperto in un altro programma o è in sola lettura" \
                if isinstance(exc, PermissionError) else exc.strerror or str(exc)
            raise ValueError(f"salvataggio non riuscito: {reason}") from None
        finally:
            tmp.unlink(missing_ok=True)
        doc.path, doc.name, doc.saved_index = target, target.name, doc.index
        remember(target)
        return target

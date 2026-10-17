"""Apertura dell'input: un PDF, oppure un .p7m che contiene un PDF."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from wmt_pdf.cades import P7mError, P7mResult, looks_like_cms, open_p7m


class CliError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


@dataclass
class Source:
    path: Path
    data: bytes
    p7m: P7mResult | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def open(self, password: str | None = None) -> pdfium.PdfDocument:
        try:
            return pdfium.PdfDocument(self.data, password=password)
        except pdfium.PdfiumError as exc:
            if "password" in str(exc).lower():
                raise CliError("PDF protetto da password: usa --password") from None
            raise CliError(f"PDF non leggibile: {exc}") from None


def read_file(path: str | Path) -> bytes:
    p = Path(path)
    if not p.is_file():
        raise CliError(f"file non trovato: {p}")
    return p.read_bytes()


def load_source(path: str | Path) -> Source:
    p = Path(path)
    data = read_file(p)
    if data.lstrip()[:5].startswith(b"%PDF"):
        return Source(p, data)
    if looks_like_cms(data):
        # Mai ripiegare sul PDF contenuto: un p7m rotto non va letto come se fosse a posto.
        try:
            result = open_p7m(data)
        except P7mError as exc:
            raise CliError(f"p7m non leggibile ({exc}): il contenuto non viene aperto") from None
        if result.content_kind != "pdf":
            raise CliError(
                f"il p7m contiene un file {result.content_kind}, non un PDF: estrailo con `wpdf p7m {p.name} -o <file>`"
            )
        return Source(p, result.content, result)
    if b"%PDF" in data[:1024]:  # PDF con qualche byte spurio in testa
        return Source(p, data)
    raise CliError(f"non è un PDF né un p7m: {p.name}")


def p7m_banner(source: Source) -> str | None:
    if source.p7m is None:
        return None
    signers = [s for layer in source.p7m.layers for s in layer]
    names = ", ".join(s.name or "?" for s in signers)
    state = "integro" if source.p7m.ok else "NON integro: controlla con `wpdf p7m`"
    return f"> PDF estratto dal p7m, firmato da {names} ({state}; certificatore non verificato)"

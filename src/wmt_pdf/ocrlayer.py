"""PDF ricercabile: strato di testo invisibile, dall'OCR, sopra le pagine scansionate."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

from wmt_pdf import ocr
from wmt_pdf.extract import is_scanned, page_stats
from wmt_pdf.ops import open_pdf, save
from wmt_pdf.source import CliError

DPI = ocr.OCR_DPI


def make_searchable(data: bytes, target: Path, *, lang: str = "it", force: bool = False,
                    password: str | None = None) -> list[int]:
    """Aggiunge il testo alle pagine scansionate (o a tutte con force). Restituisce le pagine trattate."""
    if not ocr.available():
        raise CliError("OCR non installato: `uv sync --extra ocr` (oppure pip install 'wmt-pdf[ocr]')")
    doc = pdfium.PdfDocument(data, password=password)
    layers: dict[int, bytes] = {}
    try:
        for n in range(1, len(doc) + 1):
            page = doc[n - 1]
            try:
                _, st = page_stats(page)
                if not (force or is_scanned(st.chars, st.images)):
                    continue
                layer = _text_layer(page, lang)
                if layer:
                    layers[n] = layer
            finally:
                page.close()
    finally:
        doc.close()
    with open_pdf(data, password) as pdf:
        for n, layer in layers.items():
            page = pdf.pages[n - 1]
            with pikepdf.open(io.BytesIO(layer)) as overlay:
                box = page.mediabox
                page.add_overlay(overlay.pages[0], pikepdf.Rectangle(box[0], box[1], box[2], box[3]))
        save(pdf, target)
    return sorted(layers)


def _text_layer(page: pdfium.PdfPage, lang: str) -> bytes | None:
    """Una pagina della stessa misura con il testo riconosciuto, invisibile, al suo posto."""
    import numpy as np
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    rotation = page.get_rotation()
    # Si lavora nello spazio della pagina senza la rotazione di visualizzazione.
    image = page.render(scale=DPI / 72, rotation=(360 - rotation) % 360).to_pil().convert("RGB")
    result = ocr.engine(lang)(np.asarray(image))
    if result.txts is None or result.boxes is None:
        return None
    left, _bottom, _right, top = page.get_cropbox()
    mleft, mbottom, mright, mtop = page.get_mediabox()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(mright - mleft, mtop - mbottom))
    font = "Helvetica"
    for box, text in zip(result.boxes, result.txts):
        if not text.strip():
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x = left + min(xs) * 72 / DPI - mleft
        y_top = top - min(ys) * 72 / DPI - mbottom
        height = (max(ys) - min(ys)) * 72 / DPI
        width = (max(xs) - min(xs)) * 72 / DPI
        size = max(1.0, height * 0.85)
        natural = stringWidth(text, font, size) or 1
        t = c.beginText()
        t.setTextRenderMode(3)  # invisibile ma selezionabile e ricercabile
        t.setFont(font, size)
        t.setHorizScale(100 * width / natural)
        t.setTextOrigin(x, y_top - height * 0.8)
        t.textOut(text)
        c.drawText(t)
    c.showPage()
    c.save()
    return buf.getvalue()

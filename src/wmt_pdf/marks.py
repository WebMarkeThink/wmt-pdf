"""Filigrane (aggiunta e rimozione), evidenziazioni e annotazioni."""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

from wmt_pdf.i18n import N_, t
from wmt_pdf.ops import open_pdf, save
from wmt_pdf.source import CliError

# Marcatura standard (ISO 32000, 14.8.2.2) che usano anche Acrobat e gli altri editor.
WATERMARK_BDC = b"/Artifact <</Type /Pagination /Subtype /Watermark>> BDC\n"

COLORS = {
    "giallo": (1, 0.92, 0.23), "verde": (0.56, 0.93, 0.56), "azzurro": (0.53, 0.81, 0.98),
    "rosa": (1, 0.6, 0.8), "arancione": (1, 0.72, 0.3),
    "yellow": (1, 0.92, 0.23), "green": (0.56, 0.93, 0.56), "blue": (0.53, 0.81, 0.98), "pink": (1, 0.6, 0.8),
}

ANNOT_NAMES = {
    "Highlight": N_("Evidenziazione"), "Underline": N_("Sottolineatura"), "StrikeOut": N_("Barrato"), "Squiggly": N_("Ondulata"),
    "Text": N_("Nota"), "FreeText": N_("Testo"), "Square": N_("Rettangolo"), "Circle": N_("Cerchio"), "Ink": N_("Disegno"),
    "Stamp": N_("Timbro"), "Line": N_("Linea"), "Polygon": N_("Poligono"), "FileAttachment": N_("Allegato"), "Watermark": N_("Filigrana"),
    "Link": N_("Collegamento"), "Widget": N_("Campo modulo"), "Popup": N_("Popup"),
}


# --- filigrane ------------------------------------------------------------------


def _overlay_pdf(width: float, height: float, *, text: str | None, image: Path | None, opacity: float,
                 angle: float, size: float | None, color: tuple[float, float, float]) -> bytes:
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.saveState()
    c.setFillAlpha(opacity)
    c.setStrokeAlpha(opacity)
    c.translate(width / 2, height / 2)
    c.rotate(angle)
    if text:
        font = "Helvetica-Bold"
        fsize = size or _fit_font(text, font, (width**2 + height**2) ** 0.5 * 0.7)
        c.setFont(font, fsize)
        c.setFillColorRGB(*color)
        c.drawCentredString(0, -fsize / 3, text)
    if image:
        reader = ImageReader(str(image))
        iw, ih = reader.getSize()
        scale = min(width * 0.6 / iw, height * 0.6 / ih)
        c.drawImage(reader, -iw * scale / 2, -ih * scale / 2, iw * scale, ih * scale, mask="auto")
    c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


def _fit_font(text: str, font: str, length: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return max(12.0, min(160.0, length / max(1.0, stringWidth(text, font, 1))))


def add_watermark(data: bytes, target: Path, *, text: str | None = None, image: Path | None = None,
                  opacity: float = 0.15, angle: float = 45, size: float | None = None,
                  color: str = "grigio", numbers: list[int] | None = None, password: str | None = None) -> int:
    if not text and not image:
        raise CliError(t("indica --text oppure --image"))
    if not 0 < opacity <= 1:
        raise CliError(t("--opacity tra 0 e 1"))
    rgb = (0.45, 0.45, 0.45) if color == "grigio" else COLORS.get(color, (0.45, 0.45, 0.45))
    cache: dict[tuple[float, float], pikepdf.Pdf] = {}
    with open_pdf(data, password) as pdf:
        count = 0
        try:
            for n, page in enumerate(pdf.pages, start=1):
                if numbers and n not in numbers:
                    continue
                box = page.mediabox
                w, h = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
                key = (round(w, 1), round(h, 1))
                if key not in cache:
                    overlay = pikepdf.open(io.BytesIO(_overlay_pdf(
                        w, h, text=text, image=image, opacity=opacity, angle=angle, size=size, color=rgb)))
                    first = overlay.pages[0]
                    first.contents_coalesce()
                    stream = first.obj.Contents
                    stream.write(WATERMARK_BDC + stream.read_bytes() + b"\nEMC\n")
                    cache[key] = overlay
                page.add_overlay(cache[key].pages[0], pikepdf.Rectangle(box[0], box[1], box[2], box[3]))
                count += 1
            save(pdf, target)
        finally:
            for overlay in cache.values():
                overlay.close()
    return count


def remove_watermarks(data: bytes, target: Path, password: str | None = None) -> int:
    """Toglie filigrane marcate come tali; restituisce quante pagine ne avevano."""
    with open_pdf(data, password) as pdf:
        pages_changed = 0
        for page in pdf.pages:
            changed = _strip_watermark_content(pdf, page)
            changed |= _drop_watermark_annots(page)
            pages_changed += changed
        pdf.remove_unreferenced_resources()
        save(pdf, target)
    return pages_changed


def _is_watermark_bdc(operands) -> bool:
    if len(operands) < 2 or operands[0] != pikepdf.Name.Artifact:
        return False
    props = operands[1]
    return isinstance(props, pikepdf.Dictionary) and props.get("/Subtype") == pikepdf.Name.Watermark


NEUTRAL_OPS = {"q", "Q", "cm", "gs"}  # operatori che non disegnano nulla


def _filter_watermarks(instructions) -> tuple[list, bool, bool]:
    """Toglie i blocchi /Artifact /Watermark BDC ... EMC.

    Restituisce (istruzioni rimaste, se ha tolto qualcosa, se i marcatori erano bilanciati).
    Le immagini incorporate nel flusso (BI ... EI) restano come sono.
    """
    kept, depth, skipping, removed = [], 0, None, False
    for item in instructions:
        if isinstance(item, pikepdf.ContentStreamInlineImage):
            if skipping is None:
                kept.append(item)
            continue
        op = str(item.operator)
        if skipping is not None:
            if op in ("BDC", "BMC"):
                depth += 1
            elif op == "EMC":
                if depth == skipping:
                    skipping = None
                depth -= 1
            continue
        if op == "BDC" and _is_watermark_bdc(list(item.operands)):
            depth += 1
            skipping = depth
            removed = True
            continue
        if op in ("BDC", "BMC"):
            depth += 1
        elif op == "EMC":
            depth -= 1
        kept.append(item)
    return kept, removed, skipping is None and depth == 0


def _xobject_is_watermark(xobj: pikepdf.Object) -> bool:
    """Vero solo se l'oggetto è tutto filigrana: un contenitore che la include tra altro non si tocca."""
    if xobj.get("/Subtype") != pikepdf.Name.Form:
        return False
    piece = xobj.get("/PieceInfo")
    if (piece is not None and "/ADBE_CompoundType" in piece
            and piece.ADBE_CompoundType.get("/Private") == pikepdf.Name.Watermark):
        return True
    try:
        kept, removed, balanced = _filter_watermarks(pikepdf.parse_content_stream(xobj))
    except pikepdf.PdfError:
        return False
    return removed and balanced and all(
        not isinstance(i, pikepdf.ContentStreamInlineImage) and str(i.operator) in NEUTRAL_OPS for i in kept
    )


def _strip_watermark_content(pdf: pikepdf.Pdf, page: pikepdf.Page) -> bool:
    xobjects = page.obj.get("/Resources", {}).get("/XObject", {})
    marked = {name for name, xobj in xobjects.items() if _xobject_is_watermark(xobj)}
    kept, removed, balanced = _filter_watermarks(pikepdf.parse_content_stream(page))
    if not balanced:
        return False  # marcatori senza chiusura: meglio lasciare la pagina com'è che perdere contenuto
    final = []
    for item in kept:
        if (not isinstance(item, pikepdf.ContentStreamInlineImage) and str(item.operator) == "Do"
                and item.operands and str(item.operands[0]) in marked):
            removed = True
            continue
        final.append(item)
    if removed:
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(final))
    return removed


def _drop_watermark_annots(page: pikepdf.Page) -> bool:
    annots = page.obj.get("/Annots")
    if not annots:
        return False
    keep = [a for a in annots if a.get("/Subtype") != pikepdf.Name.Watermark]
    if len(keep) == len(annots):
        return False
    page.obj.Annots = pikepdf.Array(keep)
    return True


# --- evidenziazioni ---------------------------------------------------------------


def find_rects(data: bytes, query: str, password: str | None = None,
               numbers: list[int] | None = None) -> dict[int, list[list[tuple[float, float, float, float]]]]:
    """Per ogni pagina, i riquadri di ogni occorrenza (una riga = un riquadro)."""
    doc = pdfium.PdfDocument(data, password=password)
    found: dict[int, list[list[tuple[float, float, float, float]]]] = {}
    try:
        for n in range(1, len(doc) + 1):
            if numbers and n not in numbers:
                continue
            page = doc[n - 1]
            textpage = page.get_textpage()
            try:
                searcher = textpage.search(query, match_case=False, match_whole_word=False)
                while (hit := searcher.get_next()) is not None:
                    start, count = hit
                    found.setdefault(n, []).append(_line_rects(textpage, start, count))
                searcher.close()
            finally:
                textpage.close()
                page.close()
    finally:
        doc.close()
    return found


def _line_rects(textpage, start: int, count: int) -> list[tuple[float, float, float, float]]:
    lines: list[list[float]] = []
    for i in range(start, start + count):
        left, bottom, right, top = textpage.get_charbox(i, loose=True)
        if right - left <= 0:
            continue  # spazi e a capo
        if lines and abs(lines[-1][1] - bottom) < (top - bottom) * 0.5:
            line = lines[-1]
            line[0], line[1] = min(line[0], left), min(line[1], bottom)
            line[2], line[3] = max(line[2], right), max(line[3], top)
        else:
            lines.append([left, bottom, right, top])
    return [(a - 1, b - 1.5, c + 1, d + 1.5) for a, b, c, d in lines]


def add_highlights(data: bytes, target: Path, query: str, *, color: str = "giallo", note: str | None = None,
                   author: str = "wmt-pdf", numbers: list[int] | None = None, password: str | None = None) -> int:
    rgb = COLORS.get(color)
    if rgb is None:
        raise CliError(t("colore sconosciuto: {color} (scegli tra {join})", color=color, join=', '.join(sorted(COLORS))))
    hits = find_rects(data, query, password, numbers)
    total = sum(len(v) for v in hits.values())
    if not total:
        return 0
    now = pikepdf.String(dt.datetime.now().astimezone().strftime("D:%Y%m%d%H%M%S"))
    with open_pdf(data, password) as pdf:
        for n, occurrences in hits.items():
            page = pdf.pages[n - 1]
            for rects in occurrences:
                annot = _highlight_annot(pdf, rects, rgb, note, author, now)
                annot.P = page.obj
                if "/Annots" not in page.obj:
                    page.obj.Annots = pikepdf.Array()
                page.obj.Annots.append(annot)
        save(pdf, target)
    return total


def _highlight_annot(pdf: pikepdf.Pdf, rects, rgb, note, author, now) -> pikepdf.Object:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects)
    y1 = max(r[3] for r in rects)
    quads: list[float] = []
    for left, bottom, right, top in rects:
        quads += [left, top, right, top, left, bottom, right, bottom]
    # Aspetto esplicito: non tutti i visualizzatori lo generano da soli.
    fill = " ".join(f"{left - x0:.2f} {bottom - y0:.2f} {right - left:.2f} {top - bottom:.2f} re" for left, bottom, right, top in rects)
    appearance = pdf.make_stream(
        f"/GS0 gs {rgb[0]} {rgb[1]} {rgb[2]} rg {fill} f".encode(),
        Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, x1 - x0, y1 - y0],
        Matrix=[1, 0, 0, 1, 0, 0],
        Resources=pikepdf.Dictionary(ExtGState=pikepdf.Dictionary(
            GS0=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, BM=pikepdf.Name.Multiply))),
    )
    annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Highlight, Rect=[x0, y0, x1, y1],
        QuadPoints=quads, C=list(rgb), CA=0.5, F=4, T=pikepdf.String(author), M=now,
        AP=pikepdf.Dictionary(N=appearance),
    )
    if note:
        annot.Contents = pikepdf.String(note)
    return pdf.make_indirect(annot)


# --- annotazioni ---------------------------------------------------------------------

HIDDEN_BY_DEFAULT = {"Link", "Widget", "Popup"}


@dataclass
class Annotation:
    page: int
    type: str
    label: str
    author: str | None
    contents: str | None
    text: str | None
    modified: str | None


def list_annotations(data: bytes, include_all: bool = False, password: str | None = None) -> list[Annotation]:
    result = []
    doc = pdfium.PdfDocument(data, password=password)
    try:
        with open_pdf(data, password) as pdf:
            for n, page in enumerate(pdf.pages, start=1):
                annots = page.obj.get("/Annots") or []
                textpage = None
                for a in annots:
                    kind = str(a.get("/Subtype", "/?"))[1:]
                    if kind in HIDDEN_BY_DEFAULT and not include_all:
                        continue
                    text = None
                    if kind in ("Highlight", "Underline", "StrikeOut", "Squiggly") and "/QuadPoints" in a:
                        if textpage is None:
                            textpage = doc[n - 1].get_textpage()
                        text = _text_under(textpage, [float(v) for v in a.QuadPoints])
                    result.append(Annotation(
                        page=n, type=kind, label=t(ANNOT_NAMES.get(kind, kind)),
                        author=_s(a.get("/T")), contents=_s(a.get("/Contents")), text=text, modified=_s(a.get("/M")),
                    ))
                if textpage is not None:
                    textpage.close()
    finally:
        doc.close()
    return result


def _text_under(textpage, quads: list[float]) -> str:
    parts = []
    for i in range(0, len(quads) - 7, 8):
        xs, ys = quads[i:i + 8:2], quads[i + 1:i + 8:2]
        parts.append(textpage.get_text_bounded(min(xs), min(ys), max(xs), max(ys)).strip())
    return " ".join(p for p in parts if p)


def _s(value) -> str | None:
    return str(value) if value is not None else None


def remove_annotations(data: bytes, target: Path, types: set[str] | None = None, password: str | None = None) -> int:
    """Toglie le annotazioni (di default tutte tranne collegamenti e campi modulo)."""
    removed = 0
    with open_pdf(data, password) as pdf:
        for page in pdf.pages:
            annots = page.obj.get("/Annots")
            if not annots:
                continue
            keep = []
            for a in annots:
                kind = str(a.get("/Subtype", "/?"))[1:]
                drop = kind in types if types else kind not in {"Link", "Widget"}
                if drop:
                    removed += 1
                else:
                    keep.append(a)
            page.obj.Annots = pikepdf.Array(keep)
        save(pdf, target)
    return removed

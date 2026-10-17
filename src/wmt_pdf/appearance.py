"""Aspetto dei campi di testo e degli elenchi, con le lettere accentate al loro posto.

Il generatore di pikepdf scrive «?» al posto di à, è, €: qui il testo si codifica in WinAnsi (cp1252),
che con Helvetica copre tutto l'italiano. Un testo con caratteri fuori da cp1252 non ha un aspetto
affidabile: lo si segnala, e i visualizzatori lo rigenerano grazie a NeedAppearances.
"""

from __future__ import annotations

import re

import pikepdf

FONT_NAME = "/WmtHelv"
PADDING = 2.0
MULTILINE = 1 << 12
COMB = 1 << 24


def encodable(text: str) -> bool:
    try:
        text.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _pdf_string(text: str) -> bytes:
    raw = text.encode("cp1252")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _font(pdf: pikepdf.Pdf) -> pikepdf.Object:
    return pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
        Encoding=pikepdf.Name.WinAnsiEncoding))


def _font_size(widget: pikepdf.Object, field: pikepdf.Object) -> float:
    da = widget.get("/DA") or field.get("/DA") or ""
    match = re.search(r"([\d.]+)\s+Tf", str(da))
    return float(match.group(1)) if match else 0.0


def _width(text: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, "Helvetica", size)


def _wrap(text: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if current and _width(candidate, size) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def text_appearance(pdf: pikepdf.Pdf, widget: pikepdf.Object, field: pikepdf.Object, text: str,
                    font: pikepdf.Object | None = None) -> pikepdf.Object:
    """Flusso d'aspetto /N per un widget di testo: corpo dal /DA (0 = adatta), una o più righe."""
    x0, y0, x1, y1 = (float(v) for v in widget.Rect)
    w, h = abs(x1 - x0), abs(y1 - y0)
    flags = int(field.get("/Ff", 0))
    size = _font_size(widget, field)
    inner_w = max(1.0, w - 2 * PADDING)
    if flags & MULTILINE:
        size = size or 10.0
        lines = _wrap(text, size, inner_w)
        leading = size * 1.15
        y = h - PADDING - size
        body = []
        for line in lines:
            if y < 0:
                break
            body.append(f"1 0 0 1 {PADDING:.2f} {y:.2f} Tm ".encode() + _pdf_string(line) + b" Tj")
            y -= leading
        content = b" ".join(body)
    else:
        line = text.replace("\n", " ")
        if not size:
            size = min(h * 0.7, 12.0)
            natural = _width(line, size)
            if natural > inner_w:
                size = max(4.0, size * inner_w / natural)
        baseline = (h - size * 0.72) / 2
        content = f"1 0 0 1 {PADDING:.2f} {baseline:.2f} Tm ".encode() + _pdf_string(line) + b" Tj"
    stream = (b"/Tx BMC q BT " + FONT_NAME.encode() + f" {size:.2f} Tf 0 g ".encode() + content
              + b" ET Q EMC")
    return pdf.make_stream(
        stream, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, w, h],
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary({FONT_NAME: font or _font(pdf)})))


def widgets_of(field: pikepdf.Object) -> list[pikepdf.Object]:
    if "/Kids" in field:
        return [k for k in field.Kids if k.get("/Subtype") == pikepdf.Name.Widget]
    return [field] if field.get("/Subtype") == pikepdf.Name.Widget else []


def refresh_text_appearances(pdf: pikepdf.Pdf) -> list[str]:
    """Rifà l'aspetto di tutti i campi testo ed elenco dal loro valore. Restituisce i campi non rappresentabili."""
    if "/AcroForm" not in pdf.Root:
        return []
    font = _font(pdf)
    problems = []
    for f in pdf.acroform.fields:
        kind = f.field_type
        if kind not in (pikepdf.Name.Tx, pikepdf.Name.Ch):
            continue
        value = f.obj.get("/V")
        if value is None and "/Parent" in f.obj:
            value = f.obj.Parent.get("/V")
        if value is None:
            continue
        if isinstance(value, pikepdf.Array):  # elenco a scelta multipla: si mostrano tutte
            text = ", ".join(str(v) for v in value)
        else:
            text = str(value)
        if kind == pikepdf.Name.Ch:
            text = _display_value(f.obj, text)
        if not encodable(text):
            problems.append(f.fully_qualified_name)
            continue
        for widget in widgets_of(f.obj):
            if "/Rect" not in widget:
                continue
            widget.AP = pikepdf.Dictionary(N=text_appearance(pdf, widget, f.obj, text, font))
    return problems


def _display_value(field: pikepdf.Object, export: str) -> str:
    """Per gli elenchi con coppie [valore, etichetta] si mostra l'etichetta."""
    for opt in field.get("/Opt") or []:
        if isinstance(opt, pikepdf.Array) and len(opt) == 2 and str(opt[0]) == export:
            return str(opt[1])
    return export

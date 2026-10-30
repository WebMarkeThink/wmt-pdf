"""PDF ricercabile: strato di testo invisibile, dall'OCR, sopra le pagine scansionate."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from wmt_pdf import ocr
from wmt_pdf.extract import is_scanned, page_stats
from wmt_pdf.forms import has_signatures
from wmt_pdf.i18n import t
from wmt_pdf.ops import open_pdf, save
from wmt_pdf.source import CliError

DPI = ocr.OCR_DPI
EDITED = "/WmtScanEdited"  # sulla pagina: una riga è stata riscritta (ora ha testo visibile, ma resta una scansione)


def upright_pages(data: bytes) -> bytes:
    """Le pagine con /Rotate diventano diritte nel file: a video non cambia nulla, ma lo spazio del PDF è quello visto.

    Le scansioni di lato hanno il testo di traverso nello spazio del PDF: lo strato OCR, che scrive in orizzontale,
    finirebbe fuori posto, e con lui selezione, ricerca e modifica.
    """
    with pikepdf.open(io.BytesIO(data)) as pdf:
        changed = False
        for page in pdf.pages:
            angle = int(page.obj.get("/Rotate", 0)) % 360
            # Timbri e note hanno un aspetto disegnato per la pagina ruotata: non si possono raddrizzare senza deformarli.
            kinds = {annot.get("/Subtype") for annot in page.obj.get("/Annots") or []}
            if angle and kinds <= {pikepdf.Name.Link}:
                _make_upright(pdf, page, angle)
                changed = True
        if not changed:
            return data
        out = io.BytesIO()
        pdf.save(out, encryption=pdf.is_encrypted)  # un PDF con limiti e senza password d'apertura resta com'è
        return out.getvalue()


def _make_upright(pdf: pikepdf.Pdf, page: pikepdf.Page, angle: int) -> None:
    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    w, h = x1 - x0, y1 - y0
    # Da spazio del PDF a pagina diritta, con l'origine in basso a sinistra: (a, b, c, d, e, f).
    matrix = {
        90: (0, -1, 1, 0, -y0, w + x0),
        180: (-1, 0, 0, -1, w + x0, h + y0),
        270: (0, 1, -1, 0, h + y0, -x0),
    }[angle]
    a, b, c, d, e, f = matrix

    def moved(box) -> list[float]:
        bx0, by0, bx1, by1 = (float(v) for v in box)
        xs = [a * x + c * y + e for x, y in ((bx0, by0), (bx1, by1))]
        ys = [b * x + d * y + f for x, y in ((bx0, by0), (bx1, by1))]
        return [min(xs), min(ys), max(xs), max(ys)]

    crop = moved(page.cropbox)
    _seal_content(pdf, page, f"{a} {b} {c} {d} {e} {f} cm\n".encode())
    for annot in page.obj.get("/Annots") or []:
        if "/Rect" in annot:
            annot.Rect = moved(annot.Rect)
        if "/QuadPoints" in annot:
            del annot["/QuadPoints"]
    page.mediabox = moved(page.mediabox)
    page.cropbox = crop
    del page.obj["/Rotate"]


def scan_status(data: bytes, password: str | None = None) -> dict:
    """Quali pagine sono solo immagini, e se l'OCR si può proporre: non sui PDF firmati, le firme non varrebbero più."""
    doc = pdfium.PdfDocument(data, password=password)
    pages: list[int] = []
    try:
        total = len(doc)
        for n in range(1, total + 1):
            page = doc[n - 1]
            try:
                _, st = page_stats(page)
                if is_scanned(st.chars, st.images):
                    pages.append(n)
            finally:
                page.close()
    finally:
        doc.close()
    with open_pdf(data, password) as pdf:
        signed = has_signatures(pdf)
    return {"pages": pages, "total": total, "available": ocr.available(), "signed": signed}


def scan_lines(data: bytes, page_no: int, password: str | None = None) -> dict:
    """Per «Modifica testo» sulle scansioni: la pagina è un'immagine? E le sue righe di testo (dall'OCR), con la posizione.

    I riquadri sono in punti PDF. Il testo si legge con pdfium: vale per l'OCR di wmt-pdf e per quello di altri programmi.
    """
    doc = pdfium.PdfDocument(data, password=password)
    try:
        if not 1 <= page_no <= len(doc):
            raise CliError(t("pagina {page_no} fuori dal documento", page_no=page_no))
        page = doc[page_no - 1]
        try:
            with open_pdf(data, password) as pdf:
                edited = EDITED in pdf.pages[page_no - 1].obj
            if not _image_page(page, edited):
                return {"image_page": False, "lines": []}
            textpage = page.get_textpage()
            lines = []
            for i in range(textpage.count_rects()):
                left, bottom, right, top = textpage.get_rect(i)
                text = textpage.get_text_bounded(left, bottom, right, top).strip()
                if text:
                    lines.append({"index": i, "text": text, "bbox": [round(v, 2) for v in (left, bottom, right, top)]})
            return {"image_page": True, "lines": lines}
        finally:
            page.close()
    finally:
        doc.close()


def _image_page(page: pdfium.PdfPage, edited: bool = False) -> bool:
    """La pagina è una scansione: un'immagine la copre e il testo, se c'è, è quello invisibile dell'OCR.

    Un PDF digitale con uno sfondo a tutta pagina ha testo visibile: lì «Modifica testo» lavora sul testo vero.
    """
    width, height = page.get_size()
    area = (width * height) or 1
    covered = False
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
        left, bottom, right, top = obj.get_bounds()
        if (right - left) * (top - bottom) >= area * 0.6:
            covered = True
            break
    if not covered or edited:
        return covered  # le righe già cambiate qui sono visibili: la pagina è marcata
    hidden = visible = 0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT], max_depth=3):
        if pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw) == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE:
            hidden += 1
        else:
            visible += 1
    return hidden >= visible


def edit_scan_line(data: bytes, target: Path, page_no: int, rect: list[float], text: str,
                   password: str | None = None) -> None:
    """Cambia una riga di una scansione: la copre col colore dello sfondo e scrive il testo nuovo al suo posto.

    Vuoto: la riga sparisce. Il testo di prima (invisibile dell'OCR o di una modifica precedente) si toglie, così
    non resta nella ricerca né nella copia; per l'OCR di altri programmi può non riuscire.
    """
    from wmt_pdf.textedit import add_text, font_width, refuse_signed

    refuse_signed(data)
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        raise CliError(t("riquadro non valido"))
    with open_pdf(data, password) as pdf:
        if not 1 <= page_no <= len(pdf.pages):
            raise CliError(t("pagina {page_no} fuori dal documento", page_no=page_no))
        if int(pdf.pages[page_no - 1].obj.get("/Rotate", 0)) % 360:
            # Testo riconosciuto da un altro programma su una pagina di lato: si scriverebbe di traverso.
            raise CliError(t("pagina ruotata: rifai il riconoscimento del testo con «Rendi ricercabile» (Anche le pagine con testo)"))
    background, ink = _line_colors(data, page_no, rect, password)
    with open_pdf(data, password) as pdf:
        page = pdf.pages[page_no - 1]
        _drop_old_text(pdf, page, rect)
        if EDITED not in page.obj:  # una volta sola: le altre aggiunte vengono dopo un contenuto già chiuso
            _seal_content(pdf, page)
            page.obj[EDITED] = True
        r, g, b = (c / 255 for c in background)
        cover = f"q {r:.3f} {g:.3f} {b:.3f} rg {x0 - 1:.2f} {y0 - 1:.2f} {x1 - x0 + 2:.2f} {y1 - y0 + 2:.2f} re f Q\n"
        page.contents_add(pdf.make_stream(cover.encode()))
        save(pdf, target)
    if not text.strip():
        return
    data = target.read_bytes()
    height = y1 - y0
    size = max(6.0, height * 1.25)  # il riquadro dell'OCR va dalla base all'altezza delle maiuscole: il corpo è di più
    width = font_width(text, size)
    if width > (x1 - x0) * 1.02:  # più lungo della riga vecchia: un po' più piccolo, finché resta leggibile
        size = max(6.0, size * (x1 - x0) / width)
    add_text(data, target, page_no, x0, y0 + height * 0.2, text, size=size, color=ink, password=password)


def _seal_content(pdf: pikepdf.Pdf, page: pikepdf.Page, prefix: bytes = b"") -> None:
    """Il contenuto della pagina tra q e Q bilanciati: quel che segue non eredita trasformazioni o colori.

    Le Q di troppo si tolgono e quelle che mancano si aggiungono: altrimenti chiuderebbero la q aggiunta qui.
    """
    depth = 0
    kept = []
    for ins in pikepdf.parse_content_stream(page):
        if not isinstance(ins, pikepdf.ContentStreamInlineImage):
            name = str(ins.operator)
            if name == "q":
                depth += 1
            elif name == "Q":
                if not depth:
                    continue
                depth -= 1
        kept.append(ins)
    page.obj.Contents = pdf.make_stream(b"q\n" + prefix + pikepdf.unparse_content_stream(kept) + b"\n" + b"Q\n" * (depth + 1))


def _line_colors(data: bytes, page_no: int, rect: list[float], password: str | None) -> tuple[tuple, tuple]:
    """Colore dello sfondo (una cornice intorno alla riga) e dell'inchiostro (i punti più lontani dallo sfondo)."""
    import numpy as np

    doc = pdfium.PdfDocument(data, password=password)
    try:
        page = doc[page_no - 1]
        try:
            scale = 2.0
            left, _, _, top = page.get_cropbox()  # l'immagine è il riquadro visibile, non il MediaBox
            image = np.asarray(page.render(scale=scale, may_draw_forms=False).to_pil().convert("RGB")).astype(int)
        finally:
            page.close()
    finally:
        doc.close()
    x0, y0, x1, y1 = rect
    pad = 8  # pixel, a questa scala: 4 punti
    a, b = int((x0 - left) * scale), int((x1 - left) * scale)
    c, d = int((top - y1) * scale), int((top - y0) * scale)
    h, w = image.shape[:2]
    a, b, c, d = max(a, 0), min(b, w), max(c, 0), min(d, h)
    if a >= b or c >= d:
        return (255, 255, 255), (0, 0, 0)
    outer = image[max(c - pad, 0):min(d + pad, h), max(a - pad, 0):min(b + pad, w)]
    # Una cornice stretta a qualche punto dalla riga: più vicino ci sono le lettere che escono dal riquadro dell'OCR.
    edge = np.concatenate([part.reshape(-1, 3) for part in (outer[:2], outer[-2:], outer[:, :2], outer[:, -2:])])
    background = np.median(edge, axis=0)
    inner = image[c:d, a:b].reshape(-1, 3)
    distance = np.abs(inner - background).sum(axis=1)
    strong = inner[distance >= max(60, np.percentile(distance, 95) * 0.8)]
    ink = np.median(strong, axis=0) if len(strong) else np.zeros(3)
    return tuple(int(v) for v in background), tuple(int(v) for v in ink)


def _drop_old_text(pdf: pikepdf.Pdf, page: pikepdf.Page, rect: list[float]) -> None:
    """Toglie il testo che la riga aveva prima: quello invisibile dell'OCR e quello di una modifica precedente."""
    _drop_edited_blocks(pdf, page, rect)
    resources = page.resources
    xobjects = resources.get("/XObject") or {}
    placed = _form_placements(page)
    replaced = {}
    for key in list(xobjects.keys()):
        form = xobjects[key]
        if form.get("/Subtype") != pikepdf.Name.Form:
            continue
        # Lo strato OCR è una forma posata sulla pagina: il riquadro va portato nelle sue coordinate.
        sx, sy, dx, dy = placed.get(str(key), (1.0, 1.0, 0.0, 0.0))
        inner = [(rect[0] - dx) / sx, (rect[1] - dy) / sy, (rect[2] - dx) / sx, (rect[3] - dy) / sy]
        kept, changed = _without_hidden_lines(form, inner)
        if not changed:
            continue
        # Una copia: la stessa forma può servire ad altre pagine (pagina duplicata), che non vanno toccate.
        copy = pdf.make_stream(pikepdf.unparse_content_stream(kept))
        for name, value in form.items():
            if name not in ("/Length", "/Filter", "/DecodeParms"):
                copy[name] = value
        replaced[key] = copy
    if replaced:
        new_resources = pikepdf.Dictionary(resources)
        new_xobjects = pikepdf.Dictionary(xobjects)
        for key, copy in replaced.items():
            new_xobjects[key] = pdf.make_indirect(copy)
        new_resources.XObject = new_xobjects
        page.obj.Resources = new_resources


def _form_placements(page: pikepdf.Page) -> dict[str, tuple[float, float, float, float]]:
    """Per ogni forma disegnata sulla pagina: scala e spostamento del «cm» che la precede (sx, sy, dx, dy)."""
    placed: dict[str, tuple[float, float, float, float]] = {}
    last = (1.0, 1.0, 0.0, 0.0)
    for ins in pikepdf.parse_content_stream(page):
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            continue
        name = str(ins.operator)
        if name == "cm":
            a, _b, _c, d, e, f = (float(v) for v in ins.operands)
            last = (a or 1.0, d or 1.0, e, f)
        elif name == "Do":
            placed[str(ins.operands[0])] = last
        elif name in ("q", "Q"):
            last = (1.0, 1.0, 0.0, 0.0)
    return placed


def _without_hidden_lines(form: pikepdf.Object, rect: list[float]) -> tuple[list, bool]:
    kept, block, changed = [], None, False
    for ins in pikepdf.parse_content_stream(form):
        name = "" if isinstance(ins, pikepdf.ContentStreamInlineImage) else str(ins.operator)
        if name == "BT":
            block = [ins]
        elif block is not None:
            block.append(ins)
            if name == "ET":
                if _hidden_in(block, rect):
                    changed = True
                else:
                    kept.extend(block)
                block = None
        else:
            kept.append(ins)
    return kept, changed


def _hidden_in(block: list, rect: list[float]) -> bool:
    x0, y0, x1, y1 = rect
    hidden, origin = False, None
    for ins in block:
        name = str(ins.operator)
        if name == "Tr" and int(ins.operands[0]) == 3:
            hidden = True
        elif name == "Tm":
            origin = (float(ins.operands[4]), float(ins.operands[5]))
    return hidden and origin is not None and x0 - 3 <= origin[0] <= x1 + 3 and y0 - 3 <= origin[1] <= y1 + 3


def _drop_edited_blocks(pdf: pikepdf.Pdf, page: pikepdf.Page, rect: list[float]) -> None:
    """Il testo di una modifica precedente: un blocco breve «q ... cm ... BT ... ET Q» che comincia nel riquadro."""
    if EDITED not in page.obj:
        return
    x0, y0, x1, y1 = rect
    instructions = list(pikepdf.parse_content_stream(page))
    names = ["" if isinstance(i, pikepdf.ContentStreamInlineImage) else str(i.operator) for i in instructions]
    drop: set[int] = set()
    stack: list[int] = []
    for n, name in enumerate(names):
        if name == "q":
            stack.append(n)
        elif name == "Q" and stack:
            start = stack.pop()
            if n - start > 30 or "BT" not in names[start:n] or "cm" not in names[start:n]:
                continue
            operands = instructions[start + names[start:n].index("cm")].operands
            e, f = float(operands[4]), float(operands[5])
            if x0 - 3 <= e <= x1 + 3 and y0 - 3 <= f <= y1 + 3:
                drop.update(range(start, n + 1))
    if drop:
        kept = [ins for n, ins in enumerate(instructions) if n not in drop]
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(kept))


def make_searchable(data: bytes, target: Path, *, lang: str = "it", force: bool = False,
                    password: str | None = None) -> list[int]:
    """Aggiunge il testo alle pagine scansionate (o a tutte con force). Restituisce le pagine trattate."""
    if not ocr.available():
        raise CliError(t("OCR non installato: `uv sync --extra ocr` (oppure pip install 'wmt-pdf[ocr]')"))
    if password is None:
        data = upright_pages(data)  # con la password no: riscrivendo il file si perderebbe la protezione
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

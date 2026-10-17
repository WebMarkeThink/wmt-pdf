"""Inserimento e modifica del testo, senza riscrivere il resto della pagina.

PDFium serve solo a leggere: quali pezzi di testo ci sono, dove, con che font, colore e modalità di resa.
Le modifiche si fanno con pikepdf sul flusso originale della pagina, toccando soltanto l'istruzione di
testo interessata: tutto il resto (riempimenti, spazi colore, immagini) resta com'era.
(Far rigenerare la pagina a PDFium cambiava in nero i riempimenti in spazi colore che non gestisce.)

- Se il font del pezzo ha le lettere nuove (si legge la sua tabella ToUnicode), il testo cambia sul posto,
  con lo stesso font.
- Altrimenti il pezzo vecchio si svuota e il nuovo si disegna sopra, nella stessa posizione, dimensione,
  colore e modalità di resa (il testo invisibile dell'OCR resta invisibile), con un font di sistema di cui
  si incorporano solo le lettere usate.
- Alla fine si rilegge la pagina: se il testo non è quello atteso, il file non viene scritto.
"""

from __future__ import annotations

import ctypes
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from pypdfium2 import raw

from wmt_pdf.source import CliError

SHOW_OPS = {"Tj", "TJ", "'", '"'}


@dataclass
class TextObject:
    index: int  # posizione tra le istruzioni di testo della pagina (primo livello)
    text: str
    bbox: list[float]
    font: str
    font_size: float
    embedded: bool


@dataclass
class EditResult:
    changed: int = 0
    recreated: int = 0
    split: int = 0  # occorrenze divise tra più pezzi (o dentro gruppi di oggetti): non sostituite
    notes: list[str] = field(default_factory=list)


@dataclass
class _Info:
    """Quel che serve di un pezzo di testo per riscriverlo uguale."""

    text: str
    matrix: tuple[float, float, float, float, float, float]
    size: float
    fill: tuple[int, int, int] | None
    stroke: tuple[int, int, int] | None
    mode: int
    bold: bool
    bbox: list[float]
    font: str
    embedded: bool


# --- lettura con PDFium ----------------------------------------------------------------------------


def _obj_text(obj, textpage) -> str:
    size = raw.FPDFTextObj_GetText(obj, textpage, None, 0)  # in byte, terminatore compreso
    if size <= 2:
        return ""
    buf = (ctypes.c_ushort * (size // 2))()
    raw.FPDFTextObj_GetText(obj, textpage, buf, size)
    return bytes(buf).decode("utf-16-le", errors="replace").rstrip("\x00").replace("\xa0", " ")


def _font_name(font) -> str:
    size = raw.FPDFFont_GetBaseFontName(font, None, 0)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    raw.FPDFFont_GetBaseFontName(font, buf, size)
    return buf.value.decode("latin-1", errors="replace")


def _bounds(obj) -> list[float]:
    left, bottom, right, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    raw.FPDFPageObj_GetBounds(obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top))
    return [round(left.value, 2), round(bottom.value, 2), round(right.value, 2), round(top.value, 2)]


def _color(getter, obj) -> tuple[int, int, int] | None:
    rgba = [ctypes.c_uint() for _ in range(4)]
    if not getter(obj, *(ctypes.byref(c) for c in rgba)):
        return None
    return rgba[0].value, rgba[1].value, rgba[2].value


def _read_page(data: bytes, page_no: int, password: str | None) -> list[_Info]:
    """Tutti i pezzi di testo di primo livello della pagina, nell'ordine del flusso."""
    doc = _open(data, password)
    try:
        page = _page(doc, page_no)
        textpage = page.get_textpage()
        try:
            infos = []
            for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT], max_depth=1):
                handle = obj.raw
                size = ctypes.c_float()
                raw.FPDFTextObj_GetFontSize(handle, ctypes.byref(size))
                m = raw.FS_MATRIX()
                raw.FPDFPageObj_GetMatrix(handle, ctypes.byref(m))
                font = raw.FPDFTextObj_GetFont(handle)
                name = _font_name(font)
                infos.append(_Info(
                    text=_obj_text(handle, textpage.raw), matrix=(m.a, m.b, m.c, m.d, m.e, m.f), size=size.value,
                    fill=_color(raw.FPDFPageObj_GetFillColor, handle),
                    stroke=_color(raw.FPDFPageObj_GetStrokeColor, handle),
                    mode=raw.FPDFTextObj_GetTextRenderMode(handle), bold="bold" in name.lower(),
                    bbox=_bounds(handle), font=name, embedded=bool(raw.FPDFFont_GetIsEmbedded(font)),
                ))
            return infos
        finally:
            textpage.close()
            page.close()
    finally:
        doc.close()


def _display_size(info: _Info) -> float:
    _, _, c, d, _, _ = info.matrix
    # Altezza dei caratteri: la scala verticale (c, d); quella orizzontale cambia con lo stiramento (Tz).
    return round(info.size * ((c ** 2 + d ** 2) ** 0.5 or 1.0), 2)


def list_objects(data: bytes, page_no: int, password: str | None = None) -> list[TextObject]:
    return [
        TextObject(i, info.text, info.bbox, info.font, _display_size(info), info.embedded)
        for i, info in enumerate(_read_page(data, page_no, password)) if info.text.strip()
    ]


# --- supporto ------------------------------------------------------------------------------------


def _open(data: bytes, password: str | None) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data, password=password)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise CliError("PDF protetto da password: usa --password o --password-env") from None
        raise CliError(f"PDF non leggibile: {exc}") from None


def _page(doc, page_no: int):
    if not 1 <= page_no <= len(doc):
        raise CliError(f"pagina {page_no} fuori dal documento (1-{len(doc)})")
    return doc[page_no - 1]


def _page_text(data: bytes, page_no: int, password: str | None) -> str:
    doc = _open(data, password)
    try:
        page = _page(doc, page_no)
        textpage = page.get_textpage()
        try:
            return textpage.get_text_range().replace("\xa0", " ")
        finally:
            textpage.close()
            page.close()
    finally:
        doc.close()


def _refuse_signed(data: bytes) -> None:
    from wmt_pdf.pades import count_signatures

    if count_signatures(data):
        raise CliError("il PDF è firmato digitalmente: modificarne il testo renderebbe le firme non valide")


def _page_count(data: bytes, password: str | None) -> int:
    doc = _open(data, password)
    try:
        return len(doc)
    finally:
        doc.close()


# --- codifica del testo nel font originale -------------------------------------------------------------


_HEX = re.compile(r"<([0-9A-Fa-f]+)>")


def _unicode_of(hexstr: str) -> str:
    raw_bytes = bytes.fromhex(hexstr if len(hexstr) % 2 == 0 else "0" + hexstr)
    try:
        return raw_bytes.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def _tounicode_reverse(stream: pikepdf.Object) -> dict[str, bytes]:
    """Dalla tabella ToUnicode del font: carattere → codice da scrivere nel flusso."""
    try:
        text = stream.read_bytes().decode("latin-1")
    except pikepdf.PdfError:
        return {}
    reverse: dict[str, bytes] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.DOTALL):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            char = _unicode_of(dst)
            if len(char) == 1:
                reverse.setdefault(char, bytes.fromhex(src))
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.DOTALL):
        for line in block.strip().splitlines():
            match = re.match(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(.*)", line.strip())
            if not match:
                continue
            lo, hi, rest = int(match.group(1), 16), int(match.group(2), 16), match.group(3).strip()
            width = len(match.group(1)) // 2
            if hi < lo or hi - lo > 65535:
                continue
            if rest.startswith("["):
                for k, dst in enumerate(_HEX.findall(rest)):
                    char = _unicode_of(dst)
                    if len(char) == 1:
                        reverse.setdefault(char, (lo + k).to_bytes(width, "big"))
            else:
                start = _HEX.match(rest)
                if not start:
                    continue
                base = int(start.group(1), 16)
                for k in range(hi - lo + 1):
                    if 0 <= base + k <= 0x10FFFF:
                        reverse.setdefault(chr(base + k), (lo + k).to_bytes(width, "big"))
    return reverse


def _encode(font: pikepdf.Object | None, text: str) -> bytes | None:
    """Il testo codificato per quel font, oppure None se qualche lettera non c'è (o il font è troppo particolare)."""
    if font is None:
        return None
    subtype = font.get("/Subtype")
    if subtype == pikepdf.Name.Type3:
        return None
    to_unicode = font.get("/ToUnicode")
    if subtype == pikepdf.Name.Type0:
        identity = font.get("/Encoding") in (pikepdf.Name("/Identity-H"), pikepdf.Name("/Identity-V"))
        if not identity or to_unicode is None:
            return None
    if to_unicode is not None:
        reverse = _tounicode_reverse(to_unicode)
        try:
            return b"".join(reverse[ch] for ch in text)
        except KeyError:
            return None
    encoding = font.get("/Encoding")
    descriptor = font.get("/FontDescriptor")
    embedded = descriptor is not None and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    if isinstance(encoding, pikepdf.Dictionary) or embedded:
        return None  # Differences o sottoinsieme senza tabella: non si sa quali lettere ci sono
    codec = "mac_roman" if encoding == pikepdf.Name.MacRomanEncoding else "cp1252"
    try:
        return text.encode(codec)  # font standard non incorporato: ha tutte le lettere della sua codifica
    except UnicodeEncodeError:
        return None


# --- scrittura con pikepdf -------------------------------------------------------------------------------


@dataclass
class _Change:
    index: int  # n-esima istruzione di testo della pagina
    new_text: str


def _show_ops(instructions: list) -> list[tuple[int, str | None]]:
    """Posizione di ogni istruzione di testo nel flusso, con il font attivo (nome della risorsa)."""
    result = []
    font: str | None = None
    stack: list[str | None] = []
    for i, ins in enumerate(instructions):
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            continue
        op = str(ins.operator)
        if op == "q":
            stack.append(font)
        elif op == "Q" and stack:
            font = stack.pop()
        elif op == "Tf" and ins.operands:
            font = str(ins.operands[0])
        elif op in SHOW_OPS:
            result.append((i, font))
    return result


def _replace_show(ins, payload: bytes) -> pikepdf.ContentStreamInstruction:
    op = str(ins.operator)
    string = pikepdf.String(payload)
    if op == '"':  # " = spaziatura + a capo + testo: si conservano spaziatura e a capo
        return pikepdf.ContentStreamInstruction([ins.operands[0], ins.operands[1], string], pikepdf.Operator('"'))
    if op == "'":  # ' = a capo + testo
        return pikepdf.ContentStreamInstruction([string], pikepdf.Operator("'"))
    return pikepdf.ContentStreamInstruction([string], pikepdf.Operator("Tj"))


_FONTS: dict[bool, str] = {}


def _overlay_font(bold: bool) -> str:
    """Font di sistema per reportlab (incorpora solo le lettere usate); Helvetica se non ce n'è."""
    if bold in _FONTS:
        return _FONTS[bold]
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    from wmt_pdf.convert import _font_files

    files = _font_files()
    name = "Helvetica-Bold" if bold else "Helvetica"
    if files:
        name = "WmtTesto-Bold" if bold else "WmtTesto"
        pdfmetrics.registerFont(TTFont(name, files[1] if bold else files[0]))
    _FONTS[bold] = name
    return name


def _overlay(width: float, height: float, origin: tuple[float, float], items: list[tuple[_Info, str]]) -> bytes:
    """Una pagina con soltanto il testo nuovo, ciascuno con la matrice, il colore e la resa dell'originale."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    for info, text in items:
        a, b, cc, d, e, f = info.matrix
        c.saveState()
        c.transform(a, b, cc, d, e - origin[0], f - origin[1])
        if info.fill:
            c.setFillColorRGB(*(v / 255 for v in info.fill))
        if info.stroke:
            c.setStrokeColorRGB(*(v / 255 for v in info.stroke))
        t = c.beginText(0, 0)
        t.setFont(_overlay_font(info.bold), info.size)
        if 0 <= info.mode <= 7:
            t.setTextRenderMode(info.mode)  # 3 = invisibile, come il testo dell'OCR
        t.textOut(text)
        c.drawText(t)
        c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


def _apply(data: bytes, page_no: int, changes: list[_Change], password: str | None,
           additions: list[tuple[_Info, str]] | None = None) -> tuple[bytes, int]:
    """Applica i cambiamenti di una pagina. Restituisce il PDF e quanti pezzi sono stati riscritti sopra."""
    from wmt_pdf.ops import open_pdf

    infos = _read_page(data, page_no, password)
    overlays: list[tuple[_Info, str]] = list(additions or [])
    expected = [info.text for info in infos]
    recreated = 0
    with open_pdf(data, password) as pdf:
        page = pdf.pages[page_no - 1]
        if changes:
            instructions = list(pikepdf.parse_content_stream(page))
            shows = _show_ops(instructions)
            if len(shows) != len(infos):
                raise CliError("struttura del testo di questa pagina non gestita: nessuna modifica fatta")
            fonts = page.obj.get("/Resources", {}).get("/Font", {})
            for change in changes:
                position, font_name = shows[change.index]
                font = fonts.get(font_name) if font_name else None
                payload = _encode(font, change.new_text) if change.new_text.strip() else b""
                if payload is None:  # lettere mancanti nel font: si svuota il pezzo e si riscrive sopra
                    payload = b""
                    overlays.append((infos[change.index], change.new_text))
                    recreated += 1
                    expected[change.index] = ""
                else:
                    expected[change.index] = change.new_text
                instructions[position] = _replace_show(instructions[position], payload)
            page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(instructions))
        if overlays:
            _append_text(pdf, page, overlays)
            expected += [text for _, text in overlays]  # in fondo al flusso, come pezzi di testo modificabili
        buf = io.BytesIO()
        pdf.save(buf, encryption=pdf.is_encrypted)  # un PDF protetto resta protetto
    result = buf.getvalue()
    _verify(result, page_no, password, expected)
    return result, recreated


def _append_text(pdf: pikepdf.Pdf, page: pikepdf.Page, items: list[tuple[_Info, str]]) -> None:
    """Il testo nuovo va in fondo al flusso della pagina (non in un gruppo a parte), così resta modificabile."""
    box = page.mediabox
    x0, y0 = float(box[0]), float(box[1])
    layer = _overlay(float(box[2]) - x0, float(box[3]) - y0, (x0, y0), items)
    with pikepdf.open(io.BytesIO(layer)) as extra:
        source = extra.pages[0]
        resources = page.obj.get("/Resources")
        if resources is None:
            page.obj.Resources = resources = pikepdf.Dictionary()
        if "/Font" not in resources:
            resources.Font = pikepdf.Dictionary()
        renamed: dict[str, pikepdf.Name] = {}
        for name, font in source.obj.Resources.Font.items():
            n = 1
            while f"/WmtF{n}" in resources.Font:
                n += 1
            new_name = pikepdf.Name(f"/WmtF{n}")
            resources.Font[new_name] = pdf.copy_foreign(font)
            renamed[name] = new_name
        instructions = []
        for ins in pikepdf.parse_content_stream(source):
            if str(ins.operator) == "Tf" and str(ins.operands[0]) in renamed:
                ins = pikepdf.ContentStreamInstruction([renamed[str(ins.operands[0])], ins.operands[1]], ins.operator)
            instructions.append(ins)
        added = pikepdf.unparse_content_stream(instructions)
    # Il contenuto originale tra q e Q: le sue impostazioni grafiche non toccano il testo nuovo.
    page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
    page.contents_add(pdf.make_stream(b"\nQ\n" + added), prepend=False)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _verify(data: bytes, page_no: int, password: str | None, expected: list[str]) -> None:
    """La pagina deve avere esattamente i pezzi di testo attesi: altrimenti meglio non scrivere nulla."""
    got = [_norm(i.text) for i in _read_page(data, page_no, password) if _norm(i.text)]
    want = [_norm(t) for t in expected if _norm(t)]
    if got != want:
        raise CliError("la modifica non ha dato il testo atteso: file non scritto (PDF con una struttura particolare)")


def _write(data: bytes, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


# --- modifiche -------------------------------------------------------------------------------------


def replace_text(data: bytes, target: Path, find: str, replacement: str, pages: list[int] | None = None,
                 password: str | None = None) -> EditResult:
    if not find:
        raise CliError("testo da cercare vuoto")
    _refuse_signed(data)
    result = EditResult()
    current = data
    for page_no in pages or range(1, _page_count(data, password) + 1):
        infos = _read_page(current, page_no, password)
        changes = [_Change(i, info.text.replace(find, replacement)) for i, info in enumerate(infos) if find in info.text]
        in_objects = sum(info.text.count(find) for info in infos)
        result.split += max(0, _page_text(current, page_no, password).count(find) - in_objects)
        if changes:
            current, recreated = _apply(current, page_no, changes, password)
            result.recreated += recreated
            result.changed += len(changes)
    if not result.changed:
        hint = (" (c'è, ma diviso in più pezzi di testo o dentro un gruppo di oggetti: "
                "modificalo un pezzo per volta con edittext)") if result.split else ""
        raise CliError(f"«{find}» non trovato in un unico pezzo di testo{hint}")
    _write(current, target)
    return result


def _object_at(infos: list[_Info], x: float, y: float) -> int | None:
    best = None
    for i, info in enumerate(infos):
        x0, y0, x1, y1 = info.bbox
        if info.text.strip() and x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[0]:
                best = (area, i)  # il più piccolo: quello più preciso sotto il punto
    return best[1] if best else None


def edit_object(data: bytes, target: Path, page_no: int, new_text: str, *, at: tuple[float, float] | None = None,
                index: int | None = None, password: str | None = None) -> EditResult:
    _refuse_signed(data)
    infos = _read_page(data, page_no, password)
    if at is not None:
        index = _object_at(infos, *at)
        if index is None:
            raise CliError(f"nessun testo in quel punto di pagina {page_no}")
    elif index is None or not 0 <= index < len(infos):
        raise CliError("pezzo di testo inesistente: vedi `wpdf textobjects`")
    result, recreated = _apply(data, page_no, [_Change(index, new_text)], password)
    _write(result, target)
    if not new_text.strip():
        return EditResult(changed=1, notes=["testo eliminato"])
    return EditResult(changed=1, recreated=recreated)


def parse_color(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
    if not match:
        raise CliError("--color vuole un colore esadecimale, es. #c00000")
    value = match.group(1)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def add_text(data: bytes, target: Path, page_no: int, x: float, y: float, text: str, *, size: float = 11,
             color: tuple[int, int, int] = (0, 0, 0), bold: bool = False, password: str | None = None) -> int:
    """Testo nuovo con origine (x, y) della prima riga, in punti dal basso a sinistra; una riga per «\\n»."""
    if not text.strip():
        raise CliError("testo vuoto")
    if not 1 <= size <= 400:
        raise CliError("--size tra 1 e 400")
    _refuse_signed(data)
    if not 1 <= page_no <= _page_count(data, password):
        raise CliError(f"pagina {page_no} fuori dal documento")
    additions = []
    for k, line in enumerate(text.split("\n")):
        if line:
            info = _Info(text=line, matrix=(1, 0, 0, 1, x, y - k * size * 1.25), size=size, fill=color, stroke=None,
                         mode=0, bold=bold, bbox=[], font="", embedded=False)
            additions.append((info, line))
    result, _ = _apply(data, page_no, [], password, additions=additions)
    _write(result, target)
    return len(additions)

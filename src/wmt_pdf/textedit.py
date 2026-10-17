"""Inserimento e modifica del testo con PDFium.

Modifica di un testo esistente: se le lettere nuove sono disponibili nel font del testo, lo si cambia
sul posto (stesso font, stessa posizione). Altrimenti, tipicamente con i font incorporati in
sottoinsieme che contengono solo le lettere usate, l'oggetto si ricrea nella stessa posizione, dimensione,
colore, modalità di resa (anche il testo invisibile dell'OCR resta invisibile) e ordine di sovrapposizione,
con un font di sistema: il testo resta vero testo, ma il carattere può cambiare leggermente. Il risultato lo dice.

Si lavora in due fasi: prima si legge tutto con la pagina di testo di PDFium aperta, poi la si chiude e si
modifica, perché la pagina di testo tiene riferimenti agli oggetti e non va usata mentre si tolgono.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pypdfium2 import raw

from wmt_pdf.source import CliError


@dataclass
class TextObject:
    index: int  # posizione tra gli oggetti di testo della pagina (solo primo livello)
    text: str
    bbox: list[float]
    font: str
    font_size: float
    embedded: bool


@dataclass
class EditResult:
    changed: int = 0
    recreated: int = 0
    split: int = 0  # occorrenze divise tra più oggetti (o dentro gruppi di oggetti): non sostituite
    notes: list[str] = field(default_factory=list)


@dataclass
class _Target:
    obj: Any  # oggetto di pagina di pypdfium2
    new_text: str
    in_place: bool
    bold: bool


# --- lettura -------------------------------------------------------------------------------


def _obj_text(obj, textpage) -> str:
    size = raw.FPDFTextObj_GetText(obj, textpage, None, 0)  # in byte, terminatore compreso
    if size <= 2:
        return ""
    buf = (ctypes.c_ushort * (size // 2))()
    raw.FPDFTextObj_GetText(obj, textpage, buf, size)
    return bytes(buf).decode("utf-16-le", errors="replace").rstrip("\x00").replace("\xa0", " ")


def _wide(text: str):
    data = (text + "\x00").encode("utf-16-le")
    return (ctypes.c_ushort * (len(data) // 2)).from_buffer_copy(data)


def _font_name(font) -> str:
    size = raw.FPDFFont_GetBaseFontName(font, None, 0)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    raw.FPDFFont_GetBaseFontName(font, buf, size)
    return buf.value.decode("latin-1", errors="replace")


def _font_size(obj) -> float:
    value = ctypes.c_float()
    raw.FPDFTextObj_GetFontSize(obj, ctypes.byref(value))
    matrix = raw.FS_MATRIX()
    raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(matrix))
    # Altezza dei caratteri: la scala verticale (c, d); quella orizzontale cambia con lo stiramento (Tz).
    scale = (matrix.c ** 2 + matrix.d ** 2) ** 0.5 or 1.0
    return round(value.value * scale, 2)


def _bounds(obj) -> list[float]:
    left, bottom, right, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    raw.FPDFPageObj_GetBounds(obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top))
    return [round(left.value, 2), round(bottom.value, 2), round(right.value, 2), round(top.value, 2)]


def _text_objects(page) -> list:
    return list(page.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT], max_depth=1))


def _address(handle) -> int | None:
    return ctypes.cast(handle, ctypes.c_void_p).value  # i puntatori ctypes non si confrontano con ==


def list_objects(data: bytes, page_no: int, password: str | None = None) -> list[TextObject]:
    doc = _open(data, password)
    try:
        page = _page(doc, page_no)
        textpage = page.get_textpage()
        try:
            result = []
            for i, obj in enumerate(_text_objects(page)):
                text = _obj_text(obj.raw, textpage.raw)
                if not text.strip():
                    continue
                font = raw.FPDFTextObj_GetFont(obj.raw)
                result.append(TextObject(i, text, _bounds(obj.raw), _font_name(font), _font_size(obj.raw),
                                         bool(raw.FPDFFont_GetIsEmbedded(font))))
            return result
        finally:
            textpage.close()
            page.close()
    finally:
        doc.close()


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


def _refuse_signed(data: bytes) -> None:
    from wmt_pdf.pades import count_signatures

    if count_signatures(data):
        raise CliError("il PDF è firmato digitalmente: modificarne il testo renderebbe le firme non valide")


def _dedupe_fonts(pdf) -> None:
    """Un font di sistema incorporato a ogni modifica: le copie identiche si riducono a una."""
    import pikepdf

    seen: dict[str, object] = {}
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Dictionary) or obj.get("/Type") != pikepdf.Name.FontDescriptor:
            continue
        for key in ("/FontFile2", "/FontFile3", "/FontFile"):
            stream = obj.get(key)
            if stream is None:
                continue
            try:
                digest = hashlib.sha256(stream.read_raw_bytes()).hexdigest()
            except pikepdf.PdfError:
                continue
            if digest in seen:
                obj[key] = seen[digest]  # la copia non più usata sparisce al salvataggio
            else:
                seen[digest] = stream


def _save(doc, data: bytes, target: Path, password: str | None) -> None:
    """Salvataggio completo; un PDF protetto resta protetto con la stessa password."""
    import pikepdf

    from wmt_pdf.ops import open_pdf, save

    buf = io.BytesIO()
    doc.save(buf)
    # PDFium salva un PDF protetto ancora protetto: si riapre con la stessa password.
    with open_pdf(data, password) as original, \
            pikepdf.open(io.BytesIO(buf.getvalue()), password=password or "") as edited:
        _dedupe_fonts(edited)
        save(edited, target, source=original, password=password)


class FontCache:
    """Font di sistema caricati una volta per documento (per il testo nuovo o ricreato)."""

    def __init__(self, doc):
        self.doc = doc
        self.fonts: dict[bool, object] = {}

    def get(self, bold: bool = False):
        if bold not in self.fonts:
            from wmt_pdf.convert import _font_files

            files = _font_files()
            font = None
            if files:
                data = Path(files[1] if bold else files[0]).read_bytes()
                buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)  # PDFium ne fa una copia
                font = raw.FPDFText_LoadFont(self.doc.raw, buf, len(data), raw.FPDF_FONT_TRUETYPE, 1)
            if not font:  # nessun font di sistema: Helvetica standard (solo caratteri latini)
                font = raw.FPDFText_LoadStandardFont(self.doc.raw, b"Helvetica-Bold" if bold else b"Helvetica")
            if not font:
                raise CliError("nessun font disponibile per scrivere il testo")
            self.fonts[bold] = font
        return self.fonts[bold]

    def close(self) -> None:
        for font in self.fonts.values():
            raw.FPDFFont_Close(font)


def _plan(page, textpage, matches: list[tuple[Any, str]]) -> list[_Target]:
    """Fase di lettura: per ogni oggetto da cambiare, se si può fare sul posto."""
    chars_by_font: dict[int | None, set[str]] = {}
    for obj in _text_objects(page):
        key = _address(raw.FPDFTextObj_GetFont(obj.raw))
        chars_by_font.setdefault(key, set()).update(_obj_text(obj.raw, textpage))
    targets = []
    for obj, new_text in matches:
        font = raw.FPDFTextObj_GetFont(obj.raw)
        embedded = bool(raw.FPDFFont_GetIsEmbedded(font))
        # In un sottoinsieme incorporato ci sono di sicuro solo le lettere già scritte con quel font.
        available = chars_by_font.get(_address(font), set())
        in_place = not embedded or (set(new_text) - {" "}) <= available
        targets.append(_Target(obj, new_text, in_place, "bold" in _font_name(font).lower()))
    return targets


def _object_index(page, obj) -> int:
    wanted = _address(obj.raw)
    for i in range(raw.FPDFPage_CountObjects(page.raw)):
        if _address(raw.FPDFPage_GetObject(page.raw, i)) == wanted:
            return i
    return -1


def _execute(doc, page, targets: list[_Target], fonts: FontCache) -> int:
    """Fase di scrittura (pagina di testo già chiusa). Restituisce quanti oggetti sono stati ricreati."""
    recreated = 0
    for target in targets:
        if target.in_place and raw.FPDFText_SetText(target.obj.raw, _wide(target.new_text)):
            continue
        _recreate(doc, page, target, fonts)
        recreated += 1
    return recreated


def _recreate(doc, page, target: _Target, fonts: FontCache) -> None:
    old = target.obj.raw
    size = ctypes.c_float()
    raw.FPDFTextObj_GetFontSize(old, ctypes.byref(size))
    matrix = raw.FS_MATRIX()
    raw.FPDFPageObj_GetMatrix(old, ctypes.byref(matrix))
    fill = [ctypes.c_uint() for _ in range(4)]
    has_fill = raw.FPDFPageObj_GetFillColor(old, *(ctypes.byref(c) for c in fill))
    stroke = [ctypes.c_uint() for _ in range(4)]
    has_stroke = raw.FPDFPageObj_GetStrokeColor(old, *(ctypes.byref(c) for c in stroke))
    mode = raw.FPDFTextObj_GetTextRenderMode(old)
    new = raw.FPDFPageObj_CreateTextObj(doc.raw, fonts.get(target.bold), size.value)
    raw.FPDFText_SetText(new, _wide(target.new_text))
    raw.FPDFPageObj_SetMatrix(new, ctypes.byref(matrix))
    if has_fill:
        raw.FPDFPageObj_SetFillColor(new, *(c.value for c in fill))
    if has_stroke:
        raw.FPDFPageObj_SetStrokeColor(new, *(c.value for c in stroke))
    if mode >= 0:
        raw.FPDFTextObj_SetTextRenderMode(new, mode)  # 3 = invisibile, come il testo dell'OCR
    index = _object_index(page, target.obj)
    raw.FPDFPage_RemoveObject(page.raw, old)
    raw.FPDFPageObj_Destroy(old)
    if index >= 0:
        raw.FPDFPage_InsertObjectAtIndex(page.raw, new, index)  # stessa posizione nella pila
    else:
        raw.FPDFPage_InsertObject(page.raw, new)


# --- modifiche -------------------------------------------------------------------------------------


def replace_text(data: bytes, target: Path, find: str, replacement: str, pages: list[int] | None = None,
                 password: str | None = None) -> EditResult:
    if not find:
        raise CliError("testo da cercare vuoto")
    _refuse_signed(data)
    doc = _open(data, password)
    fonts = FontCache(doc)
    result = EditResult()
    try:
        for page_no in pages or range(1, len(doc) + 1):
            page = _page(doc, page_no)
            try:
                textpage = page.get_textpage()
                try:
                    matches, in_objects = [], 0
                    for obj in _text_objects(page):
                        text = _obj_text(obj.raw, textpage.raw)
                        if find in text:
                            in_objects += text.count(find)
                            matches.append((obj, text.replace(find, replacement)))
                    targets = _plan(page, textpage.raw, matches)
                    page_total = textpage.get_text_range().replace("\xa0", " ").count(find)
                finally:
                    textpage.close()
                result.split += max(0, page_total - in_objects)
                if targets:
                    result.recreated += _execute(doc, page, targets, fonts)
                    result.changed += len(targets)
                    raw.FPDFPage_GenerateContent(page.raw)
            finally:
                page.close()
        if not result.changed:
            hint = (" (c'è, ma diviso in più pezzi di testo o dentro un gruppo di oggetti: "
                    "modificalo un pezzo per volta con edittext)") if result.split else ""
            raise CliError(f"«{find}» non trovato in un unico pezzo di testo{hint}")
        _save(doc, data, target, password)
    finally:
        fonts.close()
        doc.close()
    return result


def _object_at(page, x: float, y: float):
    best = None
    for obj in _text_objects(page):
        x0, y0, x1, y1 = _bounds(obj.raw)
        if x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[0]:
                best = (area, obj)  # il più piccolo: quello più preciso sotto il punto
    return best[1] if best else None


def edit_object(data: bytes, target: Path, page_no: int, new_text: str, *, at: tuple[float, float] | None = None,
                index: int | None = None, password: str | None = None) -> EditResult:
    _refuse_signed(data)
    doc = _open(data, password)
    fonts = FontCache(doc)
    try:
        page = _page(doc, page_no)
        try:
            if at is not None:
                obj = _object_at(page, *at)
                if obj is None:
                    raise CliError(f"nessun testo in quel punto di pagina {page_no}")
            else:
                objs = _text_objects(page)
                if index is None or not 0 <= index < len(objs):
                    raise CliError("oggetto di testo inesistente: vedi `wpdf textobjects`")
                obj = objs[index]
            if not new_text.strip():
                raw.FPDFPage_RemoveObject(page.raw, obj.raw)
                raw.FPDFPageObj_Destroy(obj.raw)
                result = EditResult(changed=1, notes=["testo eliminato"])
            else:
                textpage = page.get_textpage()
                try:
                    targets = _plan(page, textpage.raw, [(obj, new_text)])
                finally:
                    textpage.close()
                result = EditResult(changed=1, recreated=_execute(doc, page, targets, fonts))
            raw.FPDFPage_GenerateContent(page.raw)
        finally:
            page.close()
        _save(doc, data, target, password)
    finally:
        fonts.close()
        doc.close()
    return result


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
    doc = _open(data, password)
    fonts = FontCache(doc)
    lines = text.split("\n")
    try:
        page = _page(doc, page_no)
        try:
            for k, line in enumerate(lines):
                if not line:
                    continue
                obj = raw.FPDFPageObj_CreateTextObj(doc.raw, fonts.get(bold), ctypes.c_float(size))
                raw.FPDFText_SetText(obj, _wide(line))
                raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y - k * size * 1.25)
                raw.FPDFPageObj_SetFillColor(obj, *color, 255)
                raw.FPDFPage_InsertObject(page.raw, obj)
            raw.FPDFPage_GenerateContent(page.raw)
        finally:
            page.close()
        _save(doc, data, target, password)
    finally:
        fonts.close()
        doc.close()
    return len([line for line in lines if line])

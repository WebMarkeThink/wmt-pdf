"""Oggetti della pagina che si possono spostare: testo, immagini, elementi grafici e annotazioni.

- Testo: vedi textedit.move_object.
- Immagini ed elementi grafici (firme grafiche, timbri, filigrane, loghi vettoriali messi come XObject):
  l'istruzione che li disegna (Do o immagine in linea) si toglie dal flusso e si riscrive in fondo, con la
  stessa matrice spostata, la stessa trasparenza e gli stessi colori, e con il ritaglio spostato insieme.
  PDFium dice dove sono; pikepdf tocca soltanto quell'istruzione. Alla fine si rilegge la pagina e si controlla
  che l'oggetto sia dove deve e che gli altri non si siano mossi.
- Annotazioni (evidenziazioni, note, timbri, campi dei moduli): si spostano il rettangolo e i punti.
"""

from __future__ import annotations

import ctypes
import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pypdfium2 import raw

from wmt_pdf.i18n import N_, t
from wmt_pdf.source import CliError
from wmt_pdf.textedit import (
    _bounds,
    _open,
    _page,
    _refuse_signed,
    _write,
    clip_ops,
    clip_paths,
    list_objects,
    move_object,
    wrap_and_append,
)

KINDS = ("text", "image", "graphic", "annotation")
KIND_NAMES = {"text": N_("testo"), "image": N_("immagine"), "graphic": N_("elemento grafico"), "annotation": N_("annotazione")}

# Istruzioni che cambiano come si disegna un'immagine o un XObject: si riportano sull'oggetto spostato.
FILL_COLOR, STROKE_COLOR = {"rg", "g", "k"}, {"RG", "G", "K"}
PAINT = {"n", "f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}  # chiudono la costruzione di un tracciato
LINE_STATE = {"w", "J", "j", "M", "d", "ri", "i"}  # contano per i tratti dentro un XObject che non li imposta


@dataclass
class PageObject:
    kind: str  # text, image, graphic, annotation
    index: int  # posizione tra gli oggetti dello stesso tipo
    bbox: list[float]
    label: str


@dataclass
class _Drawn:
    """Un'immagine o un elemento grafico come lo vede PDFium."""

    kind: str
    matrix: tuple[float, float, float, float, float, float]
    bbox: list[float]
    clip: list


@dataclass
class _Draw:
    """La stessa cosa nel flusso della pagina, con lo stato grafico che le serve."""

    position: int
    kind: str
    name: str | None  # risorsa XObject; None per un'immagine in linea
    state: list  # gs e colori in vigore
    marks: list  # contenuto marcato aperto (livelli, artefatti), da riaprire attorno all'oggetto spostato
    even_odd: bool = False  # ritaglio pari-dispari (W*): PDFium non lo dice, riprodurlo come W sbaglierebbe


# --- lettura -------------------------------------------------------------------------------------------


def _read_drawn(data: bytes, page_no: int, password: str | None) -> list[_Drawn]:
    doc = _open(data, password)
    try:
        page = _page(doc, page_no)
        try:
            result = []
            for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_FORM], max_depth=1):
                handle = obj.raw
                m = raw.FS_MATRIX()
                raw.FPDFPageObj_GetMatrix(handle, ctypes.byref(m))
                kind = "image" if raw.FPDFPageObj_GetType(handle) == raw.FPDF_PAGEOBJ_IMAGE else "graphic"
                result.append(_Drawn(kind, (m.a, m.b, m.c, m.d, m.e, m.f), _bounds(handle), clip_paths(handle)))
            return result
        finally:
            page.close()
    finally:
        doc.close()


def _carried_mark(ins) -> bool:
    """Un contenuto marcato da riaprire: sì per livelli e artefatti, no per i riferimenti alla struttura (MCID)."""
    if str(ins.operator) == "BMC":
        return True
    props = ins.operands[1] if len(ins.operands) > 1 else None
    return not (isinstance(props, pikepdf.Dictionary) and "/MCID" in props)


def _draw_ops(instructions: list, xobjects) -> list[_Draw]:
    """Le istruzioni di primo livello che disegnano un'immagine o un XObject, nell'ordine del flusso."""
    result: list[_Draw] = []
    state: dict[str, list] = {"gs": [], "fill": [], "stroke": [], "line": [], "even_odd": []}
    stack: list[dict[str, list]] = []
    marks: list = []
    subpaths = 0  # pezzi del tracciato in costruzione: con uno solo pari-dispari e non-zero coincidono
    for i, ins in enumerate(instructions):
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            result.append(_Draw(i, "image", None, state["gs"] + state["fill"], [m for m in marks if m],
                                bool(state["even_odd"])))
            continue
        op = str(ins.operator)
        if op == "q":
            stack.append({k: list(v) for k, v in state.items()})
        elif op == "Q" and stack:
            state = stack.pop()
        elif op == "gs":
            state["gs"].append(ins)
        elif op in FILL_COLOR or op == "cs":
            state["fill"] = [ins]
        elif op in ("sc", "scn"):
            state["fill"] = [x for x in state["fill"] if str(x.operator) == "cs"] + [ins]
        elif op in STROKE_COLOR or op == "CS":
            state["stroke"] = [ins]
        elif op in ("SC", "SCN"):
            state["stroke"] = [x for x in state["stroke"] if str(x.operator) == "CS"] + [ins]
        elif op in LINE_STATE:
            state["line"] = [x for x in state["line"] if str(x.operator) != op] + [ins]
        elif op in ("m", "re"):
            subpaths += 1
        elif op in PAINT:
            subpaths = 0
        elif op == "W*" and subpaths > 1:
            state["even_odd"] = [ins]
        elif op in ("BDC", "BMC"):
            marks.append(ins if _carried_mark(ins) else None)
        elif op == "EMC" and marks:
            marks.pop()
        elif op == "Do" and ins.operands:
            name = str(ins.operands[0])
            xobj = xobjects.get(name) if xobjects is not None else None
            subtype = xobj.get("/Subtype") if xobj is not None else None
            if subtype in (pikepdf.Name.Image, pikepdf.Name.Form):
                kind = "image" if subtype == pikepdf.Name.Image else "graphic"
                result.append(_Draw(i, kind, name, state["gs"] + state["fill"] + state["stroke"] + state["line"],
                                    [m for m in marks if m], bool(state["even_odd"])))
    return result


def _visible_annots(page: pikepdf.Page) -> list[tuple[int, pikepdf.Dictionary]]:
    annots = page.obj.get("/Annots") or []
    result = []
    for i, annot in enumerate(annots):
        if not isinstance(annot, pikepdf.Dictionary) or "/Rect" not in annot:
            continue
        try:
            flags = int(annot.get("/F", 0))
        except (TypeError, ValueError):
            flags = 0
        if annot.get("/Subtype") == pikepdf.Name.Popup or flags & (2 | 32):  # nascosta, non visualizzata
            continue
        result.append((i, annot))
    return result


def _annot_label(annot: pikepdf.Dictionary) -> str:
    from wmt_pdf.marks import ANNOT_NAMES

    kind = str(annot.get("/Subtype", "/Annotazione")).lstrip("/")
    if kind == "Widget":
        field, name = annot, None
        while field is not None and name is None:
            name = field.get("/T")
            field = field.get("/Parent")
        return t("Campo «{name}»", name=name) if name is not None else t("Campo modulo")
    return t(ANNOT_NAMES.get(kind, kind))


def _rect(annot: pikepdf.Dictionary) -> list[float]:
    x0, y0, x1, y1 = (float(v) for v in annot.Rect)
    return [round(min(x0, x1), 2), round(min(y0, y1), 2), round(max(x0, x1), 2), round(max(y0, y1), 2)]


def list_page_objects(data: bytes, page_no: int, password: str | None = None) -> list[PageObject]:
    from wmt_pdf.marks import _xobject_is_watermark
    from wmt_pdf.ops import open_pdf

    items = [PageObject("text", o.index, o.bbox, o.text) for o in list_objects(data, page_no, password)]
    drawn = _read_drawn(data, page_no, password)
    with open_pdf(data, password) as pdf:
        page = pdf.pages[page_no - 1]
        xobjects = page.obj.get("/Resources", {}).get("/XObject")
        draws = _draw_ops(list(pikepdf.parse_content_stream(page)), xobjects)
        matched = [d.kind for d in draws] == [d.kind for d in drawn]
        counts = {"image": 0, "graphic": 0}
        for k, item in enumerate(drawn):
            label = t("Immagine") if item.kind == "image" else t("Elemento grafico")
            if matched and draws[k].name and _xobject_is_watermark(xobjects[draws[k].name]):
                label = t("Filigrana")
            items.append(PageObject(item.kind, counts[item.kind], item.bbox, label))
            counts[item.kind] += 1
        items += [PageObject("annotation", i, _rect(a), _annot_label(a)) for i, a in _visible_annots(page)]
    return items


# --- spostamento -----------------------------------------------------------------------------------------


def _delta(bbox: list[float], by: tuple[float, float] | None, to: tuple[float, float] | None) -> tuple[float, float]:
    if to is not None:
        return to[0] - bbox[0], to[1] - bbox[1]
    if by is None:
        raise CliError(t("serve --by dx,dy oppure --to x,y"))
    return by


def move_page_object(data: bytes, target: Path, page_no: int, kind: str, index: int, *,
                     by: tuple[float, float] | None = None, to: tuple[float, float] | None = None,
                     password: str | None = None) -> None:
    """Sposta un oggetto della pagina; con --to, l'angolo in basso a sinistra va nel punto indicato."""
    if kind not in KINDS:
        raise CliError(t("tipo di oggetto sconosciuto: {kind} (uno tra {join})", kind=kind, join=', '.join(KINDS)))
    _refuse_signed(data)
    if kind == "text":
        texts = {o.index: o for o in list_objects(data, page_no, password)}
        if index not in texts:
            raise CliError(t("pezzo di testo inesistente: vedi `wpdf objects`"))
        move_object(data, target, page_no, index, by=_delta(texts[index].bbox, by, to), password=password)
    elif kind == "annotation":
        _move_annotation(data, target, page_no, index, by, to, password)
    else:
        _move_drawn(data, target, page_no, kind, index, by, to, password)


def _move_drawn(data: bytes, target: Path, page_no: int, kind: str, index: int, by, to,
                password: str | None) -> None:
    from wmt_pdf.ops import open_pdf

    drawn = _read_drawn(data, page_no, password)
    same_kind = [k for k, d in enumerate(drawn) if d.kind == kind]
    if not 0 <= index < len(same_kind):
        raise CliError(t("{v} inesistente: vedi `wpdf objects`", v=t(KIND_NAMES[kind])))
    position = same_kind[index]
    item = drawn[position]
    dx, dy = _delta(item.bbox, by, to)
    with open_pdf(data, password) as pdf:
        page = pdf.pages[page_no - 1]
        instructions = list(pikepdf.parse_content_stream(page))
        draws = _draw_ops(instructions, page.obj.get("/Resources", {}).get("/XObject"))
        if [d.kind for d in draws] != [d.kind for d in drawn]:
            raise CliError(t("struttura di questa pagina non gestita: nessuna modifica fatta"))
        draw = draws[position]
        problem = _unsupported(draw, page.obj.get("/Resources", {}))
        if problem:
            raise CliError(t("questo oggetto usa {problem}: spostandolo cambierebbe aspetto, nessuna modifica fatta", problem=problem))
        original = instructions[draw.position]
        rebuilt = [ins for i, ins in enumerate(instructions) if i != draw.position]
        op = pikepdf.Operator
        a, b, c, d, e, f = item.matrix
        moved = [pikepdf.ContentStreamInstruction([], op("q")), *draw.marks, *draw.state,
                 *clip_ops(item.clip, dx, dy),
                 pikepdf.ContentStreamInstruction([a, b, c, d, e + dx, f + dy], op("cm")), original,
                 *[pikepdf.ContentStreamInstruction([], op("EMC")) for _ in draw.marks],
                 pikepdf.ContentStreamInstruction([], op("Q"))]
        wrap_and_append(pdf, page, rebuilt, moved)
        buf = io.BytesIO()
        pdf.save(buf, encryption=pdf.is_encrypted)
    result = buf.getvalue()
    _check_drawn(result, page_no, password, drawn, position, dx, dy)
    _write(result, target)


def _unsupported(draw: _Draw, resources) -> str | None:
    """Stati grafici che dipendono dalla posizione sulla pagina e che, riportati così come sono, non seguirebbero
    l'oggetto: maschere morbide, motivi (pattern), ritagli pari-dispari."""
    if draw.even_odd:
        return t("un ritaglio pari-dispari")
    states = resources.get("/ExtGState", {})
    for ins in draw.state:
        op = str(ins.operator)
        if op == "gs" and ins.operands:
            gs = states.get(str(ins.operands[0]))
            if gs is not None and gs.get("/SMask", pikepdf.Name("/None")) != pikepdf.Name("/None"):
                return t("una maschera di trasparenza")
        elif op in ("scn", "SCN") and ins.operands and isinstance(ins.operands[-1], pikepdf.Name):  # /Pattern cs
            return t("un motivo (pattern)")
    return None


def _check_drawn(result: bytes, page_no: int, password: str | None, before: list[_Drawn], position: int,
                 dx: float, dy: float) -> None:
    """L'oggetto spostato è in fondo e dove deve essere; gli altri sono rimasti com'erano."""
    after = _read_drawn(result, page_no, password)
    expected = [d.bbox for k, d in enumerate(before) if k != position]
    expected.append([v + (dx if i % 2 == 0 else dy) for i, v in enumerate(before[position].bbox)])
    kinds = [d.kind for k, d in enumerate(before) if k != position] + [before[position].kind]
    ok = len(after) == len(expected) and [d.kind for d in after] == kinds and all(
        abs(u - v) < 1.0 for got, want in zip(after, expected, strict=True) for u, v in zip(got.bbox, want, strict=True))
    if not ok:
        raise CliError(t("lo spostamento non ha dato il risultato atteso: file non scritto (PDF con una struttura particolare)"))


def _shift(values, dx: float, dy: float) -> pikepdf.Array:
    return pikepdf.Array([float(v) + (dx if i % 2 == 0 else dy) for i, v in enumerate(values)])


def _move_annotation(data: bytes, target: Path, page_no: int, index: int, by, to, password: str | None) -> None:
    from wmt_pdf.ops import open_pdf

    with open_pdf(data, password) as pdf:
        if not 1 <= page_no <= len(pdf.pages):
            raise CliError(t("pagina {page_no} fuori dal documento (1-{len})", page_no=page_no, len=len(pdf.pages)))
        page = pdf.pages[page_no - 1]
        annots = dict(_visible_annots(page))
        if index not in annots:
            raise CliError(t("annotazione inesistente: vedi `wpdf objects`"))
        annot = annots[index]
        dx, dy = _delta(_rect(annot), by, to)
        # L'aspetto (/AP) si adatta al rettangolo da solo: bastano il rettangolo e i punti che lo descrivono.
        annot.Rect = _shift(annot.Rect, dx, dy)
        for key in ("/QuadPoints", "/L", "/Vertices", "/CL"):
            if key in annot:
                annot[key] = _shift(annot[key], dx, dy)
        for key in ("/InkList", "/Path"):  # elenchi di tratti (Path: poligoni del PDF 2.0)
            if key in annot:
                annot[key] = pikepdf.Array([_shift(stroke, dx, dy) for stroke in annot[key]])
        popup = annot.get("/Popup")
        if isinstance(popup, pikepdf.Dictionary) and "/Rect" in popup:
            popup.Rect = _shift(popup.Rect, dx, dy)
        buf = io.BytesIO()
        pdf.save(buf, encryption=pdf.is_encrypted)
    _write(buf.getvalue(), target)

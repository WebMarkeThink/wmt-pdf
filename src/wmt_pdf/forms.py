"""Moduli PDF: elenco dei campi, compilazione, appiattimento, creazione di campi nuovi."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf.form import (
    CheckboxField,
    ChoiceField,
    ExtendedAppearanceStreamGenerator,
    Form,
    RadioButtonGroup,
    SignatureField,
    TextField,
)

from wmt_pdf.appearance import refresh_text_appearances, widgets_of
from wmt_pdf.ops import open_pdf, save
from wmt_pdf.source import CliError

TYPE_LABELS = {
    "text": "testo", "checkbox": "casella", "radio": "scelta singola", "choice": "elenco",
    "signature": "firma", "button": "pulsante", "other": "altro",
}
TRUE_WORDS = {"1", "true", "sì", "si", "s", "yes", "y", "x", "on", "vero", "ok"}
FALSE_WORDS = {"0", "false", "no", "n", "", "off", "falso"}


# --- elenco ----------------------------------------------------------------------------


@dataclass
class FieldInfo:
    name: str
    type: str
    value: object
    options: list[str] = field(default_factory=list)
    page: int | None = None
    required: bool = False
    readonly: bool = False

    @property
    def label(self) -> str:
        return TYPE_LABELS.get(self.type, self.type)


def _kind(f) -> str:
    if isinstance(f, TextField):
        return "text"
    if isinstance(f, CheckboxField):
        return "checkbox"
    if isinstance(f, RadioButtonGroup):
        return "radio"
    if isinstance(f, ChoiceField):
        return "choice"
    if isinstance(f, SignatureField):
        return "signature"
    return "button" if type(f).__name__ == "PushbuttonField" else "other"


def _form(pdf: pikepdf.Pdf) -> Form:
    return Form(pdf, ExtendedAppearanceStreamGenerator)


def _widget_pages(pdf: pikepdf.Pdf) -> dict[tuple[int, int], int]:
    """Per ogni campo (e ogni suo antenato), la prima pagina in cui compare un suo widget."""
    found: dict[tuple[int, int], int] = {}
    for n, page in enumerate(pdf.pages, start=1):
        for annot in page.obj.get("/Annots") or []:
            node, depth = annot, 0
            while node is not None and depth < 32:
                found.setdefault(node.objgen, n)
                node = node.get("/Parent")
                depth += 1
    return found


def _options(f) -> list[str]:
    if isinstance(f, ChoiceField):
        return [str(o.export_value) for o in f.options]
    if isinstance(f, RadioButtonGroup):
        return [str(s)[1:] for s in f.states if str(s) != "/Off"]
    return []


def _value(f):
    if isinstance(f, CheckboxField):
        return bool(f.checked)
    if isinstance(f, RadioButtonGroup):
        v = f.value
        return None if v is None or str(v) == "/Off" else str(v)[1:]
    if isinstance(f, SignatureField):
        return "firmato" if f.obj.get("/V") is not None else None
    v = f.value
    return None if v is None else str(v)


def list_fields(data: bytes, password: str | None = None) -> list[FieldInfo]:
    with open_pdf(data, password) as pdf:
        if "/AcroForm" not in pdf.Root:
            return []
        pages = _widget_pages(pdf)
        result = []
        for name, f in _form(pdf).items():
            result.append(FieldInfo(
                name=name, type=_kind(f), value=_value(f), options=_options(f), page=pages.get(f.obj.objgen),
                required=bool(f.is_required), readonly=bool(f.is_read_only),
            ))
        return result


# --- compilazione ------------------------------------------------------------------------


def read_records(path: Path) -> list[dict[str, str]]:
    """Righe da compilare: un oggetto JSON, una lista di oggetti, oppure un CSV con intestazione."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CliError(f"{path.name}: JSON non valido ({exc.msg}, riga {exc.lineno})") from None
        records = data if isinstance(data, list) else [data]
        if not all(isinstance(r, dict) for r in records):
            raise CliError(f"{path.name}: serve un oggetto {{\"campo\": valore}} o una lista di oggetti")
        return records
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise CliError(f"{path.name}: nessuna riga di dati sotto l'intestazione")
    return rows


@dataclass
class FillResult:
    filled: int
    ignored: list[str] = field(default_factory=list)
    viewer_rendered: list[str] = field(default_factory=list)  # aspetto lasciato al visualizzatore


def fill(data: bytes, target: Path | None, values: dict, *, flatten: bool = False, ignore_unknown: bool = False,
         password: str | None = None) -> FillResult:
    """Compila i campi; con target None controlla soltanto (per validare tutte le righe prima di scrivere)."""
    with open_pdf(data, password) as pdf:
        if "/AcroForm" not in pdf.Root:
            raise CliError("il PDF non ha campi da compilare (per crearli: wpdf form)")
        if _has_signatures(pdf):
            raise CliError("il PDF contiene firme digitali: modificarne i campi le renderebbe non valide")
        form = _form(pdf)
        names = [n for n, _ in form.items()]
        unknown = [k for k in values if k not in names]
        if unknown and not ignore_unknown:
            raise CliError(f"campi inesistenti: {', '.join(unknown)}. Campi del modulo: {', '.join(names)}")
        known = {k: v for k, v in values.items() if k in names}
        for name, value in known.items():
            _set(form[name], name, value)
        problems = refresh_text_appearances(pdf)
        if problems and flatten:
            raise CliError(f"caratteri non rappresentabili con il font standard nei campi: {', '.join(problems)}; "
                           "senza --flatten il visualizzatore li mostra correttamente")
        _leave_to_viewer(pdf, problems)
        if flatten:
            _flatten_widgets(pdf)
        if target is not None:
            save(pdf, target)
    return FillResult(len(known), unknown, problems)


def _leave_to_viewer(pdf: pikepdf.Pdf, names: list[str]) -> None:
    """Per i campi con caratteri fuori da cp1252: niente aspetto con «?», lo rigenera il visualizzatore."""
    if not names:
        return
    for f in pdf.acroform.fields:
        if f.fully_qualified_name in names:
            for widget in widgets_of(f.obj):
                if "/AP" in widget:
                    del widget.AP
    pdf.Root.AcroForm.NeedAppearances = True


def _set(f, name: str, value) -> None:
    if f.is_read_only:
        raise CliError(f"«{name}» è in sola lettura: il modulo non ne prevede la compilazione")
    if isinstance(value, (list, dict)):
        raise CliError(f"«{name}»: valore non valido (serve testo, numero o sì/no)")
    if isinstance(f, (RadioButtonGroup, ChoiceField)) and (value is None or str(value).strip() == ""):
        return  # cella vuota: si lascia com'è
    if isinstance(f, CheckboxField):
        f.checked = _truthy(value, name)
    elif isinstance(f, RadioButtonGroup):
        options = _options(f)
        text = str(value)
        if text not in options:
            raise CliError(f"«{name}»: valore «{text}» non previsto (scegli tra {', '.join(options)})")
        f.value = pikepdf.Name("/" + text)
    elif isinstance(f, ChoiceField):
        options = _options(f)
        text = str(value)
        if options and text not in options and not f.allow_edit:
            raise CliError(f"«{name}»: valore «{text}» non previsto (scegli tra {', '.join(options)})")
        f.value = text
    elif isinstance(f, TextField):
        text = "" if value is None else str(value)
        if f.max_length and len(text) > f.max_length:
            raise CliError(f"«{name}»: al massimo {f.max_length} caratteri")
        f.value = text
    elif isinstance(f, SignatureField):
        raise CliError(f"«{name}» è un campo firma: si compila firmando (fase firme), non con fill")
    else:
        raise CliError(f"«{name}»: tipo di campo non compilabile")


def _truthy(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise CliError(f"«{name}» è una casella: usa sì/no, true/false, 1/0 (non «{value}»)")


def _flatten_widgets(pdf: pikepdf.Pdf) -> None:
    """Rende i campi parte della pagina; le altre annotazioni (evidenziazioni, note) restano."""
    if _has_signatures(pdf):
        raise CliError("il PDF contiene firme digitali: appiattirlo le renderebbe non valide")
    pdf.generate_appearance_streams()
    problems = refresh_text_appearances(pdf)  # il generatore di qpdf scrive «?» al posto delle accentate
    if problems:
        raise CliError(f"caratteri non rappresentabili con il font standard nei campi: {', '.join(problems)}")
    stash: list[tuple[pikepdf.Page, list]] = []
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if not annots:
            continue
        others = [a for a in annots if a.get("/Subtype") != pikepdf.Name.Widget]
        if others:
            stash.append((page, others))
            page.obj.Annots = pikepdf.Array([a for a in annots if a.get("/Subtype") == pikepdf.Name.Widget])
    pdf.flatten_annotations("all")
    stashed = {page.obj.objgen: others for page, others in stash}
    for page in pdf.pages:
        # Restano solo i widget senza aspetto (per esempio un campo firma vuoto): senza modulo non servono più.
        current = [a for a in (page.obj.get("/Annots") or []) if a.get("/Subtype") != pikepdf.Name.Widget]
        current += stashed.get(page.obj.objgen, [])
        if current:
            page.obj.Annots = pikepdf.Array(current)
        elif "/Annots" in page.obj:
            del page.obj.Annots
    if "/AcroForm" in pdf.Root:
        del pdf.Root.AcroForm


def _has_signatures(pdf: pikepdf.Pdf) -> bool:
    if "/AcroForm" not in pdf.Root:
        return False
    return any(f.field_type == pikepdf.Name.Sig and "/V" in f.obj for f in pdf.acroform.fields)


def flatten(data: bytes, target: Path, password: str | None = None) -> int:
    with open_pdf(data, password) as pdf:
        count = len(pdf.acroform.fields) if "/AcroForm" in pdf.Root else 0
        _flatten_widgets(pdf)
        save(pdf, target)
    return count


RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(text: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")[:120].strip(" .")
    if name.split(".")[0].upper() in RESERVED:
        name = "_" + name  # nomi riservati di Windows
    return name or "modulo"


# --- creazione di campi --------------------------------------------------------------------

FIELD_TYPES = {"text", "date", "checkbox", "choice", "list", "radio", "signature"}
DEFAULT_SIZE = {"text": (180, 16), "date": (90, 16), "checkbox": (12, 12), "choice": (120, 16),
                "list": (120, 60), "radio": (12, 12), "signature": (160, 40)}


def add_fields(data: bytes, target: Path, spec: dict, password: str | None = None) -> int:
    from wmt_pdf.marks import find_rects

    specs = spec.get("fields") if isinstance(spec, dict) else spec
    if not isinstance(specs, list) or not specs:
        raise CliError('specifica vuota: serve {"fields": [{"name": ..., "type": ..., "rect" o "after": ...}]}')
    with open_pdf(data, password) as pdf:
        if _has_signatures(pdf):
            raise CliError("il PDF contiene firme digitali: aggiungere campi le renderebbe non valide")
        _ensure_acroform(pdf)
        existing = {f.fully_qualified_name for f in pdf.acroform.fields}
        for i, item in enumerate(specs, start=1):
            if not isinstance(item, dict):
                raise CliError(f"campo n. {i}: serve un oggetto con name, type e posizione")
            name = str(item.get("name") or "").strip()
            kind = str(item.get("type", "text"))
            if not name:
                raise CliError(f"campo n. {i}: manca il nome")
            if kind not in FIELD_TYPES:
                raise CliError(f"«{name}»: tipo «{kind}» sconosciuto (usa {', '.join(sorted(FIELD_TYPES))})")
            if "." in name:
                raise CliError(f"«{name}»: il punto nel nome separa i livelli dei campi, usa _ o -")
            if name in existing:
                raise CliError(f"«{name}»: un campo con questo nome esiste già")
            existing.add(name)
            page_no, rect = _place(pdf, data, password, item, kind, find_rects)
            page = pdf.pages[page_no - 1]
            if kind == "radio":
                _add_radio(pdf, page, name, item, rect)
            else:
                widget = _widget(pdf, page, name, kind, item, rect)
                _attach(pdf, page, widget)
        pdf.Root.AcroForm.NeedAppearances = True
        save(pdf, target)
    return len(specs)


def _ensure_acroform(pdf: pikepdf.Pdf):
    if "/AcroForm" not in pdf.Root:
        pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=pikepdf.Array()))
    acro = pdf.Root.AcroForm
    if "/Fields" not in acro:
        acro.Fields = pikepdf.Array()
    if "/DR" not in acro:
        acro.DR = pikepdf.Dictionary()
    if "/Font" not in acro.DR:
        acro.DR.Font = pikepdf.Dictionary()
    fonts = acro.DR.Font
    if "/Helv" not in fonts:
        fonts.Helv = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding))
    if "/ZaDb" not in fonts:
        fonts.ZaDb = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.ZapfDingbats))
    if "/DA" not in acro:
        acro.DA = pikepdf.String("/Helv 0 Tf 0 g")
    return acro


def _place(pdf, data, password, item, kind, find_rects) -> tuple[int, list[float]]:
    """Pagina e rettangolo: dati esplicitamente, oppure subito a destra di un'etichetta del testo."""
    name = item["name"]
    dw, dh = DEFAULT_SIZE[kind]
    w = _number(item.get("width", dw), name, "width")
    h = _number(item.get("height", dh), name, "height")
    if w <= 0 or h <= 0:
        raise CliError(f"«{name}»: width e height devono essere positivi")
    page_no = int(_number(item.get("page", 1), name, "page"))
    if not 1 <= page_no <= len(pdf.pages):
        raise CliError(f"«{name}»: pagina {page_no} fuori dal documento (1-{len(pdf.pages)})")
    if "rect" in item:
        raw = item["rect"]
        if not isinstance(raw, list) or len(raw) != 4:
            raise CliError(f"«{name}»: rect vuole [x0, y0, x1, y1] in punti")
        rect = [_number(v, name, "rect") for v in raw]
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            raise CliError(f"«{name}»: rect vuole [x0, y0, x1, y1] con x0<x1 e y0<y1 (in punti)")
    elif "after" in item:
        label = str(item["after"])
        numbers = [page_no] if "page" in item else None
        hits = find_rects(data, label, password, numbers)
        if not hits:
            raise CliError(f"«{item['name']}»: etichetta «{label}» non trovata nel testo")
        page_no = min(hits)
        occurrence = int(_number(item.get("occurrence", 1), name, "occurrence"))
        if not 1 <= occurrence <= len(hits[page_no]):
            raise CliError(f"«{item['name']}»: «{label}» compare {len(hits[page_no])} volte a pagina {page_no}")
        _, y0, x1, y1 = hits[page_no][occurrence - 1][-1]  # ultima riga dell'etichetta
        gap = _number(item.get("gap", 6), name, "gap")
        middle = (y0 + y1) / 2
        rect = [x1 + gap, middle - h / 2, x1 + gap + w, middle + h / 2]
    else:
        raise CliError(f"«{item['name']}»: indica \"rect\" oppure \"after\" (un'etichetta del testo)")
    if not 1 <= page_no <= len(pdf.pages):
        raise CliError(f"«{item['name']}»: pagina {page_no} fuori dal documento")
    return page_no, rect


def _number(value, name: str, key: str) -> float:
    if isinstance(value, bool):
        raise CliError(f"«{name}»: {key} deve essere un numero")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise CliError(f"«{name}»: {key} deve essere un numero (non «{value}»)") from None


DATE_FORMAT = re.compile(r"[dmyHhMstT/.\- :]{1,32}")


def _widget(pdf, page, name: str, kind: str, item: dict, rect) -> pikepdf.Dictionary:
    size = _number(item.get("font_size", 0), name, "font_size")
    flags = 0
    if item.get("readonly"):
        flags |= 1
    if item.get("required"):
        flags |= 2
    widget = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, Rect=rect, F=4, P=page.obj,
        T=pikepdf.String(name), MK=pikepdf.Dictionary(BC=[0.6, 0.6, 0.6]),
    )
    if item.get("tooltip"):
        widget.TU = pikepdf.String(str(item["tooltip"]))
    if kind in ("text", "date"):
        widget.FT = pikepdf.Name.Tx
        widget.DA = pikepdf.String(f"/Helv {size:g} Tf 0 g")
        if item.get("multiline"):
            flags |= 1 << 12
        if item.get("max_length"):
            widget.MaxLen = int(item["max_length"])
        if kind == "date":
            fmt = str(item.get("format", "dd/mm/yyyy"))
            if not DATE_FORMAT.fullmatch(fmt):
                # Il formato finisce in uno script del PDF: solo lettere di data e separatori.
                raise CliError(f"«{name}»: formato data «{fmt}» non valido (es. dd/mm/yyyy)")
            js = f'AFDate_FormatEx("{fmt}");'
            widget.AA = pikepdf.Dictionary(
                F=pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String(js)),
                K=pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String(f'AFDate_KeystrokeEx("{fmt}");')))
        if item.get("value") is not None:
            widget.V = pikepdf.String(str(item["value"]))
    elif kind == "checkbox":
        widget.FT = pikepdf.Name.Btn
        widget.DA = pikepdf.String("/ZaDb 0 Tf 0 g")
        widget.MK.CA = pikepdf.String("4")  # segno di spunta in ZapfDingbats
        on = _truthy(item.get("value", False), name)
        widget.V = pikepdf.Name.Yes if on else pikepdf.Name.Off
        widget.AS = widget.V
        widget.AP = _check_appearance(pdf, rect, "4")
    elif kind in ("choice", "list"):
        options = [str(o) for o in item.get("options") or []]
        if not options:
            raise CliError(f"«{name}»: un elenco vuole \"options\"")
        widget.FT = pikepdf.Name.Ch
        widget.Opt = pikepdf.Array([pikepdf.String(o) for o in options])
        widget.DA = pikepdf.String(f"/Helv {size:g} Tf 0 g")
        if kind == "choice":
            flags |= 1 << 17  # menu a tendina
            if item.get("editable"):
                flags |= 1 << 18
        if item.get("value") is not None:
            if str(item["value"]) not in options:
                raise CliError(f"«{name}»: value «{item['value']}» non è tra le options")
            widget.V = pikepdf.String(str(item["value"]))
    elif kind == "signature":
        widget.FT = pikepdf.Name.Sig
    widget.Ff = flags
    return widget


def _attach(pdf, page, widget) -> None:
    ref = pdf.make_indirect(widget)
    if "/Annots" not in page.obj:
        page.obj.Annots = pikepdf.Array()
    page.obj.Annots.append(ref)
    pdf.Root.AcroForm.Fields.append(ref)


def _check_appearance(pdf, rect, char: str) -> pikepdf.Dictionary:
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    size = min(w, h) * 0.8
    border = f"0.6 0.6 0.6 RG 0.5 w 0.25 0.25 {w - 0.5:.2f} {h - 0.5:.2f} re S".encode()
    resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(ZaDb=pdf.Root.AcroForm.DR.Font.ZaDb))
    on = pdf.make_stream(
        border + f" BT /ZaDb {size:.2f} Tf 0 g {(w - size * 0.75) / 2:.2f} {(h - size * 0.7) / 2:.2f} Td ({char}) Tj ET".encode(),
        Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, w, h], Resources=resources)
    off = pdf.make_stream(border, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, w, h])
    return pikepdf.Dictionary(N=pikepdf.Dictionary(Yes=on, Off=off))


def _add_radio(pdf, page, name: str, item: dict, rect) -> None:
    """Gruppo di scelte: "options" è una lista di {"value": ..., "rect" o "after": ...}; la prima posizione è rect."""
    options = item.get("options") or []
    if not options:
        raise CliError(f"«{name}»: un gruppo di scelte vuole \"options\" con le posizioni")
    parent = pdf.make_indirect(pikepdf.Dictionary(
        FT=pikepdf.Name.Btn, T=pikepdf.String(name), Ff=(1 << 15) | (1 << 14), Kids=pikepdf.Array(),
        V=pikepdf.Name.Off))
    values = [str(o.get("value", "")) if isinstance(o, dict) else str(o) for o in options]
    if any(not v.strip() or v == "Off" or not re.fullmatch(r"[\w\-]+", v) for v in values):
        raise CliError(f"«{name}»: ogni opzione vuole un value fatto di lettere, cifre, _ o - (e diverso da Off)")
    if len(set(values)) != len(values):
        raise CliError(f"«{name}»: due opzioni con lo stesso value")
    for k, option in enumerate(options):
        value = values[k]
        box = [_number(v, name, "rect") for v in option["rect"]] if isinstance(option, dict) and "rect" in option else (
            [rect[0] + k * (rect[2] - rect[0] + 24), rect[1], rect[2] + k * (rect[2] - rect[0] + 24), rect[3]])
        state = pikepdf.Name("/" + value)
        ap = _check_appearance(pdf, box, "l")
        ap.N[state] = ap.N.Yes
        del ap.N["/Yes"]
        kid = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, Rect=box, F=4, P=page.obj, Parent=parent,
            AS=pikepdf.Name.Off, AP=ap, MK=pikepdf.Dictionary(BC=[0.6, 0.6, 0.6], CA=pikepdf.String("l"))))
        parent.Kids.append(kid)
        if "/Annots" not in page.obj:
            page.obj.Annots = pikepdf.Array()
        page.obj.Annots.append(kid)
    pdf.Root.AcroForm.Fields.append(parent)

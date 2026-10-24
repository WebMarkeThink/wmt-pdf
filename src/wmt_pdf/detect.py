"""«Prepara modulo»: dove andrebbero i campi in un PDF piatto, ricavato da ciò che si vede sulla pagina.

Si riconoscono:
- le righe di trattini bassi ``____`` (o di puntini ``......``) del testo: campi di testo;
- i quadratini del testo (□ ☐ ❑…, anche Wingdings): caselle;
- le linee orizzontali disegnate con spazio vuoto sopra: campi di testo;
- i quadratini disegnati vuoti: caselle; i riquadri vuoti più grandi: campi di testo (su più righe se alti).

Il risultato è una proposta nella stessa forma della specifica di ``wpdf form`` (``{"fields": [...]}``), con
``rect`` in punti PDF e ``tooltip`` = l'etichetta trovata accanto: si rivede e poi si applica.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field

import pdfplumber

from wmt_pdf.i18n import t
from wmt_pdf.ops import open_pdf
from wmt_pdf.source import CliError

BOX_CHARS = set("□☐❏❐❑❒▢◻⬜◽")
WINGDINGS_BOXES = set("oqn¨")  # in Wingdings: quadratini vuoti (o, q, n) e il riquadro ¨
# Almeno due trattini bassi (anche con uno spazio in mezzo: alcuni programmi spezzano così le righe lunghe),
# oppure puntini di sospensione.
LEADER = re.compile(r"_(?: ?_)+|\.{5,}|…{2,}")
MIN_LINE = 40.0  # punti: linee più corte non sono spazi da riempire
MAX_FIELD_H = 18.0


@dataclass
class Proposal:
    page: int
    kind: str  # "text" o "checkbox"
    rect: list[float]  # [x0, y0, x1, y1] in punti PDF, origine in basso a sinistra
    label: str = ""
    multiline: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class _Line:
    top: float
    bottom: float
    chars: list[dict]


def detect_fields(data: bytes, password: str | None = None, pages: list[int] | None = None) -> dict:
    """Proposta di campi per le pagine indicate (tutte se None).

    Oltre a ``fields``: ``existing`` (i widget che il PDF ha già, per non riproporli e per mostrarli),
    ``names`` (i nomi già usati) e ``skipped`` (pagine non esaminate, con il motivo).
    """
    with open_pdf(data, password) as pdf:
        names = _field_names(pdf.Root.AcroForm.get("/Fields")) if "/AcroForm" in pdf.Root else set()
        widgets = _widget_rects(pdf)
    proposals: list[Proposal] = []
    skipped: list[dict] = []
    try:
        doc = pdfplumber.open(io.BytesIO(data), password=password or "")
    except Exception as exc:  # noqa: BLE001 - pdfminer solleva tipi diversi per i file che non sa leggere
        raise CliError(t("impossibile leggere il testo del PDF: {error}", error=exc)) from None
    with doc:
        numbers = pages or list(range(1, len(doc.pages) + 1))
        for number in numbers:
            if not 1 <= number <= len(doc.pages):
                raise CliError(t("pagina {n} fuori dal documento (1-{count})", n=number, count=len(doc.pages)))
            page = doc.pages[number - 1]
            if int(page.rotation or 0) % 360:
                # pdfplumber misura le pagine ruotate come si vedono, non nello spazio dei Rect: meglio nulla
                # che campi nel posto sbagliato. Si disegnano a mano.
                skipped.append({"page": number, "reason": t("pagina ruotata")})
                continue
            try:
                found = _page_fields(page, number)
            except Exception as exc:  # noqa: BLE001 - una pagina danneggiata non ferma le altre
                skipped.append({"page": number, "reason": str(exc) or type(exc).__name__})
                continue
            # Niente proposte sopra i campi che ci sono già (i trattini restano sotto i widget).
            proposals.extend(p for p in found if not any(
                w["page"] == number and _overlap(p.rect, w["rect"]) > 0.3 for w in widgets))
    return {"fields": _named(proposals, names), "existing": widgets, "names": sorted(names), "skipped": skipped}


def _widget_rects(pdf) -> list[dict]:
    out = []
    for number, page in enumerate(pdf.pages, start=1):
        for annot in page.obj.get("/Annots") or []:
            if annot.get("/Subtype") != "/Widget" or "/Rect" not in annot:
                continue
            x0, y0, x1, y1 = (float(v) for v in annot.Rect)
            out.append({"page": number, "rect": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]})
    return out


def _field_names(fields, prefix: str = "", depth: int = 0) -> set[str]:
    """Nomi completi dei campi che il PDF ha già (anche senza widget sulle pagine)."""
    names: set[str] = set()
    for f in fields or []:
        part = str(f.get("/T", ""))
        name = f"{prefix}.{part}" if prefix and part else part or prefix
        if name:
            names.add(name)
        if "/Kids" in f and depth < 32:
            names |= _field_names(f.Kids, name, depth + 1)
    return names


def _page_fields(page, number: int) -> list[Proposal]:
    # Su una pagina non ruotata pdfplumber dà x già nello spazio PDF e misura top dall'altezza della pagina, anche
    # con un MediaBox che non parte da 0,0 (verificato con pdfplumber 0.11): y = altezza - top.
    page_h = float(page.height)

    def to_pdf(x0: float, top: float, x1: float, bottom: float) -> list[float]:
        """Da coordinate pdfplumber (origine in alto) a punti PDF (origine in basso)."""
        return [round(x0, 2), round(page_h - bottom, 2), round(x1, 2), round(page_h - top, 2)]

    chars = [c for c in page.chars if c.get("text")]
    lines = _text_lines(chars)
    found: list[Proposal] = []

    # 1. Trattini bassi e puntini nel testo.
    for line in lines:
        text = "".join(c["text"] for c in line.chars)
        for match in LEADER.finditer(text):
            if match.group().startswith(".") and re.fullmatch(r"\s*[0-9ivxlcdm]+\s*", text[match.end():], re.IGNORECASE):
                continue  # puntini seguiti dal numero di pagina: un indice, non uno spazio da riempire
            run = line.chars[match.start():match.end()]
            size = max(float(c.get("size") or 10) for c in run)
            x0, x1 = float(run[0]["x0"]), float(run[-1]["x1"])
            base = max(float(c["bottom"]) for c in run)
            height = min(MAX_FIELD_H, size * 1.25)
            # Il campo poggia sulla riga: dal fondo dei trattini in su.
            rect = to_pdf(x0, base - height, x1, base)
            label = _label_before(line.chars, match.start())
            found.append(Proposal(number, "text", rect, label))

    # 2. Quadratini nel testo.
    for line in lines:
        for i, c in enumerate(line.chars):
            font = str(c.get("fontname") or "")
            glyph = c["text"]
            if glyph in BOX_CHARS or ("wingding" in font.lower() and glyph in WINGDINGS_BOXES):
                x0, x1, bottom = float(c["x0"]), float(c["x1"]), float(c["bottom"])
                size = float(c.get("size") or 10)
                # Il riquadro del carattere è alto quanto la riga: il quadratino disegnato sta sopra la linea di
                # base (circa 0,2 corpi sopra il fondo) ed è alto poco meno di un corpo.
                side = max(6.0, min(x1 - x0, size * 0.8))
                cx, cy = (x0 + x1) / 2, bottom - size * 0.2 - side / 2
                rect = to_pdf(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
                found.append(Proposal(number, "checkbox", rect, _label_after(line.chars, i)))

    # 3. Linee e riquadri disegnati.
    # Gli spazi non occupano: alcuni programmi ne riempiono le righe vuote sopra le linee da firmare.
    occupied = [(float(c["x0"]), float(c["top"]), float(c["x1"]), float(c["bottom"])) for c in chars if c["text"].strip()]
    occupied += [(float(i["x0"]), float(i["top"]), float(i["x1"]), float(i["bottom"])) for i in page.images]
    segments = [(float(e["x0"]), float(e["top"]), float(e["x1"]), float(e["bottom"])) for e in page.lines]
    # I programmi di grafica disegnano spesso i quadratini come tracciati (curve), non come rettangoli: valgono
    # solo i tracciati che sono proprio un rettangolo (un grafico o un logo non sono riquadri da riempire).
    shapes = list(page.rects) + [c for c in page.curves if c.get("stroke") and _is_box(c)]
    for r in shapes:
        x0, top, x1, bottom = float(r["x0"]), float(r["top"]), float(r["x1"]), float(r["bottom"])
        w, h = x1 - x0, bottom - top
        if h <= 1.5 and w >= MIN_LINE:
            segments.append((x0, top, x1, bottom))  # rettangolo sottile: è una linea
        elif not r.get("stroke"):
            continue  # solo riempito: uno sfondo, non un riquadro da riempire
        elif 5 <= w <= 20 and 5 <= h <= 20 and abs(w - h) <= 3 and _empty((x0, top, x1, bottom), occupied):
            found.append(Proposal(number, "checkbox", to_pdf(x0 + 1, top + 1, x1 - 1, bottom - 1),
                                  _box_label(lines, x0, x1, (top + bottom) / 2)))
        elif w >= 50 and 12 <= h and w < page.width * 0.9 and _empty((x0, top, x1, bottom), occupied):
            found.append(Proposal(number, "text", to_pdf(x0 + 1, top + 1, x1 - 1, bottom - 1),
                                  _label_near(lines, x0, (top + bottom) / 2, above=(x0, x1, top)),
                                  multiline=h > 30))
        elif w >= 100 and h >= 40 and w < page.width * 0.95 and h < page.height * 0.6:  # non la cornice della pagina
            # Riquadro con una domanda in alto e spazio vuoto sotto («Segnalo le seguenti circostanze:»).
            inside = [b for a0, atop, a1, b in occupied if a0 >= x0 - 1 and a1 <= x1 + 1 and atop >= top - 1 and b <= bottom + 1]
            last = max(inside, default=None)
            if last is not None and bottom - last >= 24:
                label_line = next((ln for ln in lines if abs(ln.bottom - last) < 1), None)
                label = _clean("".join(c["text"] for c in label_line.chars)) if label_line else ""
                area = to_pdf(x0 + 4, last + 3, x1 - 4, bottom - 3)
                found.append(Proposal(number, "text", area, label, multiline=True, extra={"spare": True}))
    for x0, top, x1, bottom in segments:
        if x1 - x0 < MIN_LINE or abs(bottom - top) > 1.5 or x1 - x0 > page.width * 0.8:
            continue  # troppo corta, o da margine a margine (un separatore, il bordo di una tabella)
        # Il campo occupa lo spazio libero sopra la linea, fino al testo più vicino (al massimo MAX_FIELD_H).
        # Testo che comincia sopra la linea, anche se la tocca (un collegamento sottolineato): ne conta il fondo.
        above = [min(b, top) for a0, atop, a1, b in occupied if a0 < x1 - 1 and a1 > x0 + 1 and atop < top]
        height = min(MAX_FIELD_H, top - max(above, default=-1e9) - 1)
        if height < 8:
            continue  # testo subito sopra: è una sottolineatura o il bordo di una tabella piena
        area = (x0, top - height, x1, top - 0.5)
        label = _label_near(lines, x0, top - height / 2, above=(x0, x1, top - height))
        if not label:
            continue  # una linea senza niente accanto è un filetto decorativo, non uno spazio da riempire
        found.append(Proposal(number, "text", to_pdf(*area), label))

    regular = [p for p in found if not p.extra.get("spare")]
    spare = [p for p in found if p.extra.get("spare") and not any(
        q.page == p.page and _overlap(p.rect, q.rect) > 0 for q in regular)]
    return _merge_stacked(_dedupe(regular + spare))


def _is_box(curve: dict) -> bool:
    """Un tracciato che passa solo per i quattro angoli del suo riquadro: un rettangolo disegnato a mano."""
    x0, x1, top, bottom = (float(curve[k]) for k in ("x0", "x1", "top", "bottom"))
    points = curve.get("pts") or []
    if not 4 <= len(points) <= 8:
        return False
    near = lambda v, a, b: abs(v - a) < 0.8 or abs(v - b) < 0.8
    return all(near(float(x), x0, x1) and near(float(y), top, bottom) for x, y in points)


def _text_lines(chars: list[dict]) -> list[_Line]:
    """Caratteri raggruppati per riga (stessa altezza), da sinistra a destra."""
    lines: list[_Line] = []
    for c in sorted(chars, key=lambda c: (round(float(c["top"])), float(c["x0"]))):
        top, bottom = float(c["top"]), float(c["bottom"])
        for line in lines:
            overlap = min(bottom, line.bottom) - max(top, line.top)
            if overlap > 0.5 * min(bottom - top, line.bottom - line.top):
                line.chars.append(c)
                line.top, line.bottom = min(top, line.top), max(bottom, line.bottom)
                break
        else:
            lines.append(_Line(top, bottom, [c]))
    for line in lines:
        line.chars.sort(key=lambda c: float(c["x0"]))
    return sorted(lines, key=lambda line: line.top)


def _clean(text: str) -> str:
    text = LEADER.sub(" ", text)
    text = "".join(ch for ch in text if ch not in BOX_CHARS)
    return re.sub(r"\s+", " ", text).strip(" :.-–—_|")


def _words(chars: list[dict]) -> list[list[dict]]:
    """Spezzoni di testo separati da spazi larghi (più colonne sulla stessa riga)."""
    groups: list[list[dict]] = []
    for c in chars:
        if groups:
            prev = groups[-1][-1]
            gap = float(c["x0"]) - float(prev["x1"])
            if gap <= float(c.get("size") or 10) * 1.5 and not LEADER.fullmatch(prev["text"] + c["text"]):
                groups[-1].append(c)
                continue
        groups.append([c])
    return groups


def _label_before(chars: list[dict], index: int) -> str:
    """L'etichetta di un campo: il testo che lo precede sulla riga, fino al campo precedente."""
    before = chars[:index]
    text = "".join(c["text"] for c in before)
    last = None
    for match in LEADER.finditer(text):
        last = match
    if last:
        before = before[last.end():]
    if not before:
        return ""
    return _clean("".join(c["text"] for c in _words(before)[-1]) if _words(before) else "")


def _label_after(chars: list[dict], index: int) -> str:
    """L'etichetta di una casella: il testo che la segue, fino alla casella successiva o a uno spazio largo."""
    after = []
    for c in chars[index + 1:]:
        if c["text"] in BOX_CHARS or LEADER.fullmatch(c["text"] * 3):
            break
        after.append(c)
    groups = _words(after)
    return _clean("".join(c["text"] for c in groups[0])) if groups else ""


def _label_near(lines: list[_Line], x: float, middle: float, after: bool = False,
                above: tuple[float, float, float] | None = None) -> str:
    """Il testo accanto (a sinistra, o a destra con after); se non c'è, quello subito sopra (above: x0, x1, top)."""
    for line in lines:
        if line.top - 2 <= middle <= line.bottom + 2:
            if after:
                rest = [c for c in line.chars if float(c["x0"]) >= x]
                groups = _words(rest)
                return _clean("".join(c["text"] for c in groups[0])) if groups else ""
            before = [c for c in line.chars if float(c["x1"]) <= x and c["text"].strip()]
            groups = _words(before)
            if groups and (label := _clean("".join(c["text"] for c in groups[-1]))):
                return label
    if above:
        x0, x1, top = above
        for line in reversed(lines):
            if top - 16 <= line.bottom <= top + 1:
                over = [c for c in line.chars if float(c["x1"]) > x0 and float(c["x0"]) < x1]
                groups = _words(over)
                if groups:
                    return _clean("".join(c["text"] for c in groups[0]))
    return ""


def _box_label(lines: list[_Line], x0: float, x1: float, middle: float) -> str:
    """Il testo dopo la casella; se è una risposta breve (SI, NO, X…) gli si premette la domanda della riga."""
    after = _label_near(lines, x1, middle, after=True)
    if len(after) > 3:
        return after
    for line in lines:
        if line.top - 2 <= middle <= line.bottom + 2:
            groups = [_clean("".join(c["text"] for c in g)) for g in _words([c for c in line.chars if float(c["x1"]) <= x0])]
            question = next((g for g in reversed(groups) if len(g) > 3), "")
            return f"{question} {after}".strip()
    return after


def _empty(area: tuple[float, float, float, float], occupied: list[tuple[float, float, float, float]]) -> bool:
    x0, top, x1, bottom = area
    for a0, atop, a1, abottom in occupied:
        if a0 < x1 - 1 and a1 > x0 + 1 and atop < bottom - 1 and abottom > top + 1:
            return False
    return True


def _overlap(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if smaller else 0.0


def _dedupe(found: list[Proposal]) -> list[Proposal]:
    """Trattini e linea disegnata nello stesso punto: un campo solo (vince il primo, dal testo)."""
    kept: list[Proposal] = []
    for p in found:
        if any(q.page == p.page and _overlap(p.rect, q.rect) > 0.5 for q in kept):
            continue
        kept.append(p)
    return kept


def _merge_stacked(found: list[Proposal]) -> list[Proposal]:
    """Più righe da riempire una sotto l'altra, senza etichetta dalla seconda in poi: un campo su più righe."""
    texts = sorted((p for p in found if p.kind == "text"), key=lambda p: (-p.rect[3], p.rect[0]))
    others = [p for p in found if p.kind != "text"]
    merged: list[Proposal] = []
    for p in texts:
        # Il campo subito sopra nella stessa colonna (con due colonne le righe si alternano nell'ordine).
        prev = next((q for q in reversed(merged) if q.page == p.page and abs(q.rect[0] - p.rect[0]) <= 12
                     and abs(q.rect[2] - p.rect[2]) <= 40 and 0 <= q.rect[1] - p.rect[3] <= 14), None)
        if prev and not p.label:
            prev.rect = [min(prev.rect[0], p.rect[0]), p.rect[1], max(prev.rect[2], p.rect[2]), prev.rect[3]]
            prev.multiline = True
            continue
        merged.append(p)
    return sorted(merged + others, key=lambda p: (p.page, -p.rect[3], p.rect[0]))


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug[:40].rstrip("_")


def _named(found: list[Proposal], existing: set[str]) -> list[dict]:
    """Nomi dai testi accanto, unici (anche rispetto ai campi che il PDF ha già)."""
    taken = {n.casefold() for n in existing}
    out = []
    for n, p in enumerate(found, start=1):
        base = _slug(p.label) or (f"casella_{n}" if p.kind == "checkbox" else f"campo_{n}")
        name, k = base, 2
        while name.casefold() in taken:
            name, k = f"{base}_{k}", k + 1
        taken.add(name.casefold())
        item = {"name": name, "type": p.kind, "page": p.page, "rect": p.rect}
        if p.label:
            item["tooltip"] = p.label
        if p.multiline:
            item["multiline"] = True
        out.append(item)
    return out

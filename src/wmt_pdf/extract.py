"""Estrazione del testo pagina per pagina, con tabelle in Markdown e pochi token."""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

# Fascia in alto e in basso dove cercare intestazioni e piè di pagina ripetuti.
MARGIN_BAND = 0.08
# Una pagina con meno caratteri di così e almeno un'immagine è trattata come scansione.
MIN_TEXT_CHARS = 10
# Oggetti grafici (linee, rettangoli) oltre i quali vale la pena cercare tabelle.
MIN_PATHS_FOR_TABLES = 4
# Pezzi ripetuti più corti di così ("€", "1") non contano come intestazioni.
MIN_REPEATED_CHARS = 4
# Nelle tabelle vere la cella tipica ha poche parole; oltre è un riquadro grafico con testo.
MAX_MEDIAN_WORDS_PER_CELL = 4
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1 if text else 0


@dataclass
class PageText:
    page: int
    text: str
    chars: int
    images: int
    tables: list[list[list[str]]] = field(default_factory=list)
    ocr: bool = False
    note: str | None = None

    @property
    def scanned(self) -> bool:
        return is_scanned(self.chars, self.images)


def is_scanned(chars: int, images: int) -> bool:
    return chars < MIN_TEXT_CHARS and images > 0


def is_blank(chars: int, images: int) -> bool:
    return chars < MIN_TEXT_CHARS and images == 0


@dataclass
class PageStats:
    chars: int
    images: int
    paths: int
    margin_lines: list[str]


def page_stats(page: pdfium.PdfPage) -> tuple[str, PageStats]:
    """Testo grezzo della pagina e conteggi utili, senza aprire pdfplumber."""
    textpage = page.get_textpage()
    try:
        raw = textpage.get_text_range()
        height = page.get_height()
        margin_lines = []
        for i in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(i)
            if top > height * (1 - MARGIN_BAND) or bottom < height * MARGIN_BAND:
                chunk = textpage.get_text_bounded(left, bottom, right, top).strip()
                if chunk:
                    margin_lines.append(chunk)
    finally:
        textpage.close()
    images = paths = 0
    for obj in page.get_objects(max_depth=2):
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            images += 1
        elif obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:
            paths += 1
    text = clean(raw)
    chars = len(re.sub(r"\s", "", text))
    return text, PageStats(chars, images, paths, margin_lines)


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x02", "-").replace("\ufffe", "-")
    text = text.replace("\xa0", " ")  # spazi non separabili: per chi cerca e legge sono spazi
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip().casefold()))


def repeated_margin_lines(stats: list[PageStats]) -> set[str]:
    """Righe nei margini presenti in almeno il 60% delle pagine (minimo 3 pagine)."""
    if len(stats) < 3:
        return set()
    counter: Counter[str] = Counter()
    for s in stats:
        counter.update({_norm(line) for line in s.margin_lines})
    threshold = max(3, int(len(stats) * 0.6 + 0.999))
    return {
        line
        for line, n in counter.items()
        if n >= threshold and len(line) >= MIN_REPEATED_CHARS and sum(c.isalpha() for c in line) >= 2
    }


def strip_lines(text: str, repeated: AbstractSet[str]) -> str:
    """Toglie le righe fatte solo di pezzi ripetuti (PDFium può spezzarle in modo diverso)."""
    if not repeated:
        return text
    chunks = sorted(repeated, key=len, reverse=True)

    def only_repeated(line: str) -> bool:
        rest = _norm(line)
        if not rest or not any(c in rest for c in chunks):
            return False
        for c in chunks:
            rest = rest.replace(c, " ")
        return not rest.strip(" -–·.,;:")

    return clean("\n".join(line for line in text.split("\n") if not only_repeated(line)))


def extract_tables(plumber_page) -> tuple[list[list[list[str]]], list[tuple[float, float, float, float]]]:
    tables, boxes = [], []
    for table in plumber_page.find_tables():
        rows = [[_cell(c) for c in row] for row in table.extract()]
        rows = [r for r in rows if any(r)]
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2 and looks_like_table(rows):
            tables.append(rows)
            boxes.append(table.bbox)
    return tables, boxes


def looks_like_table(rows: list[list[str]]) -> bool:
    """Scarta i riquadri grafici che pdfplumber scambia per tabelle."""
    cells = [c for r in rows for c in r]
    filled = [c for c in cells if c]
    if len(filled) < len(cells) * 0.5:
        return False
    words = sorted(len(c.split()) for c in filled)
    return words[len(words) // 2] <= MAX_MEDIAN_WORDS_PER_CELL


def text_with_tables(plumber_page, tables, boxes, repeated: AbstractSet[str] = frozenset()) -> str:
    """Testo fuori dalle tabelle, con le tabelle in Markdown al loro posto verticale."""

    def inside(obj) -> bool:
        cx = (obj["x0"] + obj["x1"]) / 2
        cy = (obj["top"] + obj["bottom"]) / 2
        return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

    outside = plumber_page.filter(lambda obj: obj.get("object_type") != "char" or not inside(obj))
    # Le righe ripetute si tolgono solo dal testo, mai dalle tabelle.
    blocks = [(line["top"], line["text"]) for line in outside.extract_text_lines()]
    blocks = [(top, text) for top, text in blocks if strip_lines(text, repeated)]
    blocks += [(box[1], to_markdown(rows)) for rows, box in zip(tables, boxes)]
    blocks.sort(key=lambda b: b[0])
    return clean("\n".join(text for _, text in blocks))


def _cell(value) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def to_markdown(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    esc = [[c.replace("|", "\\|") for c in r] for r in rows]
    lines = ["| " + " | ".join(esc[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in esc[1:]]
    return "\n".join(lines)


def to_csv(rows: list[list[str]]) -> str:
    import csv

    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().rstrip("\n")


def fold(text: str) -> str:
    """Minuscole e senza accenti, per cercare senza badare a maiuscole e accenti."""
    return fold_with_map(text)[0]


def fold_with_map(text: str) -> tuple[str, list[int]]:
    """Come fold, più per ogni carattere piegato l'indice del carattere originale."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKD", ch.casefold()):
            if not unicodedata.combining(c):
                out.append(c)
                index.append(i)
    return "".join(out), index


class Extractor:
    """Estrae le pagine richieste di un documento già aperto."""

    def __init__(self, doc: pdfium.PdfDocument, data: bytes, *, tables: bool = True, password: str | None = None):
        self.doc = doc
        self.data = data
        self.tables = tables
        self.password = password
        self._plumber: Any = None

    def plumber_page(self, number: int):
        if self._plumber is None:
            import pdfplumber

            self._plumber = pdfplumber.open(io.BytesIO(self.data), password=self.password or "")
        return self._plumber.pages[number - 1]

    def close(self) -> None:
        if self._plumber is not None:
            self._plumber.close()

    def stats(self, numbers: list[int]) -> dict[int, tuple[str, PageStats]]:
        out = {}
        for n in numbers:
            page = self.doc[n - 1]
            try:
                out[n] = page_stats(page)
            finally:
                page.close()
        return out

    def pages(self, numbers: list[int], *, keep_headers: bool = False, ocr: str = "auto", lang: str = "it"):
        """Restituisce (pagine, righe ripetute rimosse)."""
        raw = self.stats(numbers)
        repeated = set() if keep_headers else repeated_margin_lines([s for _, s in raw.values()])
        removed = _originals(raw, repeated)
        result = []
        for n in numbers:
            text, st = raw[n]
            item = PageText(page=n, text=text, chars=st.chars, images=st.images)
            if self.tables and st.paths >= MIN_PATHS_FOR_TABLES and st.chars:
                plumber_page = self.plumber_page(n)
                tables, boxes = extract_tables(plumber_page)
                if tables:
                    item.tables = tables
                    item.text = text_with_tables(plumber_page, tables, boxes, repeated)
                plumber_page.close()  # libera la cache di pdfplumber, conta sui documenti lunghi
            if not item.tables:
                item.text = strip_lines(item.text, repeated)
            if (ocr == "force") or (ocr == "auto" and item.scanned):
                self._apply_ocr(item, lang, required=ocr == "force")
            elif item.scanned:
                item.note = (
                    f"pagina senza testo, con {st.images} immagini: probabilmente una scansione. "
                    f"Usa --ocr force oppure `wpdf render --page {n}`"
                )
            result.append(item)
        return result, removed

    def _apply_ocr(self, item: PageText, lang: str, required: bool) -> None:
        from wmt_pdf import ocr

        if not ocr.available():
            msg = "OCR non installato: `uv sync --extra ocr` (oppure pip install 'wmt-pdf[ocr]')"
            if required:
                from wmt_pdf.source import CliError

                raise CliError(msg)
            item.note = f"pagina senza testo, con {item.images} immagini. {msg}"
            return
        page = self.doc[item.page - 1]
        try:
            text = ocr.page_text(page, lang=lang)
        finally:
            page.close()
        item.text = clean(text)
        item.chars = len(re.sub(r"\s", "", item.text))
        item.ocr = True


def _originals(raw: dict[int, tuple[str, PageStats]], repeated: set[str]) -> list[str]:
    """Una versione leggibile di ogni riga ripetuta, per la nota in testa."""
    found: dict[str, str] = {}
    for _, st in raw.values():
        for line in st.margin_lines:
            key = _norm(line)
            if key in repeated and key not in found:
                found[key] = line.strip()
    return list(found.values())

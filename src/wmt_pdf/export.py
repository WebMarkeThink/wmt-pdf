"""Esportazione da PDF verso Word, Excel, HTML, Markdown, testo e immagini."""

from __future__ import annotations

import html as htmllib
import re
from pathlib import Path

from wmt_pdf.extract import Extractor, PageText
from wmt_pdf.source import CliError, Source

TEXT_FORMATS = {"docx", "xlsx", "html", "md", "txt"}
IMAGE_FORMATS = {"png", "jpg", "tiff"}

# Caratteri che XML (quindi Word ed Excel) non accetta: capitano nel testo estratto dai PDF.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def xml_safe(text: str) -> str:
    return _CONTROL.sub("", text)


_NUMBER = re.compile(r"^-?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")


def parse_number(value: str):
    """'1.500,50' → 1500.5; codici con zeri iniziali, percentuali e testo restano stringhe."""
    text = value.strip().replace("€", "").strip()
    if not _NUMBER.match(text) or (len(text) > 1 and text.startswith("0") and not text.startswith("0,")):
        return value
    number = float(text.replace(".", "").replace(",", "."))
    return int(number) if number.is_integer() and "," not in text else number


def extract_pages(source: Source, password: str | None, ocr: str = "auto") -> list[PageText]:
    doc = source.open(password)
    extractor = Extractor(doc, source.data, password=password)
    try:
        pages, _ = extractor.pages(list(range(1, len(doc) + 1)), ocr=ocr)
    finally:
        extractor.close()
        doc.close()
    return pages


def export(source: Source, fmt: str, target: Path, *, password: str | None = None, dpi: int = 150,
           ocr: str = "auto") -> list[Path]:
    if fmt in IMAGE_FORMATS:
        return export_images(source, fmt, target, dpi=dpi, password=password)
    if fmt not in TEXT_FORMATS:
        raise CliError(f"formato di esportazione sconosciuto: {fmt}")
    pages = extract_pages(source, password, ocr)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "docx":
        _to_docx(pages, target, source.name)
    elif fmt == "xlsx":
        _to_xlsx(pages, target)
    elif fmt == "html":
        target.write_text(_to_html(pages, source.name), encoding="utf-8")
    elif fmt == "md":
        target.write_text(_to_md(pages, source.name), encoding="utf-8")
    else:
        target.write_text("\n\n\f".join(p.text for p in pages) + "\n", encoding="utf-8")
    return [target]


def _blocks(page: PageText):
    """Il testo della pagina diviso in paragrafi e tabelle Markdown, nell'ordine."""
    buffer: list[str] = []
    table: list[str] = []
    for line in page.text.split("\n"):
        if line.startswith("|"):
            if buffer:
                yield "text", "\n".join(buffer)
                buffer = []
            table.append(line)
            continue
        if table:
            yield "table", table
            table = []
        buffer.append(line)
    if table:
        yield "table", table
    if buffer and "\n".join(buffer).strip():
        yield "text", "\n".join(buffer)


def _md_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        if re.match(r"^\|(-+\|)+$", line.replace(" ", "")):
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        rows.append(cells)
    return rows


def _to_docx(pages: list[PageText], target: Path, title: str) -> None:
    import docx

    d = docx.Document()
    d.core_properties.title = title
    for i, page in enumerate(pages):
        if i:
            d.add_page_break()
        for kind, block in _blocks(page):
            if kind == "text":
                for para in re.split(r"\n\s*\n", block.strip()):
                    if para.strip():
                        d.add_paragraph(xml_safe(para.replace("\n", " ").strip()))
            else:
                rows = _md_rows(block)
                width = max(len(r) for r in rows)
                table = d.add_table(rows=len(rows), cols=width)
                table.style = "Table Grid"
                for r, row in enumerate(rows):
                    for c, value in enumerate(row):
                        table.cell(r, c).text = xml_safe(value)
        if page.note:
            d.add_paragraph(xml_safe(f"[{page.note}]"))
    d.save(target)


def _to_xlsx(pages: list[PageText], target: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for page in pages:
        for k, rows in enumerate(page.tables, start=1):
            ws = wb.create_sheet(f"Pagina {page.page}" + (f" ({k})" if len(page.tables) > 1 else ""))
            for row in rows:
                ws.append([parse_number(xml_safe(c)) for c in row])
    if not wb.worksheets:
        raise CliError("nessuna tabella nel PDF da esportare in Excel: prova --to docx o md")
    wb.save(target)


def _to_html(pages: list[PageText], title: str) -> str:
    out = [f"<!doctype html><html lang='it'><head><meta charset='utf-8'><title>{htmllib.escape(title)}</title>",
           ("<style>body{font-family:sans-serif;max-width:50em;margin:2em auto;line-height:1.5}"
           "table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px 8px}</style></head><body>")]
    for page in pages:
        out.append(f"<section><h2>Pagina {page.page}</h2>")
        for kind, block in _blocks(page):
            if kind == "text":
                for para in re.split(r"\n\s*\n", block.strip()):
                    if para.strip():
                        out.append("<p>" + "<br>".join(htmllib.escape(x) for x in para.split("\n")) + "</p>")
            else:
                rows = _md_rows(block)
                out.append("<table>")
                for r, row in enumerate(rows):
                    tag = "th" if r == 0 else "td"
                    out.append("<tr>" + "".join(f"<{tag}>{htmllib.escape(c)}</{tag}>" for c in row) + "</tr>")
                out.append("</table>")
        out.append("</section>")
    out.append("</body></html>")
    return "\n".join(out)


def _to_md(pages: list[PageText], title: str) -> str:
    parts = [f"# {title}"]
    for page in pages:
        parts.append(f"\n## Pagina {page.page}\n\n{page.text}")
    return "\n".join(parts) + "\n"


def single_file(fmt: str, target: Path) -> bool:
    """tiff verso un file .tif/.tiff è un unico file multipagina; png e jpg vanno in una cartella."""
    return fmt not in IMAGE_FORMATS or (fmt == "tiff" and target.suffix.lower() in (".tif", ".tiff"))


def image_paths(source: Source, fmt: str, target: Path, count: int) -> list[Path]:
    stem = Path(source.name).stem.removesuffix(".pdf")
    return [target / f"{stem}_p{n}.{fmt}" for n in range(1, count + 1)]


def _render(doc, n: int, dpi: int):
    page = doc[n - 1]
    try:
        return page.render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        page.close()


def export_images(source: Source, fmt: str, target: Path, *, dpi: int, password: str | None) -> list[Path]:
    doc = source.open(password)
    try:
        count = len(doc)
        if single_file(fmt, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            # Il TIFF multipagina vuole tutte le immagini insieme: si generano una alla volta.
            first = _render(doc, 1, dpi)
            rest = (_render(doc, n, dpi) for n in range(2, count + 1))
            first.save(target, save_all=True, append_images=rest, compression="tiff_deflate", dpi=(dpi, dpi))
            return [target]
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for n, path in enumerate(image_paths(source, fmt, target, count), start=1):
            options = {"quality": 85} if fmt == "jpg" else {}
            _render(doc, n, dpi).save(path, dpi=(dpi, dpi), **options)  # una pagina alla volta in memoria
            written.append(path)
        return written
    finally:
        doc.close()

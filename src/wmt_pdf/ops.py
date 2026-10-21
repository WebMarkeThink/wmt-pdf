"""Operazioni strutturali sui PDF con pikepdf: unione, divisione, pagine, rotazione, metadati, password."""

from __future__ import annotations

import io
import re
import secrets
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from wmt_pdf.i18n import t
from wmt_pdf.pages import parse_pages
from wmt_pdf.source import CliError


def open_pdf(data: bytes, password: str | None = None) -> pikepdf.Pdf:
    try:
        with warnings.catch_warnings():
            # Con merge la password vale per tutti i file: per quelli non protetti non serve, e va bene così.
            warnings.filterwarnings("ignore", message="A password was provided")
            return pikepdf.open(io.BytesIO(data), password=password or "")
    except pikepdf.PasswordError:
        raise CliError(t("PDF protetto da password: usa --password o --password-env")) from None
    except pikepdf.PdfError as exc:
        raise CliError(t("PDF non leggibile: {exc}", exc=exc)) from None


def save(pdf: pikepdf.Pdf, target: Path, *, source: pikepdf.Pdf | None = None, password: str | None = None,
         **kwargs) -> int:
    """Salva conservando la protezione: un PDF con password resta con password (toglierla è compito di unlock)."""
    if "encryption" not in kwargs:
        kwargs["encryption"] = keep_encryption(pdf, source, password)
    target.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(target, **kwargs)
    return target.stat().st_size


def keep_encryption(pdf: pikepdf.Pdf, source: pikepdf.Pdf | None, password: str | None):
    if pdf.is_encrypted:
        return True  # stesse impostazioni del file aperto
    if source is not None and source.is_encrypted and password:
        # Documento nuovo ricavato da uno protetto: stessa password di apertura e stessi permessi.
        return pikepdf.Encryption(user=password, owner=secrets.token_urlsafe(24), R=6, allow=source.allow)
    return False


@dataclass
class Part:
    title: str
    data: bytes
    password: str | None = None


@dataclass
class Outline:
    title: str
    page: int | None  # indice (da 0) nel documento di partenza
    children: list[Outline]


def read_outline(pdf: pikepdf.Pdf) -> list[Outline]:
    """Segnalibri del documento con la pagina di destinazione risolta (anche le destinazioni con nome)."""
    index = {page.obj.objgen: i for i, page in enumerate(pdf.pages)}

    def target(item) -> int | None:
        dest = item.destination
        if dest is None and item.action is not None and item.action.get("/S") == pikepdf.Name.GoTo:
            dest = item.action.get("/D")
        if isinstance(dest, (pikepdf.Name, pikepdf.String)):
            # Un Name ha la barra iniziale come sintassi; una String la può avere come parte del nome.
            dest = _named_destination(pdf, str(dest)[1:] if isinstance(dest, pikepdf.Name) else str(dest))
        if isinstance(dest, pikepdf.Dictionary):
            dest = dest.get("/D")
        if isinstance(dest, pikepdf.Array) and len(dest) and isinstance(dest[0], pikepdf.Dictionary):
            return index.get(dest[0].objgen)
        return None

    def walk(items, depth=0) -> list[Outline]:
        if depth > 32:
            return []
        return [Outline(str(i.title), target(i), walk(i.children, depth + 1)) for i in items]

    try:
        with pdf.open_outline() as outline:
            return walk(outline.root)
    except pikepdf.OutlineStructureError:
        return []


def _named_destination(pdf: pikepdf.Pdf, name: str):
    try:
        names = pdf.Root.get("/Names")
        if names is not None and "/Dests" in names:
            tree = pikepdf.NameTree(names.Dests)
            if name in tree:
                return tree[name]
        dests = pdf.Root.get("/Dests")
        if dests is not None and ("/" + name) in dests:
            return dests["/" + name]
    except (pikepdf.PdfError, KeyError, ValueError):
        return None
    return None


def build_outline(items: list[Outline], page_map: dict[int, int]) -> list[pikepdf.OutlineItem]:
    """Segnalibri per il documento nuovo: restano quelli che puntano a pagine riportate, nel nuovo ordine."""
    return [node for _, node in _build(items, page_map)]


def _build(items: list[Outline], page_map: dict[int, int]) -> list[tuple[int, pikepdf.OutlineItem]]:
    built: list[tuple[int, pikepdf.OutlineItem]] = []
    for item in items:
        children = _build(item.children, page_map)
        new_page = page_map.get(item.page) if item.page is not None else None
        if new_page is None and children:
            new_page = children[0][0]  # capitolo tolto ma con sottocapitoli rimasti: punta al primo
        if new_page is None:
            continue
        node = pikepdf.OutlineItem(item.title, new_page)
        node.children.extend(child for _, child in children)
        built.append((new_page, node))
    built.sort(key=lambda pair: pair[0])
    return built


def copy_pages(out: pikepdf.Pdf, src: pikepdf.Pdf, numbers: list[int]) -> dict[int, int]:
    """Copia le pagine con i loro campi modulo; restituisce indice di partenza → nuovo indice (da 0).

    add_pages_from porta i campi (rinominando i doppioni: nome, nome+1, ...) e le destinazioni con nome.
    """
    start = len(out.pages)
    out.add_pages_from(src, pages=[n - 1 for n in numbers])
    page_map: dict[int, int] = {}
    for offset, n in enumerate(numbers):
        page_map.setdefault(n - 1, start + offset)
    return page_map


@dataclass
class MergeResult:
    pages: int
    renamed_fields: list[str]


def merge(parts: Iterable[Part], target: Path, bookmarks: bool = True) -> MergeResult:
    """Unisce i PDF nell'ordine dato: un segnalibro per file, con dentro i segnalibri del file; campi conservati."""
    out = pikepdf.new()
    opened = []  # pikepdf vuole i sorgenti aperti fino al salvataggio
    protected: tuple[pikepdf.Pdf, str] | None = None
    original_names: set[str] = set()
    try:
        tops: list[pikepdf.OutlineItem] = []
        for part in parts:
            src = open_pdf(part.data, part.password)
            opened.append(src)
            if src.is_encrypted and part.password and protected is None:
                protected = (src, part.password)
            if "/AcroForm" in src.Root:
                original_names |= {f.fully_qualified_name for f in src.acroform.fields}
            start = len(out.pages)
            page_map = copy_pages(out, src, list(range(1, len(src.pages) + 1)))
            if bookmarks:
                node = pikepdf.OutlineItem(part.title, start)
                node.children.extend(build_outline(read_outline(src), page_map))
                tops.append(node)
        if bookmarks:
            with out.open_outline() as outline:
                outline.root.extend(tops)
        renamed = []
        if "/AcroForm" in out.Root:
            renamed = sorted(f.fully_qualified_name for f in out.acroform.fields
                             if f.fully_qualified_name not in original_names)
        # Se almeno un file era protetto, il risultato resta protetto con la stessa password.
        save(out, target, source=protected[0] if protected else None, password=protected[1] if protected else None)
        return MergeResult(len(out.pages), renamed)
    finally:
        for src in opened:
            src.close()
        out.close()


def split_groups(count: int, every: int | None = None, ranges: str | None = None) -> list[list[int]]:
    if every:
        if every < 1:
            raise CliError(t("--every vuole un numero positivo"))
        return [list(range(s, min(s + every, count + 1))) for s in range(1, count + 1, every)]
    if ranges:
        groups = [parse_pages(chunk, count) for chunk in ranges.split(";") if chunk.strip()]
        labels = [(g[0], g[-1]) for g in groups]
        if len(set(labels)) != len(labels):
            raise CliError(t("--ranges contiene due gruppi uguali: il secondo file sostituirebbe il primo"))
        return groups
    raise CliError(t("indica --every N oppure --ranges \"1-3;4-9\""))


def _derived(src: pikepdf.Pdf, numbers: list[int], target: Path, password: str | None) -> None:
    """Documento nuovo con le pagine scelte, i loro campi e i segnalibri che vi puntano."""
    with pikepdf.new() as out:
        page_map = copy_pages(out, src, numbers)
        items = build_outline(read_outline(src), page_map)
        if items:
            with out.open_outline() as outline:
                outline.root.extend(items)
        save(out, target, source=src, password=password)


def split(data: bytes, groups: list[list[int]], outdir: Path, stem: str, password: str | None = None) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    with open_pdf(data, password) as src:
        for group in groups:
            label = f"{group[0]}-{group[-1]}" if len(group) > 1 else str(group[0])
            target = outdir / f"{stem}_{label}.pdf"
            _derived(src, group, target, password)
            written.append(target)
    return written


def select_pages(data: bytes, numbers: list[int], target: Path, password: str | None = None) -> int:
    """Nuovo PDF con le pagine indicate, nell'ordine indicato (anche ripetute)."""
    with open_pdf(data, password) as src:
        _derived(src, numbers, target, password)
    return len(numbers)


def page_order(spec: str, count: int) -> list[int]:
    """Come parse_pages ma conserva ordine e ripetizioni: "3,1,2-4", e "5-3" va all'indietro."""
    result: list[int] = []
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        backwards = re.fullmatch(r"(\d+)-(\d+)", chunk)
        if backwards and int(backwards[1]) > int(backwards[2]):
            result.extend(reversed(parse_pages(f"{backwards[2]}-{backwards[1]}", count)))
        else:
            result.extend(parse_pages(chunk, count))
    return result


def rotate(data: bytes, angle: int, numbers: list[int], target: Path, password: str | None = None) -> None:
    if angle % 90:
        raise CliError(t("--angle deve essere un multiplo di 90"))
    with open_pdf(data, password) as pdf:
        for n in numbers:
            pdf.pages[n - 1].rotate(angle, relative=True)
        save(pdf, target)


META_FIELDS = {"title": "dc:title", "author": "dc:creator", "subject": "dc:description", "keywords": "pdf:Keywords"}


def set_metadata(data: bytes, values: dict[str, str | None], target: Path, clear: bool = False,
                 password: str | None = None) -> None:
    with open_pdf(data, password) as pdf:
        if clear:
            for key in list(pdf.docinfo.keys()):
                del pdf.docinfo[key]
            if "/Metadata" in pdf.Root:
                del pdf.Root.Metadata
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for field, key in META_FIELDS.items():
                value = values.get(field)
                if value is None:
                    continue
                meta[key] = [value] if key == "dc:creator" else value
        save(pdf, target)


def protect(data: bytes, target: Path, user: str, owner: str | None, *, allow_print: bool = True,
            allow_copy: bool = True, allow_edit: bool = True, password: str | None = None) -> None:
    if not user:
        raise CliError(t("password vuota"))
    permissions = pikepdf.Permissions(
        print_lowres=allow_print, print_highres=allow_print,
        extract=allow_copy, accessibility=True,
        modify_annotation=allow_edit, modify_assembly=allow_edit, modify_form=allow_edit, modify_other=allow_edit,
    )
    with open_pdf(data, password) as pdf:
        # Con la stessa password per aprire e per i permessi, chiunque apra il file avrebbe tutti i diritti:
        # senza --owner-password se ne genera una casuale, così i limiti valgono davvero.
        owner = owner or secrets.token_urlsafe(24)
        if owner == user:
            raise CliError(t("la password proprietario deve essere diversa da quella di apertura"))
        save(pdf, target, encryption=pikepdf.Encryption(user=user, owner=owner, R=6, allow=permissions))


def unlock(data: bytes, target: Path, password: str) -> None:
    with open_pdf(data, password) as pdf:
        save(pdf, target, encryption=False)

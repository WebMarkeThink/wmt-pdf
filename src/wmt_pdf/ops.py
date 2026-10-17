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

from wmt_pdf.pages import parse_pages
from wmt_pdf.source import CliError


def open_pdf(data: bytes, password: str | None = None) -> pikepdf.Pdf:
    try:
        with warnings.catch_warnings():
            # Con merge la password vale per tutti i file: per quelli non protetti non serve, e va bene così.
            warnings.filterwarnings("ignore", message="A password was provided")
            return pikepdf.open(io.BytesIO(data), password=password or "")
    except pikepdf.PasswordError:
        raise CliError("PDF protetto da password: usa --password o --password-env") from None
    except pikepdf.PdfError as exc:
        raise CliError(f"PDF non leggibile: {exc}") from None


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


def merge(parts: Iterable[Part], target: Path, bookmarks: bool = True) -> int:
    """Unisce i PDF nell'ordine dato; ogni file diventa un segnalibro. Restituisce le pagine."""
    out = pikepdf.new()
    opened = []  # pikepdf vuole i sorgenti aperti fino al salvataggio
    protected: tuple[pikepdf.Pdf, str] | None = None
    try:
        with out.open_outline() as outline:
            for part in parts:
                src = open_pdf(part.data, part.password)
                opened.append(src)
                if src.is_encrypted and part.password and protected is None:
                    protected = (src, part.password)
                start = len(out.pages)
                out.pages.extend(src.pages)
                if bookmarks:
                    outline.root.append(pikepdf.OutlineItem(part.title, start))
        # Se almeno un file era protetto, il risultato resta protetto con la stessa password.
        save(out, target, source=protected[0] if protected else None, password=protected[1] if protected else None)
        return len(out.pages)
    finally:
        for src in opened:
            src.close()
        out.close()


def split_groups(count: int, every: int | None = None, ranges: str | None = None) -> list[list[int]]:
    if every:
        if every < 1:
            raise CliError("--every vuole un numero positivo")
        return [list(range(s, min(s + every, count + 1))) for s in range(1, count + 1, every)]
    if ranges:
        groups = [parse_pages(chunk, count) for chunk in ranges.split(";") if chunk.strip()]
        labels = [(g[0], g[-1]) for g in groups]
        if len(set(labels)) != len(labels):
            raise CliError("--ranges contiene due gruppi uguali: il secondo file sostituirebbe il primo")
        return groups
    raise CliError("indica --every N oppure --ranges \"1-3;4-9\"")


def split(data: bytes, groups: list[list[int]], outdir: Path, stem: str, password: str | None = None) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    with open_pdf(data, password) as src:
        for group in groups:
            label = f"{group[0]}-{group[-1]}" if len(group) > 1 else str(group[0])
            target = outdir / f"{stem}_{label}.pdf"
            with pikepdf.new() as part:
                part.pages.extend(src.pages[n - 1] for n in group)
                save(part, target, source=src, password=password)
            written.append(target)
    return written


def select_pages(data: bytes, numbers: list[int], target: Path, password: str | None = None) -> int:
    """Nuovo PDF con le pagine indicate, nell'ordine indicato (anche ripetute)."""
    with open_pdf(data, password) as src, pikepdf.new() as out:
        for n in numbers:
            out.pages.append(src.pages[n - 1])
        save(out, target, source=src, password=password)
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
        raise CliError("--angle deve essere un multiplo di 90")
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
        raise CliError("password vuota")
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
            raise CliError("la password proprietario deve essere diversa da quella di apertura")
        save(pdf, target, encryption=pikepdf.Encryption(user=user, owner=owner, R=6, allow=permissions))


def unlock(data: bytes, target: Path, password: str) -> None:
    with open_pdf(data, password) as pdf:
        save(pdf, target, encryption=False)

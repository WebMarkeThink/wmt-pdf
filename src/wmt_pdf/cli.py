"""wpdf: legge PDF e p7m restituendo testo compatto, adatto agli agenti."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from wmt_pdf import __version__
from wmt_pdf.extract import (
    Extractor,
    estimate_tokens,
    fold,
    fold_with_map,
    is_blank,
    is_scanned,
    page_stats,
    to_csv,
    to_markdown,
)
from wmt_pdf.pages import parse_pages
from wmt_pdf.source import (
    CliError,
    check_target,
    load_source,
    p7m_banner,
    read_file,
    resolve_password,
)

EXIT_OK, EXIT_ERROR, EXIT_INVALID_SIGNATURE = 0, 1, 2
MIN_CUT_CHARS = 200  # anche col budget esaurito si avanza, così "continua" non gira a vuoto
MIN_DPI, MAX_DPI = 20, 600


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except ValueError:
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_ERROR
    try:
        return args.func(args)
    except CliError as exc:
        print(f"wpdf: {exc}", file=sys.stderr)
        return exc.code
    except ValueError as exc:
        print(f"wpdf: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if os.environ.get("WPDF_DEBUG"):
            raise
        detail = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
        print(f"wpdf: errore ({type(exc).__name__}){': ' + detail if detail else ''}", file=sys.stderr)
        print("wpdf: imposta WPDF_DEBUG=1 per i dettagli", file=sys.stderr)
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wpdf",
        description="Legge PDF e p7m come testo compatto. Le immagini solo su richiesta (render).",
    )
    parser.add_argument("--version", action="version", version=f"wpdf {__version__}")
    sub = parser.add_subparsers(metavar="comando")

    def add(name: str, func, help_text: str, pdf: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("file", help="PDF oppure .p7m")
        if pdf:
            add_password_args(p)
        p.set_defaults(func=func)
        return p

    p = add("info", cmd_info, "Panoramica: pagine, metadati, pagine scansionate, firme, token stimati")
    p.add_argument("--json", action="store_true")

    p = add("text", cmd_text, "Testo per pagina in Markdown, con tabelle e OCR delle scansioni")
    p.add_argument("--pages", help="es. 1-3,7 oppure 5- oppure last")
    p.add_argument("--max-tokens", type=int, help="budget indicativo; oltre si tronca e si indica come continuare")
    p.add_argument("--from-char", type=int, default=0, help="salta i primi N caratteri della prima pagina (per continuare)")
    p.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")
    p.add_argument("--lang", default="it", help="lingua OCR (codici PP-OCRv6: it, en, de, fr, es, ...)")
    p.add_argument("--no-tables", action="store_true", help="niente ricerca tabelle (più veloce)")
    p.add_argument("--keep-headers", action="store_true", help="non togliere intestazioni e piè di pagina ripetuti")
    p.add_argument("--json", action="store_true")

    p = add("tables", cmd_tables, "Solo le tabelle, in Markdown o CSV")
    p.add_argument("--pages")
    p.add_argument("--csv", action="store_true")

    p = add("search", cmd_search, "Cerca un testo (senza badare a maiuscole e accenti)")
    p.add_argument("query")
    p.add_argument("--pages")
    p.add_argument("--context", type=int, default=60, help="caratteri attorno al risultato")
    p.add_argument("--limit", type=int, default=50)

    p = add("render", cmd_render, "Immagine PNG di una pagina, da far guardare all'agente solo se serve")
    p.add_argument("--page", default="1")
    p.add_argument("--dpi", type=int, default=100)
    p.add_argument("--crop", help="porzione x0,y0,x1,y1 in frazioni da 0 a 1, dall'alto a sinistra")
    p.add_argument("--gray", action="store_true", help="scala di grigi (file più piccolo)")
    p.add_argument("-o", "--output", help="file PNG di destinazione (sovrascritto se esiste)")

    p = add("p7m", cmd_p7m, "Firmatari e verifica di un .p7m; con -o estrae il documento", pdf=False)
    p.add_argument("-o", "--output", help="dove salvare il documento firmato")
    p.add_argument("--force", action="store_true", help="sovrascrive il file di destinazione se esiste")
    p.add_argument("--json", action="store_true")

    p = add("signatures", cmd_signatures, "Firme incorporate nel PDF (PAdES) e loro integrità")
    p.add_argument("--json", action="store_true")

    from wmt_pdf.cli_ops import register

    register(sub)

    from wmt_pdf.cli_sign import register as register_sign

    register_sign(sub)

    from wmt_pdf.cli_text import register as register_text

    register_text(sub)

    p = sub.add_parser("gui", help="Apre l'interfaccia grafica", description="Apre l'interfaccia grafica")
    p.add_argument("--port", type=int, default=0, help="porta locale (di default una libera)")
    p.add_argument("--no-window", action="store_true", help="non aprire finestra né browser (solo server)")
    p.set_defaults(func=cmd_gui)
    return parser


def cmd_gui(args) -> int:
    import shutil
    import tempfile

    from wmt_pdf.gui.server import serve

    workdir = Path(tempfile.mkdtemp(prefix="wmt-pdf-"))
    try:
        serve(workdir, args.port, open_window=not args.no_window)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # i file caricati e prodotti non restano sul disco
    return EXIT_OK


def add_password_args(p: argparse.ArgumentParser) -> None:
    group = p.add_mutually_exclusive_group()
    group.add_argument("--password", help="password del PDF")
    group.add_argument("--password-env", metavar="VARIABILE", help="legge la password da una variabile d'ambiente")


# --- comandi --------------------------------------------------------------


def _open(args):
    source = load_source(args.file)
    return source, source.open(resolve_password(args))


def cmd_info(args) -> int:
    from wmt_pdf.pades import count_signatures

    source, doc = _open(args)
    try:
        count = len(doc)
        meta = {k: v for k, v in doc.get_metadata_dict(skip_empty=True).items()}
        scanned, blank, total_chars = [], [], 0
        for n in range(1, count + 1):
            page = doc[n - 1]
            try:
                text, st = page_stats(page)
            finally:
                page.close()
            total_chars += len(text)
            if is_scanned(st.chars, st.images):
                scanned.append(n)
            elif is_blank(st.chars, st.images):
                blank.append(n)
        width, height = doc.get_page_size(0) if count else (0, 0)
        info = {
            "file": source.name,
            "bytes": len(source.data),
            "pages": count,
            "pdf_version": _pdf_version(doc),
            "page_size_mm": [round(width / 72 * 25.4), round(height / 72 * 25.4)],
            "metadata": meta,
            "pages_without_text": scanned,
            "blank_pages": blank,
            "estimated_tokens": int(total_chars / 3.5),
            "signatures": count_signatures(source.data),
            "attachments": doc.count_attachments(),
            "toc_entries": sum(1 for _ in doc.get_toc()),
            "tagged": doc.is_tagged(),
            "p7m": _p7m_summary(source.p7m) if source.p7m else None,
        }
    finally:
        doc.close()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        return EXIT_OK

    lines = [f"File: {info['file']} ({_kb(info['bytes'])})"]
    banner = p7m_banner(source)
    if banner:
        lines.append(banner)
    w, h = info["page_size_mm"]
    lines.append(f"Pagine: {count} · PDF {info['pdf_version']} · {w}×{h} mm")
    labels = {"Title": "Titolo", "Author": "Autore", "Subject": "Oggetto", "Creator": "Creato con",
              "Producer": "Prodotto da", "CreationDate": "Creato il", "ModDate": "Modificato il"}
    for key, label in labels.items():
        if key in meta:
            lines.append(f"{label}: {meta[key]}")
    with_text = count - len(scanned) - len(blank)
    scan = f" ({_ranges(scanned)}: usa OCR o render)" if scanned else ""
    empty = f" · vuote: {len(blank)} ({_ranges(blank)})" if blank else ""
    lines.append(f"Pagine con testo: {with_text} · senza testo: {len(scanned)}{scan}{empty}")
    lines.append(f"Testo stimato: ~{info['estimated_tokens']} token")
    lines.append(
        f"Firme: {info['signatures']} · Allegati: {info['attachments']} · "
        f"Indice: {info['toc_entries']} voci · Accessibile (tagged): {'sì' if info['tagged'] else 'no'}"
    )
    print("\n".join(lines))
    return EXIT_OK


def cmd_text(args) -> int:
    source, doc = _open(args)
    extractor = Extractor(doc, source.data, tables=not args.no_tables, password=resolve_password(args))
    try:
        numbers = parse_pages(args.pages, len(doc))
        pages, removed = extractor.pages(numbers, keep_headers=args.keep_headers, ocr=args.ocr, lang=args.lang)
        total = len(doc)
    finally:
        extractor.close()
        doc.close()

    budget = args.max_tokens
    header = [f"# {source.name} — {total} pagine"]
    banner = p7m_banner(source)
    if banner:
        header.append(banner)
    if removed:
        header.append("> Righe ripetute in ogni pagina, tolte: " + "; ".join(f"«{r}»" for r in removed))
    out = "\n".join(header) + "\n"
    if args.from_char and pages:
        first = pages[0]
        first.text = first.text[args.from_char :]
        first.note = (first.note + " " if first.note else "") + f"(dal carattere {args.from_char})"
    kept: list = []
    truncated_at = None  # (pagina, carattere da cui riprendere; None se la pagina non è iniziata)
    for item in pages:
        block = _page_block(item)
        if budget and estimate_tokens(out + block) > budget:
            if not kept:  # neanche una pagina intera: si taglia questa, almeno un pezzo utile
                room = int((budget - estimate_tokens(out)) * 3.5) - (len(block) - len(item.text))
                cut = _cut_at_space(item.text, max(MIN_CUT_CHARS, room))
                start = args.from_char if item is pages[0] else 0
                item.text = item.text[:cut]
                out += _page_block(item)
                kept.append(item)
                truncated_at = (item.page, start + cut)
            else:
                truncated_at = (item.page, None)
            break
        out += block
        kept.append(item)

    if args.json:
        data = {
            "file": source.name,
            "total_pages": total,
            "removed_lines": removed,
            "p7m": _p7m_summary(source.p7m) if source.p7m else None,
            "pages": [
                {"page": i.page, "text": i.text, "images": i.images, "tables": len(i.tables), "ocr": i.ocr, "note": i.note}
                for i in kept
            ],
            "truncated": truncated_at is not None,
            "next_pages": _next_spec(truncated_at, numbers) if truncated_at else None,
            "next_from_char": truncated_at[1] if truncated_at else None,
        }
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return EXIT_OK

    if truncated_at:
        page, offset = truncated_at
        spec = _next_spec(truncated_at, numbers)
        if offset is None:
            hint = f"fermo prima della pagina {page}: continua con --pages {spec}"
        else:
            hint = f"pagina {page} tagliata: continua con --pages {spec} --from-char {offset}"
        out += f"\n… [troncato a ~{budget} token, {hint}]\n"
    sys.stdout.write(out)
    return EXIT_OK


def _page_block(item) -> str:
    tags = []
    if item.ocr:
        tags.append("testo da OCR")
    if item.tables:
        tags.append(f"tabelle: {len(item.tables)}")
    if item.images and not item.ocr:
        tags.append(f"immagini: {item.images}")
    title = f"## Pagina {item.page}" + (f" ({', '.join(tags)})" if tags else "")
    body = item.text
    if item.note:
        body = (body + "\n" if body else "") + f"[{item.note}]"
    return f"\n{title}\n{body}\n"


def _cut_at_space(text: str, limit: int) -> int:
    if limit >= len(text):
        return len(text)
    space = text.rfind(" ", int(limit * 0.8), limit)
    return space if space > 0 else limit


def _next_spec(truncated_at, numbers: list[int]) -> str:
    page, _ = truncated_at
    rest = [n for n in numbers if n >= page]
    if rest == list(range(rest[0], numbers[-1] + 1)) and numbers[-1] == max(numbers):
        return f"{rest[0]}-{rest[-1]}" if len(rest) > 1 else str(rest[0])
    return ",".join(map(str, rest))


def cmd_tables(args) -> int:
    source, doc = _open(args)
    extractor = Extractor(doc, source.data, password=resolve_password(args))
    found = 0
    try:
        for n in parse_pages(args.pages, len(doc)):
            from wmt_pdf.extract import extract_tables

            plumber_page = extractor.plumber_page(n)
            tables, _ = extract_tables(plumber_page)
            plumber_page.close()
            for k, rows in enumerate(tables, start=1):
                found += 1
                print(f"### Pagina {n}, tabella {k}")
                print(to_csv(rows) if args.csv else to_markdown(rows))
                print()
    finally:
        extractor.close()
        doc.close()
    if not found:
        print("Nessuna tabella trovata.")
    return EXIT_OK


def cmd_search(args) -> int:
    _, doc = _open(args)
    needle = fold(args.query)
    if not needle:
        raise CliError("testo da cercare vuoto")
    hits = []
    try:
        for n in parse_pages(args.pages, len(doc)):
            page = doc[n - 1]
            try:
                text, _ = page_stats(page)
            finally:
                page.close()
            flat = text.replace("\n", " ")
            folded, index = fold_with_map(flat)
            start = 0
            while (j := folded.find(needle, start)) >= 0:
                i, end = index[j], index[j + len(needle) - 1] + 1
                a, b = max(0, i - args.context), min(len(flat), end + args.context)
                snippet = ("…" if a else "") + flat[a:b].strip() + ("…" if b < len(flat) else "")
                hits.append((n, snippet))
                start = j + len(needle)
    finally:
        doc.close()
    for n, snippet in hits[: args.limit]:
        print(f"p.{n}: {snippet}")
    more = f" (mostrati {args.limit})" if len(hits) > args.limit else ""
    word = "risultato" if len(hits) == 1 else "risultati"
    print(f"{len(hits)} {word}{more}")
    return EXIT_OK


def cmd_render(args) -> int:
    if not MIN_DPI <= args.dpi <= MAX_DPI:
        raise CliError(f"--dpi tra {MIN_DPI} e {MAX_DPI}")
    source, doc = _open(args)
    doc.init_forms()  # senza, PDFium non disegna i campi dei moduli
    try:
        number = parse_pages(args.page, len(doc))
        if len(number) != 1:
            raise CliError("render lavora su una pagina alla volta: --page N")
        page = doc[number[0] - 1]
        try:
            image = page.render(scale=args.dpi / 72).to_pil()
        finally:
            page.close()
    finally:
        doc.close()
    if args.crop:
        try:
            x0, y0, x1, y1 = (float(v) for v in args.crop.split(","))
        except ValueError:
            raise CliError("--crop vuole quattro numeri: x0,y0,x1,y1") from None
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise CliError("--crop: frazioni tra 0 e 1, con x0<x1 e y0<y1")
        w, h = image.size
        image = image.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
    image = image.convert("L" if args.gray else "RGB")
    target = Path(args.output) if args.output else Path.cwd() / f"{Path(source.name).stem}_p{number[0]}.png"
    if target.resolve() == Path(args.file).resolve():
        raise CliError("la destinazione coincide con il file di partenza")
    image.save(target, optimize=True)
    print(f"{target} ({image.size[0]}×{image.size[1]} px, {_kb(target.stat().st_size)})")
    return EXIT_OK


def cmd_p7m(args) -> int:
    from wmt_pdf.cades import P7mError, open_p7m

    data = read_file(args.file)
    try:
        result = open_p7m(data)
    except P7mError as exc:
        raise CliError(str(exc)) from None
    if args.output:
        check_target(Path(args.output), Path(args.file), args.force)
        Path(args.output).write_bytes(result.content)
    if args.json:
        print(json.dumps(_p7m_summary(result) | {"saved_to": args.output}, ensure_ascii=False, indent=1, default=str))
        return EXIT_OK if result.ok else EXIT_INVALID_SIGNATURE

    lines = [f"File: {Path(args.file).name} · livelli di firma: {len(result.layers)}"]
    for depth, layer in enumerate(result.layers, start=1):
        for s in layer:
            who = s.name or "?"
            if s.fiscal_code:
                who += f" (CF {s.fiscal_code})"
            if s.organization:
                who += f", {s.organization}"
            lines.append(f"- Livello {depth}: {who}")
            lines.append(f"  Certificatore: {s.issuer or '?'} · certificato valido {_d(s.not_before)} → {_d(s.not_after)}")
            when = _dt(s.signing_time) if s.signing_time else "non dichiarata"
            lines.append(f"  Data di firma dichiarata: {when}")
            state = "integra e corrispondente al certificato" if s.ok else f"NON valida: {s.problem}"
            lines.append(f"  Firma: {state} · impronta {s.digest_algorithm}")
            if s.cert_valid_at_signing is False:
                lines.append("  Attenzione: alla data di firma il certificato non era valido")
    lines.append(f"Contenuto: {result.content_kind}, {_kb(len(result.content))}")
    if args.output:
        warn = " (ATTENZIONE: firma non valida, il contenuto può essere alterato)" if not result.ok else ""
        lines.append(f"Salvato in: {args.output}{warn}")
    lines.append("Non verificati: certificatore fidato (elenco europeo) e revoca del certificato.")
    print("\n".join(lines))
    return EXIT_OK if result.ok else EXIT_INVALID_SIGNATURE


def cmd_signatures(args) -> int:
    from wmt_pdf.pades import check_signatures

    source = load_source(args.file)
    sigs = check_signatures(source.data)
    if args.json:
        print(json.dumps([s.__dict__ for s in sigs], ensure_ascii=False, indent=1, default=str))
    elif not sigs:
        print("Nessuna firma nel PDF.")
    else:
        lines = [f"Firme nel PDF: {len(sigs)}"]
        for s in sigs:
            who = s.name or "?"
            if s.fiscal_code:
                who += f" (CF {s.fiscal_code})"
            lines.append(f"- {s.field}: {who} · {s.subfilter or '?'}")
            lines.append(f"  Certificatore: {s.issuer or '?'} · data dichiarata: {_dt(s.signing_time) if s.signing_time else 'non dichiarata'}")
            if s.error:
                lines.append(f"  Firma: NON verificabile: {s.error}")
            else:
                if s.ok:
                    state = "integra e corrispondente al certificato"
                elif s.intact and s.valid:
                    state = "NON valida: il documento è stato modificato dopo la firma"
                else:
                    state = "NON valida: contenuto alterato o firma non corrispondente"
                lines.append(f"  Firma: {state} · copre {s.coverage}")
                if s.modification:
                    lines.append(f"  Dopo la firma: {s.modification}")
        lines.append("Non verificati: certificatore fidato (elenco europeo) e revoca del certificato.")
        print("\n".join(lines))
    return EXIT_OK if all(s.ok for s in sigs) else EXIT_INVALID_SIGNATURE


# --- utilità --------------------------------------------------------------


def _p7m_summary(result) -> dict:
    return {
        "valid": result.ok,
        "content_kind": result.content_kind,
        "content_bytes": len(result.content),
        "layers": [[s.__dict__ | {"ok": s.ok} for s in layer] for layer in result.layers],
        "not_checked": ["trusted issuer (EU trusted list)", "revocation"],
    }


def _pdf_version(doc) -> str:
    v = doc.get_version()
    return f"{v // 10}.{v % 10}" if v else "?"


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _d(value) -> str:
    return value.strftime("%d/%m/%Y") if value else "?"


def _dt(value) -> str:
    return value.strftime("%d/%m/%Y %H:%M %Z").strip()


def _ranges(numbers: list[int]) -> str:
    if len(numbers) > 8:
        return f"pagine {numbers[0]}…{numbers[-1]}"
    return "pagine " + ", ".join(map(str, numbers))


if __name__ == "__main__":
    raise SystemExit(main())

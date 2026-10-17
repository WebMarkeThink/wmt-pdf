"""Comandi che producono file: operazioni, ottimizzazione, filigrane, annotazioni, conversioni, esportazione."""

from __future__ import annotations

import json
from pathlib import Path

from wmt_pdf.pages import parse_pages
from wmt_pdf.source import CliError, check_target, load_source, resolve_password

EXIT_OK, EXIT_ERROR = 0, 1


def register(sub) -> None:
    from wmt_pdf.cli import add_password_args

    def add(name: str, func, help_text: str, *, multi: bool = False, output: str = "PDF di destinazione"):
        p = sub.add_parser(name, help=help_text, description=help_text)
        if multi:
            p.add_argument("files", nargs="+", help="file da unire, nell'ordine (PDF, p7m, Office, immagini, ...)")
            add_password_args(p)  # vale per tutti i PDF protetti tra quelli da unire
        else:
            p.add_argument("file", help="PDF oppure .p7m")
            add_password_args(p)
        p.add_argument("-o", "--output", required=True, help=output)
        p.add_argument("--force", action="store_true", help="sovrascrive la destinazione se esiste")
        p.set_defaults(func=func)
        return p

    p = add("merge", cmd_merge, "Unisce più file in un PDF, anche di formati diversi; un segnalibro per file", multi=True)
    p.add_argument("--no-bookmarks", action="store_true")
    p.add_argument("--attachments", action="store_true", help="per le email: accoda anche gli allegati")

    p = add("convert", cmd_convert, "Converte in PDF: Word, Excel, PowerPoint, ODT, Markdown, testo, HTML, CSV, email, immagini")
    p.add_argument("--attachments", action="store_true", help="per le email: accoda anche gli allegati")

    p = add("split", cmd_split, "Divide il PDF in più file", output="cartella di destinazione")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--every", type=int, help="un file ogni N pagine")
    group.add_argument("--ranges", help='gruppi separati da punto e virgola: "1-3;4;5-"')

    p = add("pages", cmd_pages, "Tiene, toglie o riordina pagine")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep", help="pagine da tenere, es. 1-3,7")
    group.add_argument("--delete", help="pagine da togliere")
    group.add_argument("--order", help="nuovo ordine, con ripetizioni: 3,1,2 oppure 5-1")

    p = add("rotate", cmd_rotate, "Ruota le pagine")
    p.add_argument("--angle", type=int, required=True, help="90, 180, 270 o negativi")
    p.add_argument("--pages", help="di default tutte")

    p = add("meta", cmd_meta, "Cambia titolo, autore, oggetto e parole chiave")
    for field in ("title", "author", "subject", "keywords"):
        p.add_argument(f"--{field}")
    p.add_argument("--clear", action="store_true", help="toglie tutti i metadati esistenti")

    p = add("protect", cmd_protect, "Protegge con password (AES-256) e limita stampa, copia, modifica")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--new-password", dest="new_password", help="password per aprire il file")
    group.add_argument("--new-password-env", dest="new_password_env", metavar="VARIABILE")
    owner = p.add_mutually_exclusive_group()
    owner.add_argument("--owner-password", help="password per cambiare i permessi (di default una casuale)")
    owner.add_argument("--owner-password-env", metavar="VARIABILE")
    p.add_argument("--no-print", action="store_true")
    p.add_argument("--no-copy", action="store_true")
    p.add_argument("--no-edit", action="store_true")

    add("unlock", cmd_unlock, "Toglie la password (serve conoscerla)")

    p = add("optimize", cmd_optimize, "Riduce le dimensioni: immagini ricompresse e struttura compattata")
    p.add_argument("--profile", choices=["email", "standard", "stampa", "lossless"], default="standard")
    p.add_argument("--dpi", type=int, help="sostituisce la risoluzione del profilo")
    p.add_argument("--quality", type=int, help="qualità JPEG 1-95, sostituisce quella del profilo")
    p.add_argument("--linearize", action="store_true", help="ottimizzato per la visualizzazione web")

    p = add("watermark", cmd_watermark, "Aggiunge una filigrana di testo o immagine")
    p.add_argument("--text")
    p.add_argument("--image")
    p.add_argument("--opacity", type=float, default=0.15)
    p.add_argument("--angle", type=float, default=45)
    p.add_argument("--size", type=float, help="corpo del testo; di default si adatta alla pagina")
    p.add_argument("--color", default="grigio")
    p.add_argument("--pages")

    add("unwatermark", cmd_unwatermark, "Toglie le filigrane marcate come tali (da wpdf, Acrobat e altri editor)")

    p = add("highlight", cmd_highlight, "Evidenzia tutte le occorrenze di un testo")
    p.add_argument("query")
    p.add_argument("--color", default="giallo")
    p.add_argument("--note", help="nota allegata a ogni evidenziazione")
    p.add_argument("--author", default="wmt-pdf")
    p.add_argument("--pages")

    p = sub.add_parser("annotations", help="Elenca le annotazioni; con --remove le toglie",
                       description="Elenca le annotazioni; con --remove le toglie")
    p.add_argument("file")
    add_password_args(p)
    p.add_argument("--remove", action="store_true")
    p.add_argument("--types", help="tipi da togliere, es. Highlight,Text (di default tutti tranne link e campi)")
    p.add_argument("--all", action="store_true", help="elenca anche collegamenti e campi modulo")
    p.add_argument("--json", action="store_true")
    p.add_argument("-o", "--output")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_annotations)

    p = add("export", cmd_export, "Esporta in docx, xlsx, html, md, txt, json, xml, png, jpg o tiff",
            output="file di destinazione (cartella per png e jpg)")
    p.add_argument("--to", required=True, choices=["docx", "xlsx", "html", "md", "txt", "json", "xml", "png", "jpg", "tiff"])
    p.add_argument("--dpi", type=int, default=150, help="per le immagini")
    p.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")

    p = add("ocr", cmd_ocr, "Rende ricercabile un PDF scansionato (testo invisibile sotto l'immagine)")
    p.add_argument("--lang", default="it")
    p.add_argument("--all-pages", action="store_true", help="anche le pagine che hanno già del testo")

    # --- moduli (fase 3) ---
    p = sub.add_parser("fields", help="Elenca i campi del modulo con tipo, valore e opzioni",
                       description="Elenca i campi del modulo con tipo, valore e opzioni")
    p.add_argument("file")
    add_password_args(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fields)

    p = add("fill", cmd_fill, "Compila il modulo da JSON o CSV; con più righe CSV un PDF per riga",
            output="PDF di destinazione (cartella se i dati hanno più righe)")
    p.add_argument("--data", required=True, help='file .json ({"campo": valore}) o .csv con intestazione')
    p.add_argument("--name-column", help="colonna da usare per il nome dei file (più righe)")
    p.add_argument("--flatten", action="store_true", help="rende i campi parte della pagina, non più modificabili")

    add("flatten", cmd_flatten, "Rende i campi parte della pagina (le altre annotazioni restano)")

    p = add("form", cmd_form, "Aggiunge campi a un PDF da una specifica JSON (testo, data, casella, elenco, scelta, firma)")
    p.add_argument("--spec", required=True, help='file JSON: {"fields": [{"name", "type", "rect" o "after", ...}]}')


# --- supporto ----------------------------------------------------------------------


def _prepare(args, *, is_dir: bool = False):
    """Sorgente, password e destinazione già controllate."""
    target = Path(args.output)
    if is_dir:
        if target.exists() and not target.is_dir():
            raise CliError(f"{target} esiste e non è una cartella")
    else:
        check_target(target, Path(args.file), args.force)
    return load_source(args.file), resolve_password(args), target


def _count(data: bytes, password: str | None) -> int:
    from wmt_pdf.ops import open_pdf

    with open_pdf(data, password) as pdf:
        return len(pdf.pages)


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


# --- comandi -------------------------------------------------------------------------


def cmd_merge(args) -> int:
    from wmt_pdf.convert import kind_of, to_pdf
    from wmt_pdf.ops import Part, merge

    files = [Path(f) for f in args.files]
    target = Path(args.output)
    check_target(target, files, args.force)
    parts, notes = [], []
    for f in files:
        if not f.is_file():
            raise CliError(f"file non trovato: {f}")
        kind_of(f)
        converted = to_pdf(f, attachments=args.attachments)
        notes += converted.notes
        title = f.name.removesuffix(".p7m")
        parts.append(Part(Path(title).stem, converted.data, resolve_password(args)))
    result = merge(parts, target, bookmarks=not args.no_bookmarks)
    print(f"{target}: {len(parts)} file, {result.pages} pagine, {_kb(target.stat().st_size)}")
    if result.renamed_fields:
        shown = ", ".join(result.renamed_fields[:10]) + (" …" if len(result.renamed_fields) > 10 else "")
        notes.append(f"campi con lo stesso nome rinominati: {shown}")
    for note in notes:
        print(f"- {note}")
    return EXIT_OK


def cmd_convert(args) -> int:
    from wmt_pdf.convert import to_pdf

    src, target = Path(args.file), Path(args.output)
    if not src.is_file():
        raise CliError(f"file non trovato: {src}")
    check_target(target, src, args.force)
    converted = to_pdf(src, attachments=args.attachments)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(converted.data)
    print(f"{target}: {_count(converted.data, None)} pagine, {_kb(len(converted.data))}")
    for note in converted.notes:
        print(f"- {note}")
    return EXIT_OK


def cmd_split(args) -> int:
    from wmt_pdf.ops import split, split_groups

    source, password, outdir = _prepare(args, is_dir=True)
    groups = split_groups(_count(source.data, password), every=args.every, ranges=args.ranges)
    stem = Path(source.name.removesuffix(".p7m")).stem
    clashes = [outdir / f"{stem}_{g[0]}-{g[-1]}.pdf" if len(g) > 1 else outdir / f"{stem}_{g[0]}.pdf" for g in groups]
    existing = [p for p in clashes if p.exists()]
    if existing and not args.force:
        raise CliError(f"{existing[0]} esiste già: usa --force per sovrascriverlo")
    written = split(source.data, groups, outdir, stem, password)
    print(f"{len(written)} file in {outdir}:")
    for path in written:
        print(f"- {path.name}")
    return EXIT_OK


def cmd_pages(args) -> int:
    from wmt_pdf.ops import page_order, select_pages

    source, password, target = _prepare(args)
    count = _count(source.data, password)
    if args.keep:
        numbers = parse_pages(args.keep, count)
    elif args.delete:
        drop = set(parse_pages(args.delete, count))
        numbers = [n for n in range(1, count + 1) if n not in drop]
    else:
        numbers = page_order(args.order, count)
    if not numbers:
        raise CliError("non resterebbe nessuna pagina")
    select_pages(source.data, numbers, target, password)
    print(f"{target}: {len(numbers)} pagine (erano {count})")
    return EXIT_OK


def cmd_rotate(args) -> int:
    from wmt_pdf.ops import rotate

    source, password, target = _prepare(args)
    numbers = parse_pages(args.pages, _count(source.data, password))
    rotate(source.data, args.angle, numbers, target, password)
    print(f"{target}: {len(numbers)} pagine ruotate di {args.angle}°")
    return EXIT_OK


def cmd_meta(args) -> int:
    from wmt_pdf.ops import set_metadata

    source, password, target = _prepare(args)
    values = {k: getattr(args, k) for k in ("title", "author", "subject", "keywords")}
    if not args.clear and all(v is None for v in values.values()):
        raise CliError("niente da cambiare: indica --title, --author, --subject, --keywords o --clear")
    set_metadata(source.data, values, target, clear=args.clear, password=password)
    print(f"{target}: metadati aggiornati")
    return EXIT_OK


def cmd_protect(args) -> int:
    import os

    from wmt_pdf.ops import protect

    source, password, target = _prepare(args)
    new = args.new_password
    if args.new_password_env:
        new = os.environ.get(args.new_password_env)
        if new is None:
            raise CliError(f"variabile d'ambiente {args.new_password_env} non impostata")
    owner = args.owner_password
    if args.owner_password_env:
        owner = os.environ.get(args.owner_password_env)
        if owner is None:
            raise CliError(f"variabile d'ambiente {args.owner_password_env} non impostata")
    protect(source.data, target, new, owner, allow_print=not args.no_print,
            allow_copy=not args.no_copy, allow_edit=not args.no_edit, password=password)
    limits = [label for flag, label in ((args.no_print, "stampa"), (args.no_copy, "copia"), (args.no_edit, "modifica"))
              if flag]
    print(f"{target}: protetto con AES-256" + (f", bloccate: {', '.join(limits)}" if limits else ""))
    return EXIT_OK


def cmd_unlock(args) -> int:
    from wmt_pdf.ops import unlock

    source, password, target = _prepare(args)
    if not password:
        raise CliError("serve la password attuale: --password o --password-env")
    unlock(source.data, target, password)
    print(f"{target}: password tolta")
    return EXIT_OK


def cmd_optimize(args) -> int:
    from dataclasses import replace

    from wmt_pdf.optimize import PROFILES, optimize

    source, password, target = _prepare(args)
    profile = PROFILES[args.profile]
    if args.dpi:
        profile = replace(profile, dpi=args.dpi)
    if args.quality:
        if not 1 <= args.quality <= 95:
            raise CliError("--quality tra 1 e 95")
        profile = replace(profile, quality=args.quality)
    report = optimize(source.data, target, profile, linearize=args.linearize, password=password)
    if report.kept_original:
        print(f"{target}: già compatto, nessun guadagno ({_kb(report.before)}); copiato senza modifiche")
    else:
        saved = 100 - report.after * 100 / report.before
        print(f"{target}: {_kb(report.before)} → {_kb(report.after)} (-{saved:.0f}%), "
              f"immagini ricompresse: {report.images_changed} su {report.images_total}")
    return EXIT_OK


def cmd_watermark(args) -> int:
    from wmt_pdf.marks import add_watermark

    source, password, target = _prepare(args)
    image = Path(args.image) if args.image else None
    if image and not image.is_file():
        raise CliError(f"immagine non trovata: {image}")
    numbers = parse_pages(args.pages, _count(source.data, password)) if args.pages else None
    count = add_watermark(source.data, target, text=args.text, image=image, opacity=args.opacity, angle=args.angle,
                          size=args.size, color=args.color, numbers=numbers, password=password)
    print(f"{target}: filigrana su {count} pagine")
    return EXIT_OK


def cmd_unwatermark(args) -> int:
    from wmt_pdf.marks import remove_watermarks

    source, password, target = _prepare(args)
    count = remove_watermarks(source.data, target, password)
    if count:
        print(f"{target}: filigrane tolte da {count} pagine")
    else:
        print(f"{target}: nessuna filigrana marcata trovata (quelle disegnate come testo normale non si riconoscono)")
    return EXIT_OK


def cmd_highlight(args) -> int:
    from wmt_pdf.marks import add_highlights

    source, password, target = _prepare(args)
    numbers = parse_pages(args.pages, _count(source.data, password)) if args.pages else None
    count = add_highlights(source.data, target, args.query, color=args.color, note=args.note, author=args.author,
                           numbers=numbers, password=password)
    if not count:
        raise CliError(f"«{args.query}» non trovato: nessun file scritto")
    word = "evidenziazione" if count == 1 else "evidenziazioni"
    print(f"{target}: {count} {word}")
    return EXIT_OK


def cmd_annotations(args) -> int:
    from wmt_pdf.marks import list_annotations, remove_annotations

    source = load_source(args.file)
    password = resolve_password(args)
    if args.remove:
        if not args.output:
            raise CliError("con --remove serve -o")
        target = Path(args.output)
        check_target(target, Path(args.file), args.force)
        types = {t.strip() for t in args.types.split(",")} if args.types else None
        removed = remove_annotations(source.data, target, types, password)
        print(f"{target}: {removed} annotazioni tolte")
        return EXIT_OK
    items = list_annotations(source.data, include_all=args.all, password=password)
    if args.json:
        print(json.dumps([i.__dict__ for i in items], ensure_ascii=False, indent=1))
        return EXIT_OK
    if not items:
        print("Nessuna annotazione.")
        return EXIT_OK
    for a in items:
        parts = [f"p.{a.page}", a.label]
        if a.author:
            parts.append(a.author)
        line = " · ".join(parts)
        if a.text:
            line += f": «{a.text}»"
        if a.contents:
            line += f" — {a.contents}"
        print(line)
    print(f"{len(items)} annotazioni")
    return EXIT_OK


def cmd_export(args) -> int:
    from wmt_pdf.export import export, image_paths, single_file

    is_dir = not single_file(args.to, Path(args.output))
    source, password, target = _prepare(args, is_dir=is_dir)
    if not 20 <= args.dpi <= 600:
        raise CliError("--dpi tra 20 e 600")
    if is_dir and not args.force:
        existing = [p for p in image_paths(source, args.to, target, _count(source.data, password)) if p.exists()]
        if existing:
            raise CliError(f"{existing[0]} esiste già: usa --force per sovrascriverlo")
    written = export(source, args.to, target, password=password, dpi=args.dpi, ocr=args.ocr)
    if len(written) == 1:
        print(f"{written[0]} ({_kb(written[0].stat().st_size)})")
    else:
        print(f"{len(written)} file in {target}")
    return EXIT_OK


def cmd_ocr(args) -> int:
    from wmt_pdf.ocrlayer import make_searchable

    source, password, target = _prepare(args)
    done = make_searchable(source.data, target, lang=args.lang, force=args.all_pages, password=password)
    if done:
        print(f"{target}: testo aggiunto a {len(done)} pagine")
    else:
        print(f"{target}: nessuna pagina scansionata da trattare (usa --all-pages per forzare)")
    return EXIT_OK




# --- moduli ----------------------------------------------------------------------------------


def cmd_fields(args) -> int:
    from wmt_pdf.forms import list_fields

    source = load_source(args.file)
    items = list_fields(source.data, resolve_password(args))
    if args.json:
        print(json.dumps([i.__dict__ for i in items], ensure_ascii=False, indent=1))
        return EXIT_OK
    if not items:
        print("Nessun campo modulo nel PDF.")
        return EXIT_OK
    for f in items:
        parts = [f"p.{f.page}" if f.page else "p.?", f.name, f.label]
        if f.value not in (None, "", False):
            parts.append(f"= {f.value}")
        if f.options:
            parts.append(f"(opzioni: {', '.join(f.options)})")
        if f.required:
            parts.append("obbligatorio")
        if f.readonly:
            parts.append("sola lettura")
        print(" · ".join(parts))
    print(f"{len(items)} campi")
    return EXIT_OK


def cmd_fill(args) -> int:
    from wmt_pdf.forms import fill, read_records, safe_filename

    data_file = Path(args.data)
    if not data_file.is_file():
        raise CliError(f"file dati non trovato: {data_file}")
    records = read_records(data_file)
    if not records:
        raise CliError(f"{data_file.name}: nessun dato da compilare")
    source, password = load_source(args.file), resolve_password(args)
    is_csv = data_file.suffix.lower() != ".json"  # nel CSV le colonne in più (es. per il nome file) si ignorano
    if len(records) == 1:
        target = Path(args.output)
        check_target(target, [Path(args.file), data_file], args.force)
        result = fill(source.data, target, records[0], flatten=args.flatten, ignore_unknown=is_csv, password=password)
        print(f"{target}: {result.filled} campi compilati" + (", appiattito" if args.flatten else ""))
        _fill_notes(result)
        return EXIT_OK
    outdir = Path(args.output)
    if outdir.suffix.lower() == ".pdf":
        raise CliError(f"i dati hanno {len(records)} righe: -o deve essere una cartella")
    if args.name_column and any(args.name_column not in r for r in records):
        raise CliError(f"colonna «{args.name_column}» assente nei dati")
    stem = Path(source.name.removesuffix(".p7m")).stem
    names: list[str] = []
    taken: set[str] = set()  # Windows e macOS non distinguono maiuscole e minuscole nei nomi dei file
    for i, record in enumerate(records, start=1):
        base = safe_filename(str(record[args.name_column])) if args.name_column else f"{stem}_{i}"
        name, k = base, 2
        while name.casefold() in taken:
            name, k = f"{base}_{k}", k + 1
        names.append(name)
        taken.add(name.casefold())
    targets = [outdir / f"{n}.pdf" for n in names]
    existing = [t for t in targets if t.exists()]
    if existing and not args.force:
        raise CliError(f"{existing[0]} esiste già: usa --force per sovrascriverlo")
    # Prima si controllano tutte le righe: un errore alla riga 50 non deve lasciare 49 file a metà lavoro.
    for i, record in enumerate(records, start=1):
        try:
            fill(source.data, None, record, flatten=args.flatten, ignore_unknown=is_csv, password=password)
        except CliError as exc:
            raise CliError(f"riga {i}: {exc}") from None
    outdir.mkdir(parents=True, exist_ok=True)
    last = None
    for record, target in zip(records, targets):
        last = fill(source.data, target, record, flatten=args.flatten, ignore_unknown=is_csv, password=password)
    print(f"{len(targets)} moduli compilati in {outdir}")
    if last is not None:
        _fill_notes(last)
    return EXIT_OK


def _fill_notes(result) -> None:
    if result.ignored:
        print(f"- colonne ignorate (non sono campi del modulo): {', '.join(result.ignored)}")
    if result.viewer_rendered:
        print(f"- aspetto lasciato al visualizzatore (caratteri speciali): {', '.join(result.viewer_rendered)}")


def cmd_flatten(args) -> int:
    from wmt_pdf.forms import flatten

    source, password, target = _prepare(args)
    count = flatten(source.data, target, password)
    print(f"{target}: {count} campi resi parte della pagina")
    return EXIT_OK


def cmd_form(args) -> int:
    from wmt_pdf.forms import add_fields

    spec_file = Path(args.spec)
    if not spec_file.is_file():
        raise CliError(f"specifica non trovata: {spec_file}")
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise CliError(f"{spec_file.name}: JSON non valido ({exc.msg}, riga {exc.lineno})") from None
    source, password, target = _prepare(args)
    count = add_fields(source.data, target, spec, password)
    print(f"{target}: {count} campi aggiunti")
    return EXIT_OK

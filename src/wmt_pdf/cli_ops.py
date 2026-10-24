"""Comandi che producono file: operazioni, ottimizzazione, filigrane, annotazioni, conversioni, esportazione."""

from __future__ import annotations

import json
from pathlib import Path

from wmt_pdf.i18n import t
from wmt_pdf.pages import parse_pages
from wmt_pdf.source import CliError, check_target, load_source, resolve_password

EXIT_OK, EXIT_ERROR = 0, 1


def register(sub) -> None:
    from wmt_pdf.cli import add_password_args

    def add(name: str, func, help_text: str, *, multi: bool = False, output: str = ""):
        output = output or t("PDF di destinazione")
        p = sub.add_parser(name, help=help_text, description=help_text)
        if multi:
            p.add_argument("files", nargs="+", help=t("file da unire, nell'ordine (PDF, p7m, Office, immagini, ...)"))
            add_password_args(p)  # vale per tutti i PDF protetti tra quelli da unire
        else:
            p.add_argument("file", help=t("PDF oppure .p7m"))
            add_password_args(p)
        p.add_argument("-o", "--output", required=True, help=output)
        p.add_argument("--force", action="store_true", help=t("sovrascrive la destinazione se esiste"))
        p.set_defaults(func=func)
        return p

    p = add("merge", cmd_merge, t("Unisce più file in un PDF, anche di formati diversi; un segnalibro per file"), multi=True)
    p.add_argument("--no-bookmarks", action="store_true")
    p.add_argument("--attachments", action="store_true", help=t("per le email: accoda anche gli allegati"))

    p = add("assemble", cmd_assemble, t("Compone un PDF scegliendo pagine, ordine e rotazione da uno o più file"),
            multi=True)
    p.add_argument("--pages", help=t("es. 1:1-3,2:4@90,1:5 (file:pagine@rotazione); senza: tutte le pagine in fila"))
    p.add_argument("--bookmarks", action="store_true", help=t("un segnalibro per file"))
    p.add_argument("--attachments", action="store_true", help=t("per le email: accoda anche gli allegati"))

    p = add("convert", cmd_convert, t("Converte in PDF: Word, Excel, PowerPoint, ODT, Markdown, testo, HTML, CSV, email, immagini"))
    p.add_argument("--attachments", action="store_true", help=t("per le email: accoda anche gli allegati"))

    p = add("split", cmd_split, t("Divide il PDF in più file"), output=t("cartella di destinazione"))
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--every", type=int, help=t("un file ogni N pagine"))
    group.add_argument("--ranges", help=t("gruppi separati da punto e virgola: \"1-3;4;5-\""))

    p = add("pages", cmd_pages, t("Tiene, toglie o riordina pagine"))
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep", help=t("pagine da tenere, es. 1-3,7"))
    group.add_argument("--delete", help=t("pagine da togliere"))
    group.add_argument("--order", help=t("nuovo ordine, con ripetizioni: 3,1,2 oppure 5-1"))

    p = add("rotate", cmd_rotate, t("Ruota le pagine"))
    p.add_argument("--angle", type=int, required=True, help=t("90, 180, 270 o negativi"))
    p.add_argument("--pages", help=t("di default tutte"))

    p = add("meta", cmd_meta, t("Cambia titolo, autore, oggetto e parole chiave"))
    for field in ("title", "author", "subject", "keywords"):
        p.add_argument(f"--{field}")
    p.add_argument("--clear", action="store_true", help=t("toglie tutti i metadati esistenti"))

    p = add("protect", cmd_protect, t("Protegge con password (AES-256) e limita stampa, copia, modifica"))
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--new-password", dest="new_password", help=t("password per aprire il file"))
    group.add_argument("--new-password-env", dest="new_password_env", metavar=t("VARIABILE"))
    owner = p.add_mutually_exclusive_group()
    owner.add_argument("--owner-password", help=t("password per cambiare i permessi (di default una casuale)"))
    owner.add_argument("--owner-password-env", metavar=t("VARIABILE"))
    p.add_argument("--no-print", action="store_true")
    p.add_argument("--no-copy", action="store_true")
    p.add_argument("--no-edit", action="store_true")

    add("unlock", cmd_unlock, t("Toglie la password (serve conoscerla)"))

    p = add("optimize", cmd_optimize, t("Riduce le dimensioni: immagini ricompresse e struttura compattata"))
    p.add_argument("--profile", choices=["email", "standard", "stampa", "lossless"], default="standard")
    p.add_argument("--dpi", type=int, help=t("sostituisce la risoluzione del profilo"))
    p.add_argument("--quality", type=int, help=t("qualità JPEG 1-95, sostituisce quella del profilo"))
    p.add_argument("--linearize", action="store_true", help=t("ottimizzato per la visualizzazione web"))

    p = add("watermark", cmd_watermark, t("Aggiunge una filigrana di testo o immagine"))
    p.add_argument("--text")
    p.add_argument("--image")
    p.add_argument("--opacity", type=float, default=0.15)
    p.add_argument("--angle", type=float, default=45)
    p.add_argument("--size", type=float, help=t("corpo del testo; di default si adatta alla pagina"))
    p.add_argument("--color", default="grigio")
    p.add_argument("--pages")

    add("unwatermark", cmd_unwatermark, t("Toglie le filigrane marcate come tali (da wpdf, Acrobat e altri editor)"))

    p = add("highlight", cmd_highlight, t("Evidenzia tutte le occorrenze di un testo"))
    p.add_argument("query")
    p.add_argument("--color", default="giallo")
    p.add_argument("--note", help=t("nota allegata a ogni evidenziazione"))
    p.add_argument("--author", default="wmt-pdf")
    p.add_argument("--pages")

    p = sub.add_parser("annotations", help=t("Elenca le annotazioni; con --remove le toglie"),
                       description=t("Elenca le annotazioni; con --remove le toglie"))
    p.add_argument("file")
    add_password_args(p)
    p.add_argument("--remove", action="store_true")
    p.add_argument("--types", help=t("tipi da togliere, es. Highlight,Text (di default tutti tranne link e campi)"))
    p.add_argument("--all", action="store_true", help=t("elenca anche collegamenti e campi modulo"))
    p.add_argument("--json", action="store_true")
    p.add_argument("-o", "--output")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_annotations)

    p = add("export", cmd_export, t("Esporta in docx, xlsx, html, md, txt, json, xml, png, jpg o tiff"),
            output=t("file di destinazione (cartella per png e jpg)"))
    p.add_argument("--to", required=True, choices=["docx", "xlsx", "html", "md", "txt", "json", "xml", "png", "jpg", "tiff"])
    p.add_argument("--dpi", type=int, default=150, help=t("per le immagini"))
    p.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")

    p = add("ocr", cmd_ocr, t("Rende ricercabile un PDF scansionato (testo invisibile sotto l'immagine)"))
    p.add_argument("--lang", default="it")
    p.add_argument("--all-pages", action="store_true", help=t("anche le pagine che hanno già del testo"))

    # --- moduli (fase 3) ---
    p = sub.add_parser("fields", help=t("Elenca i campi del modulo con tipo, valore e opzioni"),
                       description=t("Elenca i campi del modulo con tipo, valore e opzioni"))
    p.add_argument("file")
    add_password_args(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fields)

    p = add("fill", cmd_fill, t("Compila il modulo da JSON o CSV; con più righe CSV un PDF per riga"),
            output=t("PDF di destinazione (cartella se i dati hanno più righe)"))
    p.add_argument("--data", required=True, help=t("file .json ({\"campo\": valore}) o .csv con intestazione"))
    p.add_argument("--name-column", help=t("colonna da usare per il nome dei file (più righe)"))
    p.add_argument("--flatten", action="store_true", help=t("rende i campi parte della pagina, non più modificabili"))

    add("flatten", cmd_flatten, t("Rende i campi parte della pagina (le altre annotazioni restano)"))

    p = add("form", cmd_form, t("Aggiunge campi a un PDF da una specifica JSON (testo, data, casella, elenco, scelta, firma)"),
            output=t("PDF di destinazione (con --detect: la proposta JSON)"))
    # --spec, se c'è, vale su --detect e --auto (la finestra manda anche il valore della casella).
    p.add_argument("--spec", help=t("file JSON: {\"fields\": [{\"name\", \"type\", \"rect\" o \"after\", ...}]}"))
    p.add_argument("--detect", action="store_true",
                   help=t("propone i campi da trattini ____, quadratini, linee e riquadri: scrive la specifica JSON da rivedere"))
    p.add_argument("--auto", action="store_true", help=t("crea subito i campi proposti da --detect"))


# --- supporto ----------------------------------------------------------------------


def _prepare(args, *, is_dir: bool = False):
    """Sorgente, password e destinazione già controllate."""
    target = Path(args.output)
    if is_dir:
        if target.exists() and not target.is_dir():
            raise CliError(t("{target} esiste e non è una cartella", target=target))
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
            raise CliError(t("file non trovato: {f}", f=f))
        kind_of(f)
        converted = to_pdf(f, attachments=args.attachments)
        notes += converted.notes
        title = f.name.removesuffix(".p7m")
        parts.append(Part(Path(title).stem, converted.data, resolve_password(args)))
    result = merge(parts, target, bookmarks=not args.no_bookmarks)
    print(t("{target}: {len} file, {pages} pagine, {kb}", target=target, len=len(parts), pages=result.pages, kb=_kb(target.stat().st_size)))
    if result.renamed_fields:
        shown = ", ".join(result.renamed_fields[:10]) + (" …" if len(result.renamed_fields) > 10 else "")
        notes.append(t("campi con lo stesso nome rinominati: {shown}", shown=shown))
    for note in notes:
        print(f"- {note}")
    return EXIT_OK


def cmd_convert(args) -> int:
    from wmt_pdf.convert import to_pdf

    src, target = Path(args.file), Path(args.output)
    if not src.is_file():
        raise CliError(t("file non trovato: {src}", src=src))
    check_target(target, src, args.force)
    converted = to_pdf(src, attachments=args.attachments)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(converted.data)
    count, kb = _count(converted.data, None), _kb(len(converted.data))
    if count == 1:
        print(t("{target}: 1 pagina, {kb}", target=target, kb=kb))
    else:
        print(t("{target}: {count} pagine, {kb}", target=target, count=count, kb=kb))
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
        raise CliError(t("{v} esiste già: usa --force per sovrascriverlo", v=existing[0]))
    written = split(source.data, groups, outdir, stem, password)
    print(t("{len} file in {outdir}:", len=len(written), outdir=outdir))
    for path in written:
        print(f"- {path.name}")
    return EXIT_OK


def cmd_assemble(args) -> int:
    from wmt_pdf.convert import kind_of, to_pdf
    from wmt_pdf.ops import Part, assemble, parse_sheets

    files = [Path(f) for f in args.files]
    target = Path(args.output)
    check_target(target, files, args.force)
    parts, notes, counts = [], [], []
    for f in files:
        if not f.is_file():
            raise CliError(t("file non trovato: {f}", f=f))
        kind_of(f)
        converted = to_pdf(f, attachments=args.attachments)
        notes += converted.notes
        part = Part(Path(f.name.removesuffix(".p7m")).stem, converted.data, resolve_password(args))
        parts.append(part)
        counts.append(_count(part.data, part.password))
    result = assemble(parts, parse_sheets(args.pages, counts), target, bookmarks=args.bookmarks)
    print(t("{target}: {len} file, {pages} pagine, {kb}", target=target, len=len(parts), pages=result.pages,
            kb=_kb(target.stat().st_size)))
    if result.renamed_fields:
        shown = ", ".join(result.renamed_fields[:10]) + (" …" if len(result.renamed_fields) > 10 else "")
        notes.append(t("campi con lo stesso nome rinominati: {shown}", shown=shown))
    for note in notes:
        print(f"- {note}")
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
        raise CliError(t("non resterebbe nessuna pagina"))
    select_pages(source.data, numbers, target, password)
    print(t("{target}: {len} pagine (erano {count})", target=target, len=len(numbers), count=count))
    return EXIT_OK


def cmd_rotate(args) -> int:
    from wmt_pdf.ops import rotate

    source, password, target = _prepare(args)
    numbers = parse_pages(args.pages, _count(source.data, password))
    rotate(source.data, args.angle, numbers, target, password)
    if len(numbers) == 1:
        print(t("{target}: 1 pagina ruotata di {angle}°", target=target, angle=args.angle))
    else:
        print(t("{target}: {len} pagine ruotate di {angle}°", target=target, len=len(numbers), angle=args.angle))
    return EXIT_OK


def cmd_meta(args) -> int:
    from wmt_pdf.ops import set_metadata

    source, password, target = _prepare(args)
    values = {k: getattr(args, k) for k in ("title", "author", "subject", "keywords")}
    if not args.clear and all(v is None for v in values.values()):
        raise CliError(t("niente da cambiare: indica --title, --author, --subject, --keywords o --clear"))
    set_metadata(source.data, values, target, clear=args.clear, password=password)
    print(t("{target}: metadati aggiornati", target=target))
    return EXIT_OK


def cmd_protect(args) -> int:
    import os

    from wmt_pdf.ops import protect

    source, password, target = _prepare(args)
    new = args.new_password
    if args.new_password_env:
        new = os.environ.get(args.new_password_env)
        if new is None:
            raise CliError(t("variabile d'ambiente {new_password_env} non impostata", new_password_env=args.new_password_env))
    owner = args.owner_password
    if args.owner_password_env:
        owner = os.environ.get(args.owner_password_env)
        if owner is None:
            raise CliError(t("variabile d'ambiente {owner_password_env} non impostata", owner_password_env=args.owner_password_env))
    protect(source.data, target, new, owner, allow_print=not args.no_print,
            allow_copy=not args.no_copy, allow_edit=not args.no_edit, password=password)
    limits = [t(label) for flag, label in ((args.no_print, "stampa"), (args.no_copy, "copia"),
                                           (args.no_edit, "modifica")) if flag]
    blocked = t(", bloccate: {limits}", limits=", ".join(limits)) if limits else ""
    print(t("{target}: protetto con AES-256{blocked}", target=target, blocked=blocked))
    return EXIT_OK


def cmd_unlock(args) -> int:
    from wmt_pdf.ops import unlock

    source, password, target = _prepare(args)
    if not password:
        raise CliError(t("serve la password attuale: --password o --password-env"))
    unlock(source.data, target, password)
    print(t("{target}: password tolta", target=target))
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
            raise CliError(t("--quality tra 1 e 95"))
        profile = replace(profile, quality=args.quality)
    report = optimize(source.data, target, profile, linearize=args.linearize, password=password)
    if report.kept_original:
        print(t("{target}: già compatto, nessun guadagno ({kb}); copiato senza modifiche", target=target, kb=_kb(report.before)))
    else:
        saved = 100 - report.after * 100 / report.before
        print(t("{target}: {kb} → {kb2} (-{saved:.0f}%), immagini ricompresse: {images_changed} su {images_total}", target=target, kb=_kb(report.before), kb2=_kb(report.after), saved=saved, images_changed=report.images_changed, images_total=report.images_total))
    return EXIT_OK


def cmd_watermark(args) -> int:
    from wmt_pdf.marks import add_watermark

    source, password, target = _prepare(args)
    image = Path(args.image) if args.image else None
    if image and not image.is_file():
        raise CliError(t("immagine non trovata: {image}", image=image))
    numbers = parse_pages(args.pages, _count(source.data, password)) if args.pages else None
    count = add_watermark(source.data, target, text=args.text, image=image, opacity=args.opacity, angle=args.angle,
                          size=args.size, color=args.color, numbers=numbers, password=password)
    print(t("{target}: filigrana su {count} pagine", target=target, count=count))
    return EXIT_OK


def cmd_unwatermark(args) -> int:
    from wmt_pdf.marks import remove_watermarks

    source, password, target = _prepare(args)
    count = remove_watermarks(source.data, target, password)
    if count:
        print(t("{target}: filigrane tolte da {count} pagine", target=target, count=count))
    else:
        print(t("{target}: nessuna filigrana marcata trovata (quelle disegnate come testo normale non si riconoscono)", target=target))
    return EXIT_OK


def cmd_highlight(args) -> int:
    from wmt_pdf.marks import add_highlights

    source, password, target = _prepare(args)
    numbers = parse_pages(args.pages, _count(source.data, password)) if args.pages else None
    count = add_highlights(source.data, target, args.query, color=args.color, note=args.note, author=args.author,
                           numbers=numbers, password=password)
    if not count:
        raise CliError(t("«{query}» non trovato: nessun file scritto", query=args.query))
    done = t("1 evidenziazione") if count == 1 else t("{n} evidenziazioni", n=count)
    print(f"{target}: {done}")
    return EXIT_OK


def cmd_annotations(args) -> int:
    from wmt_pdf.marks import list_annotations, remove_annotations

    source = load_source(args.file)
    password = resolve_password(args)
    if args.remove:
        if not args.output:
            raise CliError(t("con --remove serve -o"))
        target = Path(args.output)
        check_target(target, Path(args.file), args.force)
        types = {t.strip() for t in args.types.split(",")} if args.types else None
        removed = remove_annotations(source.data, target, types, password)
        print(t("{target}: {removed} annotazioni tolte", target=target, removed=removed))
        return EXIT_OK
    items = list_annotations(source.data, include_all=args.all, password=password)
    if args.json:
        print(json.dumps([i.__dict__ for i in items], ensure_ascii=False, indent=1))
        return EXIT_OK
    if not items:
        print(t("Nessuna annotazione."))
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
    print(t("{len} annotazioni", len=len(items)))
    return EXIT_OK


def cmd_export(args) -> int:
    from wmt_pdf.export import export, image_paths, single_file

    is_dir = not single_file(args.to, Path(args.output))
    source, password, target = _prepare(args, is_dir=is_dir)
    if not 20 <= args.dpi <= 600:
        raise CliError(t("--dpi tra 20 e 600"))
    if is_dir and not args.force:
        existing = [p for p in image_paths(source, args.to, target, _count(source.data, password)) if p.exists()]
        if existing:
            raise CliError(t("{v} esiste già: usa --force per sovrascriverlo", v=existing[0]))
    written = export(source, args.to, target, password=password, dpi=args.dpi, ocr=args.ocr)
    if len(written) == 1:
        print(f"{written[0]} ({_kb(written[0].stat().st_size)})")
    else:
        print(t("{len} file in {target}", len=len(written), target=target))
    return EXIT_OK


def cmd_ocr(args) -> int:
    from wmt_pdf.ocrlayer import make_searchable

    source, password, target = _prepare(args)
    done = make_searchable(source.data, target, lang=args.lang, force=args.all_pages, password=password)
    if done:
        print(t("{target}: testo aggiunto a {len} pagine", target=target, len=len(done)))
    else:
        print(t("{target}: nessuna pagina scansionata da trattare (usa --all-pages per forzare)", target=target))
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
        print(t("Nessun campo modulo nel PDF."))
        return EXIT_OK
    for f in items:
        parts = [f"p.{f.page}" if f.page else "p.?", f.name, f.label]
        if f.value not in (None, "", False):
            parts.append(f"= {f.value}")
        if f.options:
            parts.append(t("(opzioni: {join})", join=', '.join(f.options)))
        if f.required:
            parts.append(t("obbligatorio"))
        if f.readonly:
            parts.append(t("sola lettura"))
        print(" · ".join(parts))
    print(t("{len} campi", len=len(items)))
    return EXIT_OK


def cmd_fill(args) -> int:
    from wmt_pdf.forms import fill, read_records, safe_filename

    data_file = Path(args.data)
    if not data_file.is_file():
        raise CliError(t("file dati non trovato: {data_file}", data_file=data_file))
    records = read_records(data_file)
    if not records:
        raise CliError(t("{name}: nessun dato da compilare", name=data_file.name))
    source, password = load_source(args.file), resolve_password(args)
    is_csv = data_file.suffix.lower() != ".json"  # nel CSV le colonne in più (es. per il nome file) si ignorano
    if len(records) == 1:
        target = Path(args.output)
        check_target(target, [Path(args.file), data_file], args.force)
        result = fill(source.data, target, records[0], flatten=args.flatten, ignore_unknown=is_csv, password=password)
        flat = t(", appiattito") if args.flatten else ""
        print(t("{target}: {n} campi compilati{flat}", target=target, n=result.filled, flat=flat))
        _fill_notes(result)
        return EXIT_OK
    outdir = Path(args.output)
    if outdir.suffix.lower() == ".pdf":
        raise CliError(t("i dati hanno {len} righe: -o deve essere una cartella", len=len(records)))
    if args.name_column and any(args.name_column not in r for r in records):
        raise CliError(t("colonna «{name_column}» assente nei dati", name_column=args.name_column))
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
        raise CliError(t("{v} esiste già: usa --force per sovrascriverlo", v=existing[0]))
    # Prima si controllano tutte le righe: un errore alla riga 50 non deve lasciare 49 file a metà lavoro.
    for i, record in enumerate(records, start=1):
        try:
            fill(source.data, None, record, flatten=args.flatten, ignore_unknown=is_csv, password=password)
        except CliError as exc:
            raise CliError(t("riga {i}: {exc}", i=i, exc=exc)) from None
    outdir.mkdir(parents=True, exist_ok=True)
    last = None
    for record, target in zip(records, targets):
        last = fill(source.data, target, record, flatten=args.flatten, ignore_unknown=is_csv, password=password)
    print(t("{len} moduli compilati in {outdir}", len=len(targets), outdir=outdir))
    if last is not None:
        _fill_notes(last)
    return EXIT_OK


def _fill_notes(result) -> None:
    if result.ignored:
        print(t("- colonne ignorate (non sono campi del modulo): {join}", join=', '.join(result.ignored)))
    if result.viewer_rendered:
        print(t("- aspetto lasciato al visualizzatore (caratteri speciali): {join}", join=', '.join(result.viewer_rendered)))


def cmd_flatten(args) -> int:
    from wmt_pdf.forms import flatten

    source, password, target = _prepare(args)
    count = flatten(source.data, target, password)
    print(t("{target}: {count} campi resi parte della pagina", target=target, count=count))
    return EXIT_OK


def cmd_form(args) -> int:
    from wmt_pdf.detect import detect_fields
    from wmt_pdf.forms import add_fields

    if not (args.spec or args.detect or args.auto):
        raise CliError(t("indica --spec FILE, --detect oppure --auto"))
    if not args.spec:
        source, password = load_source(args.file), resolve_password(args)
        target = Path(args.output)
        check_target(target, Path(args.file), args.force)
        spec = detect_fields(source.data, password)
        if not spec["fields"]:
            raise CliError(t("{name}: nessuno spazio da riempire riconosciuto (trattini, quadratini, linee, riquadri)",
                             name=source.name))
        if args.detect:
            target.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(t("{target}: {count} campi proposti", target=target, count=len(spec["fields"])))
            return EXIT_OK
        count = add_fields(source.data, target, spec, password)
        print(t("{target}: {count} campi aggiunti", target=target, count=count))
        return EXIT_OK
    spec_file = Path(args.spec)
    if not spec_file.is_file():
        raise CliError(t("specifica non trovata: {spec_file}", spec_file=spec_file))
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise CliError(t("{name}: JSON non valido ({msg}, riga {lineno})", name=spec_file.name, msg=exc.msg, lineno=exc.lineno)) from None
    source, password, target = _prepare(args)
    count = add_fields(source.data, target, spec, password)
    print(t("{target}: {count} campi aggiunti", target=target, count=count))
    return EXIT_OK

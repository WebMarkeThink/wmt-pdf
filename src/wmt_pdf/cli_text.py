"""Comandi per il testo e gli oggetti della pagina: textobjects, replace, edittext, movetext, objects, move, addtext."""

from __future__ import annotations

import json
from pathlib import Path

from wmt_pdf.pages import parse_pages
from wmt_pdf.source import CliError, check_target, load_source, resolve_password

EXIT_OK = 0


def register(sub) -> None:
    from wmt_pdf.cli import add_password_args

    def add(name, func, help_text, output=True):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("file")
        add_password_args(p)
        if output:
            p.add_argument("-o", "--output", required=True)
            p.add_argument("--force", action="store_true")
        p.set_defaults(func=func)
        return p

    p = add("textobjects", cmd_textobjects, "Pezzi di testo di una pagina con posizione, font e corpo", output=False)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--json", action="store_true")

    p = add("replace", cmd_replace, "Sostituisce un testo in tutto il documento (o nelle pagine indicate)")
    p.add_argument("find", help="testo da cercare (maiuscole e minuscole contano)")
    p.add_argument("replacement", help="testo nuovo")
    p.add_argument("--pages")

    p = add("edittext", cmd_edittext, "Cambia un pezzo di testo, scelto per posizione o per indice")
    p.add_argument("--page", type=int, required=True)
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--at", help="punto x,y in punti PDF (dal basso a sinistra) sopra il testo")
    where.add_argument("--index", type=int, help="indice da `wpdf textobjects`")
    p.add_argument("--text", required=True, help="testo nuovo (vuoto per eliminarlo)")

    p = add("movetext", cmd_movetext, "Sposta un pezzo di testo (stesso font e caratteri)")
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--index", type=int, required=True, help="indice da `wpdf textobjects`")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--by", help="spostamento dx,dy in punti (y verso l'alto)")
    where.add_argument("--to", help="nuova origine x,y del testo (linea di base), in punti dal basso a sinistra")

    p = add("objects", cmd_objects, "Oggetti della pagina che si possono spostare (testo, immagini, grafica, annotazioni)",
            output=False)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--json", action="store_true")

    p = add("move", cmd_move, "Sposta un oggetto della pagina: testo, immagine, firma grafica, timbro, annotazione")
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--kind", required=True, choices=("text", "image", "graphic", "annotation"))
    p.add_argument("--index", type=int, required=True, help="indice da `wpdf objects`")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--by", help="spostamento dx,dy in punti (y verso l'alto)")
    where.add_argument("--to", help="nuovo angolo in basso a sinistra x,y, in punti dal basso a sinistra")

    p = add("addtext", cmd_addtext, "Aggiunge testo in una posizione della pagina")
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--at", required=True, help="inizio della prima riga, x,y in punti PDF dal basso a sinistra")
    p.add_argument("--text", required=True, help="testo; \\n per andare a capo")
    p.add_argument("--size", type=float, default=11)
    p.add_argument("--color", default="#000000", help="es. #c00000")
    p.add_argument("--bold", action="store_true")


def _point(text: str, option: str = "--at") -> tuple[float, float]:
    try:
        x, y = (float(v) for v in text.split(","))
    except ValueError:
        raise CliError(f"{option} vuole due numeri: x,y") from None
    return x, y


def _prepare(args):
    source = load_source(args.file)
    target = Path(args.output)
    check_target(target, Path(args.file), args.force)
    return source, resolve_password(args), target


def _report(target: Path, result, what: str) -> None:
    extra = f", {result.recreated} riscritti con un font di sistema (le lettere nuove mancavano nel font originale)" \
        if result.recreated else ""
    print(f"{target}: {what}{extra}")
    if result.split:
        print(f"- {result.split} occorrenze divise tra più pezzi di testo non sono state sostituite "
              "(si cambiano una per una con edittext)")


def cmd_textobjects(args) -> int:
    from wmt_pdf.textedit import list_objects

    source = load_source(args.file)
    items = list_objects(source.data, args.page, resolve_password(args))
    if args.json:
        print(json.dumps([o.__dict__ for o in items], ensure_ascii=False, indent=1))
        return EXIT_OK
    if not items:
        print(f"Nessun testo a pagina {args.page} (se è una scansione: wpdf ocr).")
    for o in items:
        x0, y0, x1, y1 = o.bbox
        print(f"[{o.index}] ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) {o.font} {o.font_size:g}pt: {o.text}")
    return EXIT_OK


def cmd_replace(args) -> int:
    from wmt_pdf.textedit import replace_text

    source, password, target = _prepare(args)
    pages = parse_pages(args.pages, _count(source, password)) if args.pages else None
    result = replace_text(source.data, target, args.find, args.replacement, pages, password)
    word = "sostituzione" if result.changed == 1 else "sostituzioni"
    _report(target, result, f"{result.changed} {word}")
    return EXIT_OK


def cmd_edittext(args) -> int:
    from wmt_pdf.textedit import edit_object

    source, password, target = _prepare(args)
    at = _point(args.at) if args.at else None
    # Il testo resta com'è: un «\n» scritto (es. C:\new) non va trasformato.
    result = edit_object(source.data, target, args.page, args.text, at=at, index=args.index,
                         password=password)
    _report(target, result, "testo eliminato" if result.notes else "testo modificato")
    return EXIT_OK


def cmd_movetext(args) -> int:
    from wmt_pdf.textedit import move_object

    by = _point(args.by, "--by") if args.by else None
    to = _point(args.to, "--to") if args.to else None
    source, password, target = _prepare(args)
    move_object(source.data, target, args.page, args.index, by=by, to=to, password=password)
    print(f"{target}: testo spostato a pagina {args.page}")
    return EXIT_OK


def cmd_objects(args) -> int:
    from wmt_pdf.objects import KIND_NAMES, list_page_objects

    source = load_source(args.file)
    items = list_page_objects(source.data, args.page, resolve_password(args))
    if args.json:
        print(json.dumps([o.__dict__ for o in items], ensure_ascii=False, indent=1))
        return EXIT_OK
    if not items:
        print(f"Nessun oggetto a pagina {args.page}.")
    for o in items:
        x0, y0, x1, y1 = o.bbox
        print(f"{o.kind} {o.index}: ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) {KIND_NAMES[o.kind]}: {o.label}")
    return EXIT_OK


def cmd_move(args) -> int:
    from wmt_pdf.objects import KIND_NAMES, move_page_object

    by = _point(args.by, "--by") if args.by else None
    to = _point(args.to, "--to") if args.to else None
    source, password, target = _prepare(args)
    move_page_object(source.data, target, args.page, args.kind, args.index, by=by, to=to, password=password)
    print(f"{target}: oggetto spostato ({KIND_NAMES[args.kind]}) a pagina {args.page}")
    return EXIT_OK


def cmd_addtext(args) -> int:
    from wmt_pdf.textedit import add_text, parse_color

    x, y = _point(args.at)
    color = parse_color(args.color)
    source, password, target = _prepare(args)
    lines = add_text(source.data, target, args.page, x, y, args.text.replace("\\n", "\n"), size=args.size,
                     color=color, bold=args.bold, password=password)
    what = "aggiunta 1 riga" if lines == 1 else f"aggiunte {lines} righe"
    print(f"{target}: {what} di testo a pagina {args.page}")
    return EXIT_OK


def _count(source, password) -> int:
    doc = source.open(password)
    try:
        return len(doc)
    finally:
        doc.close()

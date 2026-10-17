"""Catalogo degli strumenti dell'interfaccia: come ogni scheda diventa un comando wpdf.

L'interfaccia non compone mai la riga di comando: invia l'id dello strumento e i valori delle
opzioni dichiarate qui, che vengono controllati e tradotti in argomenti. Le password passano
per variabili d'ambiente, mai per gli argomenti.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass
from pathlib import Path

PDF = [".pdf", ".p7m"]
ANY = ["*"]
_secret_ids = itertools.count(1)


@dataclass(frozen=True)
class Option:
    name: str
    label: str
    kind: str = "text"  # text, number, choice, bool, password, file
    flag: str = ""  # opzione della CLI; "" = argomento posizionale
    choices: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()  # etichette italiane delle scelte
    default: object = None
    required: bool = False
    help: str = ""
    accept: tuple[str, ...] = ()  # per kind=file
    negate: bool = False  # bool: la CLI ha il flag per il valore False (es. --no-bookmarks)


@dataclass(frozen=True)
class Tool:
    id: str
    command: str
    label: str
    description: str
    icon: str
    category: str
    min_files: int = 1
    max_files: int = 1
    accept: tuple[str, ...] = (".pdf", ".p7m")
    output: str = "pdf"  # pdf, dir, none, p7m, export, content
    suffix: str = ""
    options: tuple[Option, ...] = ()
    one_of: tuple[str, ...] = ()  # almeno una di queste opzioni
    hidden: bool = False  # solo dalla vista documento (serve un punto sulla pagina), non nella griglia


CATEGORIES = {
    "leggi": ("Leggi e controlla", "#3B6FD4"),
    "organizza": ("Organizza", "#2F8F83"),
    "converti": ("Converti", "#D9822B"),
    "ottimizza": ("Ottimizza", "#4C9A2A"),
    "modifica": ("Modifica", "#8A5CD1"),
    "moduli": ("Moduli", "#C2457A"),
    "sicurezza": ("Proteggi e firma", "#C23B3B"),
}

ROTATIONS = ("90", "180", "270")
OCR_LANGS = ("it", "en", "de", "fr", "es")
OCR_LABELS = ("Italiano", "Inglese", "Tedesco", "Francese", "Spagnolo")
COLORS = ("giallo", "verde", "azzurro", "rosa", "arancione")

_TOOLS = [
    Tool("info", "info", "Informazioni", "Pagine, metadati, pagine scansionate, firme", "info", "leggi", output="none"),
    Tool("verify", "verify", "Verifica firme", "Firme digitali: certificatore, revoca, marca temporale", "shield-check",
         "leggi", output="none", options=(
             Option("trust_root", "Certificato radice in più", "file", "--trust-root", accept=(".pem", ".crt", ".cer", ".der")),
             Option("offline", "Senza rete", "bool", "--offline"),
         )),
    Tool("p7m", "p7m", "Apri p7m", "Firmatari e documento contenuto in un .p7m", "envelope", "leggi",
         accept=(".p7m",), output="content"),
    Tool("annotations", "annotations", "Annotazioni", "Evidenziazioni e note presenti", "comment", "leggi",
         output="none"),
    Tool("merge", "merge", "Unisci", "Più file in un PDF, anche Word, Excel, immagini, email", "merge", "organizza",
         min_files=2, max_files=50, accept=tuple(ANY), suffix="unito", options=(
             Option("bookmarks", "Un segnalibro per file", "bool", "--no-bookmarks", default=True, negate=True),
             Option("attachments", "Per le email, accoda gli allegati", "bool", "--attachments"),
         )),
    Tool("split", "split", "Dividi", "Un file ogni N pagine o per intervalli", "split", "organizza", output="dir",
         one_of=("every", "ranges"), options=(
             Option("every", "Ogni quante pagine", "number", "--every"),
             Option("ranges", "Intervalli (es. 1-3;4-)", "text", "--ranges"),
         )),
    Tool("pages", "pages", "Pagine", "Tieni, togli o riordina", "pages", "organizza", suffix="pagine",
         one_of=("keep", "delete", "order"), options=(
             Option("keep", "Tieni (es. 1-3,7)", "text", "--keep"),
             Option("delete", "Togli (es. 2)", "text", "--delete"),
             Option("order", "Nuovo ordine (es. 3,1,2)", "text", "--order"),
         )),
    Tool("rotate", "rotate", "Ruota", "Ruota tutte le pagine o alcune", "rotate", "organizza", suffix="ruotato",
         options=(
             Option("angle", "Gradi", "choice", "--angle", ROTATIONS, ("90° a destra", "180°", "90° a sinistra"),
                    default="90", required=True),
             Option("pages", "Solo le pagine (es. 2,4)", "text", "--pages"),
         )),
    Tool("convert", "convert", "Converti in PDF", "Word, Excel, PowerPoint, ODT, immagini, email, testo",
         "convert", "converti", accept=tuple(ANY), options=(
             Option("attachments", "Per le email, accoda gli allegati", "bool", "--attachments"),
         )),
    Tool("export", "export", "Esporta", "In Word, Excel, HTML, Markdown, testo o immagini", "export", "converti",
         output="export", options=(
             Option("to", "Formato", "choice", "--to", ("docx", "xlsx", "html", "md", "txt", "json", "xml", "png", "jpg",
                                                       "tiff"),
                    ("Word", "Excel (tabelle)", "HTML", "Markdown", "Testo", "JSON (struttura completa)",
                     "XML (struttura completa)", "Immagini PNG", "Immagini JPG",
                     "TIFF multipagina"), default="docx", required=True),
             Option("dpi", "Risoluzione immagini (dpi)", "number", "--dpi", default=150),
         )),
    Tool("ocr", "ocr", "Rendi ricercabile", "OCR delle pagine scansionate", "ocr", "converti", suffix="ocr", options=(
        Option("lang", "Lingua", "choice", "--lang", OCR_LANGS, OCR_LABELS, default="it"),
        Option("all_pages", "Anche le pagine con testo", "bool", "--all-pages"),
    )),
    Tool("optimize", "optimize", "Comprimi", "Riduce le dimensioni del file", "compress", "ottimizza",
         suffix="compresso", options=(
             Option("profile", "Profilo", "choice", "--profile", ("email", "standard", "stampa", "lossless"),
                    ("Email (più leggero)", "Standard", "Stampa (alta qualità)", "Senza perdita"), default="standard"),
             Option("linearize", "Ottimizzato per il web", "bool", "--linearize"),
         )),
    Tool("watermark", "watermark", "Filigrana", "Testo o immagine in trasparenza", "watermark", "modifica",
         suffix="filigrana", one_of=("text", "image"), options=(
             Option("text", "Testo", "text", "--text", help="es. BOZZA o RISERVATO"),
             Option("image", "Immagine", "file", "--image", accept=(".png", ".jpg", ".jpeg"),
                    help="al posto del testo, o insieme"),
             Option("opacity", "Opacità (0-1)", "number", "--opacity", default=0.15),
             Option("color", "Colore", "choice", "--color", ("grigio",) + COLORS, default="grigio"),
         )),
    Tool("unwatermark", "unwatermark", "Togli filigrana", "Rimuove le filigrane marcate come tali", "eraser",
         "modifica", suffix="senza_filigrana"),
    Tool("highlight", "highlight", "Evidenzia", "Evidenzia ogni occorrenza di un testo", "highlight", "modifica",
         suffix="evidenziato", options=(
             Option("query", "Testo da evidenziare", "text", "", required=True),
             Option("color", "Colore", "choice", "--color", COLORS, default="giallo"),
             Option("note", "Nota", "text", "--note"),
         )),
    Tool("replace", "replace", "Sostituisci testo", "Cambia un testo in tutto il documento", "replace", "modifica",
         suffix="modificato", options=(
             Option("find", "Testo da cercare", "text", "", required=True, help="maiuscole e minuscole contano"),
             Option("replacement", "Testo nuovo", "text", "", required=True),
             Option("pages", "Solo le pagine (es. 1-3)", "text", "--pages"),
         )),
    Tool("edittext", "edittext", "Modifica testo", "Cambia un pezzo di testo nel punto indicato", "edit-text",
         "modifica", suffix="modificato", hidden=True, one_of=("index", "at"), options=(
             Option("page", "Pagina", "number", "--page", required=True, default=1),
             Option("index", "Pezzo di testo", "number", "--index"),
             Option("at", "Punto x,y", "text", "--at"),
             Option("text", "Testo nuovo", "text", "--text", required=True),
         )),
    Tool("movetext", "movetext", "Sposta testo", "Sposta un pezzo di testo", "edit-text", "modifica",
         suffix="spostato", hidden=True, options=(
             Option("page", "Pagina", "number", "--page", required=True, default=1),
             Option("index", "Pezzo di testo", "number", "--index", required=True),
             Option("by", "Spostamento dx,dy", "text", "--by", required=True),
         )),
    Tool("addtext", "addtext", "Aggiungi testo", "Scrive testo in un punto della pagina", "add-text", "modifica",
         suffix="con_testo", hidden=True, options=(
             Option("page", "Pagina", "number", "--page", required=True, default=1),
             Option("at", "Punto x,y", "text", "--at", required=True),
             Option("text", "Testo", "text", "--text", required=True),
             Option("size", "Corpo", "number", "--size", default=11),
             Option("color", "Colore", "text", "--color", default="#000000"),
             Option("bold", "Grassetto", "bool", "--bold"),
         )),
    Tool("fields", "fields", "Campi del modulo", "Elenco dei campi compilabili", "form", "moduli", output="none"),
    Tool("fill", "fill", "Compila modulo", "Da un file JSON o CSV (più righe: un PDF per riga)", "fill", "moduli",
         suffix="compilato", output="fill", options=(
        Option("data", "Dati (JSON o CSV)", "file", "--data", required=True, accept=(".json", ".csv")),
        Option("flatten", "Rendi non modificabile", "bool", "--flatten"),
    )),
    Tool("flatten", "flatten", "Blocca modulo", "Rende i campi parte della pagina", "lock-form", "moduli",
         suffix="bloccato"),
    Tool("protect", "protect", "Proteggi", "Password e limiti a stampa, copia, modifica", "lock", "sicurezza",
         suffix="protetto", options=(
             Option("new_password", "Password per aprire", "password", "--new-password-env", required=True),
             Option("no_print", "Impedisci la stampa", "bool", "--no-print"),
             Option("no_copy", "Impedisci la copia", "bool", "--no-copy"),
             Option("no_edit", "Impedisci le modifiche", "bool", "--no-edit"),
         )),
    Tool("unlock", "unlock", "Sblocca", "Toglie la password (serve conoscerla)", "unlock", "sicurezza",
         suffix="sbloccato", options=(
             Option("password", "Password attuale", "password", "--password-env", required=True),
         )),
    Tool("sign", "sign", "Firma digitale", "PAdES nel PDF o CAdES .p7m, con certificato .p12", "sign", "sicurezza",
         accept=tuple(ANY), output="signed", options=(
             Option("p12", "Certificato (.p12 / .pfx)", "file", "--p12", required=True, accept=(".p12", ".pfx")),
             Option("p12_password", "Password del certificato", "password", "--p12-password-env"),
             Option("format", "Tipo di firma", "choice", "--format", ("pades", "cades"),
                    ("PAdES (dentro il PDF)", "CAdES (.p7m)")),
             Option("after", "Firma visibile accanto al testo", "text", "--after"),
             Option("image", "Immagine della firma", "file", "--image", accept=(".png", ".jpg", ".jpeg")),
             Option("reason", "Motivo", "text", "--reason"),
             Option("tsa", "Marca temporale (URL del servizio)", "text", "--tsa"),
         )),
    Tool("stamp", "stamp", "Firma grafica", "Immagine della firma, senza valore digitale", "pen", "sicurezza",
         suffix="firmato", options=(
             Option("image", "Immagine della firma", "file", "--image", required=True, accept=(".png", ".jpg", ".jpeg")),
             Option("after", "Accanto al testo", "text", "--after", required=True),
             Option("caption", "Didascalia (es. nome e data)", "text", "--caption"),
         )),
]

TOOLS = {t.id: t for t in _TOOLS}


def catalog() -> list[dict]:
    """Descrizione degli strumenti per l'interfaccia (senza i dettagli della CLI)."""
    result = []
    for tool in _TOOLS:
        item = asdict(tool)
        item.pop("command")
        item["category_label"], item["color"] = CATEGORIES[tool.category]
        for opt in item["options"]:
            opt.pop("flag")
            opt.pop("negate")
        result.append(item)
    return result


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".p7m", ".pdf"):
        name = name.removesuffix(suffix)
    return re.sub(r"[^\w\-. ]", "_", Path(name).stem or name)[:80] or "documento"


def _check(opt: Option, value):
    if opt.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{opt.label}: serve sì o no")
        return value
    if opt.kind == "number":
        if isinstance(value, bool):
            raise ValueError(f"{opt.label}: serve un numero")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{opt.label}: serve un numero") from None
        return int(number) if number.is_integer() else number
    text = str(value)
    if opt.kind == "choice" and text not in opt.choices:
        raise ValueError(f"{opt.label}: valore non previsto")
    if len(text) > 2000 or "\x00" in text:
        raise ValueError(f"{opt.label}: valore non valido")
    return text


def _is_pdf(path: Path) -> bool:
    if not path.is_file():
        return path.suffix.lower() == ".pdf"
    with path.open("rb") as f:
        return f.read(1024).lstrip().startswith(b"%PDF")


def _data_rows(path: Path) -> int:
    from wmt_pdf.forms import read_records
    from wmt_pdf.source import CliError

    try:
        return len(read_records(path))
    except (CliError, OSError, UnicodeDecodeError):
        return 1  # il comando stesso darà l'errore leggibile


def build_args(tool: Tool, inputs: list[Path], options: dict, outdir: Path) -> tuple[list[str], dict[str, str]]:
    """Argomenti per wpdf e variabili d'ambiente (per le password). Le opzioni di tipo file sono già percorsi."""
    known = {o.name: o for o in tool.options}
    unknown = [k for k in options if k not in known]
    if unknown:
        raise ValueError(f"opzioni sconosciute: {', '.join(unknown)}")
    if not tool.min_files <= len(inputs) <= tool.max_files:
        raise ValueError(f"{tool.label}: servono da {tool.min_files} a {tool.max_files} file")
    flags: list[str] = []
    positionals: list[str] = []
    env: dict[str, str] = {}
    values: dict[str, object] = {}
    for opt in tool.options:
        value = options.get(opt.name, opt.default)
        if value is None or value == "":
            if opt.required:
                raise ValueError(f"{opt.label}: obbligatorio")
            continue
        value = _check(opt, value) if opt.kind != "file" else Path(value)
        values[opt.name] = value
        if opt.kind == "bool":
            if value != opt.negate:  # negate: il flag si mette quando il valore è falso
                flags.append(opt.flag)
        elif opt.kind == "password":
            var = f"WPDF_GUI_SEGRETO_{next(_secret_ids)}"
            env[var] = str(value)
            flags.append(f"{opt.flag}={var}")
        elif not opt.flag:
            positionals.append(str(value))
        else:
            flags.append(f"{opt.flag}={value}")  # forma --opzione=valore: un valore che inizia con "-" resta un valore
    if tool.one_of and not any(values.get(name) not in (None, "") for name in tool.one_of):
        labels = [known[n].label for n in tool.one_of]
        raise ValueError(f"{tool.label}: indica {' oppure '.join(labels)}")

    first = inputs[0]
    stem = _stem(first)
    if tool.output == "pdf":
        name = f"{tool.suffix or stem}.pdf" if tool.id == "merge" else f"{stem}_{tool.suffix or 'pdf'}.pdf"
        flags.append(f"-o={outdir / name}")
    elif tool.output == "dir":
        flags.append(f"-o={outdir}")
    elif tool.output == "export":
        fmt = values["to"]
        target = outdir if fmt in ("png", "jpg") else outdir / f"{stem}.{'tif' if fmt == 'tiff' else fmt}"
        flags.append(f"-o={target}")
    elif tool.output == "content":
        inner = first.name.removesuffix(".p7m") if first.name.lower().endswith(".p7m") else f"{stem}_contenuto"
        flags.append(f"-o={outdir / inner}")
    elif tool.output == "signed":
        is_pdf = _is_pdf(first)
        cades = values.get("format") == "cades" or not is_pdf
        if not is_pdf:
            flags = [f for f in flags if not f.startswith("--format=")]  # un file non PDF si firma solo in CAdES
        flags.append(f"-o={outdir / (first.name + '.p7m' if cades else f'{stem}_firmato.pdf')}")
    elif tool.output == "fill":
        # Più righe di dati: un PDF per riga, in una cartella; una riga sola: un file.
        rows = _data_rows(Path(str(values["data"])))
        flags.append(f"-o={outdir}" if rows > 1 else f"-o={outdir / f'{stem}_{tool.suffix}.pdf'}")
    argv = [tool.command, *flags, "--", *[str(p) for p in inputs], *positionals]
    return argv, env

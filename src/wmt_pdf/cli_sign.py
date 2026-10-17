"""Comandi di firma: sign, verify, stamp, trust."""

from __future__ import annotations

import json
import os
from pathlib import Path

from wmt_pdf.source import (
    CliError,
    check_target,
    load_source,
    read_file,
    resolve_password,
)

EXIT_OK, EXIT_ERROR, EXIT_INVALID, EXIT_INDETERMINATE = 0, 1, 2, 3


def register(sub) -> None:
    from wmt_pdf.cli import add_password_args

    p = sub.add_parser("sign", help="Firma digitale PAdES (PDF) o CAdES (.p7m) con certificato .p12 o smart card",
                       description="Firma digitale PAdES (PDF) o CAdES (.p7m) con certificato .p12 o smart card")
    p.add_argument("file", help="documento da firmare (PDF per PAdES, qualsiasi file per CAdES)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["pades", "cades"], help="di default PAdES per i PDF, CAdES per gli altri file")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--p12", help="certificato con chiave privata (.p12 / .pfx)")
    who.add_argument("--pkcs11", help="libreria PKCS#11 del lettore o del token (.dll, .so, .dylib)")
    secret = p.add_mutually_exclusive_group()
    secret.add_argument("--p12-password-env", metavar="VARIABILE", help="password del .p12 da una variabile d'ambiente")
    secret.add_argument("--pin-env", metavar="VARIABILE", help="PIN della smart card da una variabile d'ambiente")
    p.add_argument("--token-label", help="PKCS#11: etichetta del token")
    p.add_argument("--key-label", help="PKCS#11: etichetta della chiave")
    p.add_argument("--cert-label", help="PKCS#11: etichetta del certificato (di default come la chiave)")
    p.add_argument("--field", help="campo firma già presente nel PDF")
    p.add_argument("--after", help="firma visibile accanto a questa etichetta del testo")
    p.add_argument("--rect", help="firma visibile in x0,y0,x1,y1 (punti)")
    p.add_argument("--page", type=int, help="pagina della firma visibile (con --rect)")
    p.add_argument("--width", type=float, default=180)
    p.add_argument("--height", type=float, default=50)
    p.add_argument("--image", help="immagine della firma autografa da mostrare nel riquadro")
    p.add_argument("--reason", help="motivo (es. Approvazione)")
    p.add_argument("--location", help="luogo")
    p.add_argument("--tsa", help="URL del servizio di marca temporale (RFC 3161)")
    add_password_args(p)
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("verify", help="Verifica completa delle firme: certificatore fidato, revoca, marca temporale",
                       description="Verifica completa delle firme di un PDF o di un .p7m. Esito: 0 tutte valide, "
                                   "2 almeno una non valida, 3 almeno una non verificabile o nessuna firma.")
    p.add_argument("file")
    p.add_argument("--trust-root", action="append", default=[], help="certificato radice fidato in più (PEM o DER)")
    p.add_argument("--offline", action="store_true",
                   help="niente rete: elenco europeo dalla copia locale; senza controllo della revoca l'esito è NON VERIFICABILE")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("stamp", help="Firma grafica: immagine della firma sulla pagina (senza valore digitale)",
                       description="Firma grafica: immagine della firma sulla pagina (senza valore digitale)")
    p.add_argument("file")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--image", required=True)
    p.add_argument("--after", help="accanto a questa etichetta del testo")
    p.add_argument("--rect", help="x0,y0,x1,y1 in punti")
    p.add_argument("--page", type=int)
    p.add_argument("--width", type=float, default=150)
    p.add_argument("--height", type=float, default=50)
    p.add_argument("--caption", help="testo piccolo sotto la firma (es. nome e data)")
    p.add_argument("--break-signatures", action="store_true",
                   help="consente di aggiungere l'immagine a un PDF firmato digitalmente (le firme non saranno più valide)")
    add_password_args(p)
    p.set_defaults(func=cmd_stamp)

    p = sub.add_parser("trust", help="Elenco europeo dei certificatori fidati: update, status",
                       description="Elenco europeo dei certificatori fidati, usato da verify")
    trust_sub = p.add_subparsers(metavar="azione", required=True)
    up = trust_sub.add_parser("update", help="scarica e verifica l'elenco europeo e le liste nazionali")
    up.add_argument("--country", help="solo queste nazioni, es. IT oppure IT,DE,FR (di default tutte)")
    up.set_defaults(func=cmd_trust_update)
    st = trust_sub.add_parser("status", help="data e contenuto della copia locale")
    st.set_defaults(func=cmd_trust_status)


# --- supporto ------------------------------------------------------------------------------------


def _env(name: str | None, what: str) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None:
        raise CliError(f"variabile d'ambiente {name} ({what}) non impostata")
    return value


def _rect(text: str) -> list[float]:
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        raise CliError("--rect vuole quattro numeri: x0,y0,x1,y1") from None
    if len(values) != 4 or values[0] >= values[2] or values[1] >= values[3]:
        raise CliError("--rect vuole x0,y0,x1,y1 con x0<x1 e y0<y1 (in punti)")
    return values


def _box(args, data: bytes, password: str | None) -> tuple[int | None, list[float] | None]:
    """Posizione di un riquadro visibile da --after o --rect, oppure nessuna."""
    if not args.after and not args.rect:
        if args.page:
            raise CliError("--page vale con --rect o --after")
        return None, None
    from wmt_pdf.forms import place_box
    from wmt_pdf.ops import open_pdf

    item: dict = {"name": "firma", "width": args.width, "height": args.height}
    if args.page:
        item["page"] = args.page
    if args.rect:
        item["rect"] = _rect(args.rect)
    else:
        item["after"] = args.after
    with open_pdf(data, password) as pdf:
        return place_box(pdf, data, password, item)


# --- comandi ---------------------------------------------------------------------------------------


def cmd_sign(args) -> int:
    from wmt_pdf import sign

    src, target = Path(args.file), Path(args.output)
    check_target(target, src, args.force)
    data = read_file(src)
    is_pdf = data.lstrip()[:5].startswith(b"%PDF")
    fmt = args.format or ("pades" if is_pdf else "cades")
    if fmt == "pades" and not is_pdf:
        raise CliError("PAdES si applica solo ai PDF: per gli altri file usa --format cades (.p7m)")
    if args.p12:
        handle = sign.from_pkcs12(Path(args.p12), _env(args.p12_password_env, "password del certificato"))
    else:
        handle = sign.from_pkcs11(Path(args.pkcs11), _env(args.pin_env, "PIN"), args.token_label, args.key_label,
                                  args.cert_label)
    try:
        who = sign.signer_name(handle)
        if fmt == "cades":
            if args.field or args.after or args.rect or args.image:
                raise CliError("la firma CAdES non ha una parte visibile: --field, --after, --rect e --image valgono per PAdES")
            sign.sign_cades(data, target, handle, tsa=args.tsa)
            print(f"{target}: firmato CAdES da {who}" + (", con marca temporale" if args.tsa else ""))
            return EXIT_OK
        password = resolve_password(args)
        page, rect = _box(args, data, password)
        if args.image and rect is None and not args.field:
            raise CliError("--image vuole una posizione: --after, --rect o --field")
        placement = sign.Placement(field=args.field, page=page, rect=rect,
                                   image=Path(args.image) if args.image else None)
        if placement.image and not placement.image.is_file():
            raise CliError(f"immagine non trovata: {placement.image}")
        field = sign.sign_pdf(data, target, handle, placement, reason=args.reason, location=args.location,
                              tsa=args.tsa, password=password)
        visible = "visibile" if rect is not None or args.field else "invisibile"
        print(f"{target}: firmato PAdES da {who}, campo {field} ({visible})"
              + (", con marca temporale" if args.tsa else ""))
        return EXIT_OK
    finally:
        handle.close()


def cmd_verify(args) -> int:
    from wmt_pdf import trust, verify

    data = read_file(args.file)
    trust.require_extra()
    roots = trust.load_roots(args.trust_root)
    is_pdf = data.lstrip()[:5].startswith(b"%PDF")
    reports = verify.verify_pdf(data, roots, args.offline) if is_pdf else verify.verify_p7m(data, roots, args.offline)
    meta = trust.status()
    if args.json:
        payload = [r.__dict__ | {"verdict": r.verdict} for r in reports]
        print(json.dumps({"signatures": payload, "trust_list": meta}, ensure_ascii=False, indent=1, default=str))
    elif not reports:
        print("Nessuna firma da verificare.")
    else:
        lines = []
        for n, r in enumerate(reports, start=1):
            lines.append(f"Firma {n} di {len(reports)} · {r.kind} · {r.label}")
            who = r.signer or "?"
            if r.fiscal_code:
                who += f" (CF {r.fiscal_code})"
            lines.append(f"  Firmatario: {who} · certificato di {r.issuer or '?'}")
            when = r.signing_time.strftime("%d/%m/%Y %H:%M") if r.signing_time else "non dichiarata"
            lines.append(f"  Data dichiarata: {when}")
            if r.timestamp is not None:
                state = "valida" if r.timestamp_ok else "NON valida"
                lines.append(f"  Marca temporale: presente, {r.timestamp:%d/%m/%Y %H:%M} ({state})")
            else:
                lines.append("  Marca temporale: assente")
            integrity = "integra" if r.intact and r.valid else "NON integra o non corrispondente al certificato"
            lines.append(f"  Firma: {integrity}" + (f" · copre {r.coverage}" if r.coverage else ""))
            if r.modified_after:
                lines.append(f"  Documento: {r.modified_after}")
            if r.trusted:
                lines.append(f"  Certificatore: attendibile ({r.trust_source})")
            else:
                lines.append("  Certificatore: NON riconosciuto come fidato")
            if r.revocation:
                lines.append(f"  Revoca: {r.revocation}")
            if r.qualification:
                lines.append(f"  Tipo: {r.qualification}")
            if r.problems:
                lines.append(f"  Problemi: {'; '.join(dict.fromkeys(r.problems))}")
            lines.append(f"  Esito: {r.verdict}")
        lines.append("Verifica alla data di oggi.")
        if meta is None:
            lines.append("Elenco europeo dei certificatori fidati non scaricato: `wpdf trust update` "
                         "(una volta, poi funziona anche senza rete).")
        elif trust.is_stale(meta):
            lines.append(f"Elenco europeo aggiornato il {meta['updated'][:10]}: conviene `wpdf trust update`.")
        print("\n".join(lines))
    if not reports:
        return EXIT_INDETERMINATE  # nessuna firma: non è "valido", e chi controlla il codice d'uscita deve saperlo
    verdicts = {r.verdict for r in reports}
    if verify.INVALID in verdicts:
        return EXIT_INVALID
    if verify.INDETERMINATE in verdicts:
        return EXIT_INDETERMINATE
    return EXIT_OK


def cmd_stamp(args) -> int:
    from wmt_pdf.sign import graphic_stamp

    if not args.after and not args.rect:
        raise CliError("indica dove: --after \"etichetta\" oppure --rect x0,y0,x1,y1")
    source, password, target = load_source(args.file), resolve_password(args), Path(args.output)
    check_target(target, Path(args.file), args.force)
    from wmt_pdf.pades import count_signatures

    signed = count_signatures(source.data)
    if signed and not args.break_signatures:
        raise CliError(f"il PDF ha {signed} firme digitali: aggiungere l'immagine le renderebbe non valide "
                       "(per farlo comunque: --break-signatures)")
    page, rect = _box(args, source.data, password)
    if page is None or rect is None:
        raise CliError("posizione non determinata")
    graphic_stamp(source.data, target, Path(args.image), page, rect, password, caption=args.caption)
    note = "; ATTENZIONE: le firme digitali presenti non sono più valide" if signed else ""
    print(f"{target}: firma grafica a pagina {page} (nessun valore di firma digitale){note}")
    return EXIT_OK


def cmd_trust_update(args) -> int:
    from wmt_pdf import trust

    countries = {c.strip().upper() for c in args.country.split(",") if c.strip()} if args.country else None
    meta = trust.update(countries)
    where = ", ".join(meta["territories"]) if isinstance(meta["territories"], list) else "tutte le nazioni"
    print(f"Elenco europeo aggiornato ({where}): {meta['authorities']} certificatori in {trust.cache_dir()}")
    if meta["errors"]:
        print(f"- voci non interpretate: {len(meta['errors'])} (servizi cessati o estensioni non gestite; dettagli in meta.json)")
    return EXIT_OK


def cmd_trust_status(args) -> int:
    from wmt_pdf import trust

    meta = trust.status()
    if meta is None:
        print("Elenco europeo non scaricato: `wpdf trust update` (oppure --country IT per la sola Italia).")
        return EXIT_OK
    where = ", ".join(meta["territories"]) if isinstance(meta["territories"], list) else "tutte le nazioni"
    stale = " (da aggiornare)" if trust.is_stale(meta) else ""
    print(f"Elenco europeo del {meta['updated'][:10]}{stale}: {where}, {meta['authorities']} certificatori")
    return EXIT_OK

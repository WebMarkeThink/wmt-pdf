"""Firma digitale PAdES (nel PDF) e CAdES (.p7m), con certificato PKCS#12 o dispositivo PKCS#11.

Firma grafica: solo l'immagine della firma sulla pagina, senza valore di firma digitale.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wmt_pdf.i18n import t
from wmt_pdf.source import CliError


def _quiet() -> None:
    for name in ("pyhanko", "pyhanko_certvalidator"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def timestamper(url: str):
    """Servizio di marca temporale (TSA) RFC 3161; separato per poterlo sostituire nei test."""
    from pyhanko.sign.timestamps import HTTPTimeStamper

    return HTTPTimeStamper(url, timeout=30)


# --- chi firma -------------------------------------------------------------------------------


@dataclass
class SignerHandle:
    signer: Any  # pyhanko Signer (SimpleSigner o PKCS11Signer)
    session: Any = None  # sessione PKCS#11 da chiudere

    def close(self) -> None:
        if self.session is not None:
            self.session.close()


def from_pkcs12(path: Path, password: str | None) -> SignerHandle:
    from pyhanko.sign import signers

    _quiet()
    if not path.is_file():
        raise CliError(t("certificato non trovato: {path}", path=path))
    try:
        signer = signers.SimpleSigner.load_pkcs12(str(path), passphrase=password.encode() if password else None)
    except (ValueError, TypeError, OSError):
        signer = None
    if signer is None:
        raise CliError(t("{name}: certificato non leggibile (password sbagliata o file non PKCS#12)", name=path.name))
    return SignerHandle(signer)


def from_pkcs11(module: Path, pin: str | None, token_label: str | None, key_label: str | None,
                cert_label: str | None) -> SignerHandle:
    """Smart card o token: serve la libreria PKCS#11 del produttore (.dll, .so, .dylib)."""
    if not module.is_file():
        raise CliError(t("modulo PKCS#11 non trovato: {module} (è la libreria fornita con il lettore o il token)", module=module))
    try:
        from pyhanko.sign import pkcs11
    except ImportError:
        raise CliError(t("supporto PKCS#11 non installato: `uv sync --extra firma`")) from None
    _quiet()
    from pyhanko.config.pkcs11 import TokenCriteria

    try:
        criteria = TokenCriteria(label=token_label) if token_label else None
        session = pkcs11.open_pkcs11_session(str(module), token_criteria=criteria, user_pin=pin)
    except Exception as exc:  # noqa: BLE001 - gli errori dei moduli dei produttori sono di ogni tipo
        raise CliError(t("dispositivo PKCS#11 non accessibile: {exc}", exc=exc)) from None
    try:
        signer = pkcs11.PKCS11Signer(session, key_label=key_label, cert_label=cert_label or key_label)
    except Exception as exc:  # noqa: BLE001
        session.close()
        raise CliError(t("chiave o certificato non trovati sul dispositivo: {exc}", exc=exc)) from None
    return SignerHandle(signer, session)


def signer_name(handle: SignerHandle) -> str:
    subject = handle.signer.signing_cert.subject.native
    return str(subject.get("common_name") or subject.get("organization_name") or "?")


# --- PAdES --------------------------------------------------------------------------------------


@dataclass
class Placement:
    field: str | None = None  # campo firma già presente
    page: int | None = None
    rect: list[float] | None = None
    image: Path | None = None


def sign_pdf(data: bytes, target: Path, handle: SignerHandle, placement: Placement, *, reason: str | None = None,
             location: str | None = None, tsa: str | None = None, password: str | None = None) -> str:
    """Firma PAdES in aggiornamento incrementale (le firme precedenti restano valide). Restituisce il campo."""
    from pyhanko import stamp
    from pyhanko.pdf_utils import images
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers

    _quiet()
    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001
        raise CliError(t("PDF non leggibile per la firma: {exc}", exc=exc)) from None
    if writer.prev.encrypted:
        if not password:
            raise CliError(t("PDF protetto da password: usa --password o --password-env"))
        writer.encrypt(password)
    existing = [name for name, value, _ in fields.enumerate_sig_fields(writer.prev, filled_status=None)]
    filled = {name for name, value, _ in fields.enumerate_sig_fields(writer.prev, filled_status=True)}
    spec = None
    if placement.field:
        if placement.field not in existing:
            raise CliError(t("campo firma «{field}» inesistente (campi firma: {v})", field=placement.field, v=', '.join(existing) or 'nessuno'))
        if placement.field in filled:
            raise CliError(t("il campo «{field}» è già firmato", field=placement.field))
        name = placement.field
    else:
        n = len(existing) + 1
        while f"Firma{n}" in existing:
            n += 1
        name = f"Firma{n}"
        if placement.rect is not None:
            spec = fields.SigFieldSpec(sig_field_name=name, on_page=(placement.page or 1) - 1,
                                       box=tuple(placement.rect))
    style = None
    if placement.rect is not None or placement.field:
        from pyhanko.pdf_utils.layout import AxisAlignment, Margins, SimpleBoxLayoutRule
        from pyhanko.pdf_utils.text import TextBoxStyle

        lowest = AxisAlignment.ALIGN_MIN
        if placement.image:
            # Firma autografa in alto, dicitura piccola sotto: così restano leggibili entrambe.
            style = stamp.TextStampStyle(
                stamp_text="Firmato digitalmente da %(signer)s - %(ts)s",
                background=images.PdfImage(str(placement.image)), background_opacity=1, border_width=0,
                background_layout=SimpleBoxLayoutRule(AxisAlignment.ALIGN_MID, AxisAlignment.ALIGN_MAX,
                                                      margins=Margins(bottom=9)),
                text_box_style=TextBoxStyle(font_size=5),
                inner_content_layout=SimpleBoxLayoutRule(lowest, lowest, margins=Margins(left=1, bottom=1)),
                timestamp_format="%d/%m/%Y %H:%M",
            )
        else:
            style = stamp.TextStampStyle(
                stamp_text="Firmato digitalmente da\n%(signer)s\n%(ts)s", border_width=1,
                text_box_style=TextBoxStyle(font_size=8), timestamp_format="%d/%m/%Y %H:%M",
            )
    meta = signers.PdfSignatureMetadata(
        field_name=name, reason=reason, location=location, md_algorithm="sha256",
        subfilter=fields.SigSeedSubFilter.PADES,
    )
    pdf_signer = signers.PdfSigner(meta, signer=handle.signer, timestamper=timestamper(tsa) if tsa else None,
                                   stamp_style=style, new_field_spec=spec)
    out = io.BytesIO()
    try:
        pdf_signer.sign_pdf(writer, output=out)
    except CliError:
        raise
    except Exception as exc:  # noqa: BLE001 - errori di rete della TSA, del dispositivo, del PDF
        raise CliError(t("firma non riuscita: {exc}", exc=exc)) from None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(out.getvalue())
    return name


# --- CAdES -----------------------------------------------------------------------------------------


def sign_cades(data: bytes, target: Path, handle: SignerHandle, *, tsa: str | None = None) -> None:
    """Busta .p7m con il documento incluso (come richiesto dalla PA italiana)."""
    _quiet()
    try:
        info = asyncio.run(handle.signer.async_sign_general_data(
            data, "sha256", detached=False, use_cades=True, timestamper=timestamper(tsa) if tsa else None))
    except Exception as exc:  # noqa: BLE001
        raise CliError(t("firma non riuscita: {exc}", exc=exc)) from None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(info.dump())


# --- firma grafica -------------------------------------------------------------------------------------


def graphic_stamp(data: bytes, target: Path, image: Path, page_no: int, rect: list[float],
                  password: str | None = None, caption: str | None = None) -> None:
    import pikepdf
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    from wmt_pdf.ops import open_pdf, save

    if not image.is_file():
        raise CliError(t("immagine non trovata: {image}", image=image))
    with open_pdf(data, password) as pdf:
        page = pdf.pages[page_no - 1]
        box = page.mediabox
        width, height = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(width, height))
        x0, y0, x1, y1 = (v - float(box[i % 2]) for i, v in enumerate(rect))
        text_h = 9 if caption else 0
        c.drawImage(ImageReader(str(image)), x0, y0 + text_h, x1 - x0, y1 - y0 - text_h, mask="auto",
                    preserveAspectRatio=True, anchor="sw")
        if caption:
            c.setFont("Helvetica", 7)
            c.drawString(x0, y0, caption[:120])
        c.showPage()
        c.save()
        with pikepdf.open(io.BytesIO(buf.getvalue())) as overlay:
            page.add_overlay(overlay.pages[0], pikepdf.Rectangle(box[0], box[1], box[2], box[3]))
            save(pdf, target)

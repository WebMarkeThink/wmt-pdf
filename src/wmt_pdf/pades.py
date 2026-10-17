"""Firme incorporate nei PDF (PAdES e firme PDF classiche), verificate con pyHanko.

Come per i p7m: integrità e corrispondenza col certificato sì, certificatore fidato e revoca
no (fase 3). Nessuna connessione di rete.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from wmt_pdf.cades import fiscal_code, join_names

COVERAGE = {
    "ENTIRE_FILE": "tutto il file",
    "ENTIRE_REVISION": "la sua revisione; il file è stato poi esteso",
    "CONTENT_ONLY": "solo il contenuto",
}
MODIFICATION = {
    "NONE": "nessuna modifica dopo la firma",
    "LTA_UPDATES": "solo aggiunte di validazione a lungo termine",
    "FORM_FILLING": "compilazione di moduli dopo la firma",
    "ANNOTATIONS": "annotazioni aggiunte dopo la firma",
    "OTHER": "modifiche sostanziali dopo la firma",
}


@dataclass
class PdfSignature:
    field: str
    subfilter: str | None
    name: str | None
    fiscal_code: str | None
    issuer: str | None
    signing_time: object
    intact: bool
    valid: bool
    coverage: str
    modification: str | None
    docmdp_ok: bool | None
    modification_level: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        # intact/valid coprono solo la revisione firmata: le modifiche successive vanno controllate a parte.
        return (
            self.intact
            and self.valid
            and self.error is None
            and self.docmdp_ok is not False
            and self.modification_level != "OTHER"
        )


def _quiet_pyhanko() -> None:
    """pyHanko scrive traceback interi su stderr: all'agente costano token e non servono."""
    for name in ("pyhanko", "pyhanko_certvalidator"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def count_signatures(data: bytes) -> int:
    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        return len(PdfFileReader(io.BytesIO(data), strict=False).embedded_signatures)
    except Exception:  # noqa: BLE001 - il conteggio non deve mai fermare info
        return 0


def check_signatures(data: bytes) -> list[PdfSignature]:
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    _quiet_pyhanko()
    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        embedded = list(reader.embedded_signatures)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"struttura del PDF non leggibile per le firme: {exc}") from None
    result = []
    for sig in embedded:
        item = PdfSignature(
            field=str(sig.field_name), subfilter=None, name=None, fiscal_code=None, issuer=None,
            signing_time=None, intact=False, valid=False, coverage="?", modification=None, docmdp_ok=None,
        )
        try:
            subfilter = sig.sig_object.get("/SubFilter")
            item.subfilter = str(subfilter)[1:] if subfilter else None
            cert = sig.signer_cert
            subject = cert.subject.native if cert else {}
            issuer = cert.issuer.native if cert else {}
            item.name = subject.get("common_name") or join_names(subject.get("given_name"), subject.get("surname"))
            item.fiscal_code = fiscal_code(subject.get("serial_number"))
            item.issuer = issuer.get("organization_name") or issuer.get("common_name")
            item.signing_time = sig.self_reported_timestamp
            # Nessuna radice fidata e nessun download: si verifica solo la crittografia.
            status = validate_pdf_signature(sig, ValidationContext(trust_roots=[], allow_fetching=False))
            item.intact = bool(status.intact)
            item.valid = bool(status.valid)
            item.coverage = COVERAGE.get(status.coverage.name, status.coverage.name) if status.coverage else "?"
            if status.modification_level is not None:
                level = status.modification_level.name
                item.modification_level = level
                item.modification = MODIFICATION.get(level, level)
            item.docmdp_ok = status.docmdp_ok
        except Exception as exc:  # noqa: BLE001 - una firma malformata è un esito, non un crash
            item.error = str(exc) or type(exc).__name__
        result.append(item)
    return result

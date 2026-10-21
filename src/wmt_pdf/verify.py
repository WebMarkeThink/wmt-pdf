"""Verifica completa delle firme: integrità, certificatore fidato, revoca, marca temporale, qualificazione.

Esiti: VALIDA (tutto controllato e a posto), NON VALIDA (firma rotta, documento alterato, certificato
revocato), NON VERIFICABILE (firma integra ma qualcosa non si è potuto controllare: certificatore non
riconosciuto, revoca non verificata in modalità offline, marca temporale non attendibile, errore interno).

La verifica è alla data di oggi: un certificato scaduto o revocato oggi rende la firma non verificabile
o non valida anche se al momento della firma era in regola (la validazione storica con marca temporale
e dati di revoca incorporati è un passo successivo).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
from dataclasses import dataclass, field

from wmt_pdf import trust
from wmt_pdf.cades import (
    MAX_LAYERS,
    P7mError,
    decode_p7m,
    fiscal_code,
    join_names,
    load_signed_data,
)
from wmt_pdf.i18n import N_, t
from wmt_pdf.source import CliError

VALID, INVALID, INDETERMINATE = N_("VALIDA"), N_("NON VALIDA"), N_("NON VERIFICABILE")

PROBLEMS = {
    "REVOKED": N_("certificato revocato"),
    "EXPIRED": N_("certificato scaduto alla data di verifica"),
    "NOT_YET_VALID": N_("certificato non ancora valido"),
    "NO_CERTIFICATE_CHAIN_FOUND": N_("certificatore non riconosciuto"),
    "CHAIN_CONSTRAINTS_FAILURE": N_("catena di certificati non conforme"),
    "CRYPTO_CONSTRAINTS_FAILURE": N_("algoritmi non più ammessi"),
    "TRY_LATER": N_("informazioni di revoca non disponibili ora"),
    "REVOCATION_OUT_OF_BOUNDS_NO_POE": N_("informazioni di revoca non disponibili"),
    "OUT_OF_BOUNDS_NO_POE": N_("certificato non valido alla data della firma"),
    "NO_POE": N_("manca la prova dell'esistenza alla data della firma"),
    "SIG_CONSTRAINTS_FAILURE": N_("uso del certificato non ammesso per la firma"),
    "GENERIC": N_("problema di validazione"),
}


@dataclass
class Report:
    kind: str  # PAdES, PDF o CAdES
    label: str  # campo del PDF o livello del p7m
    signer: str | None = None
    fiscal_code: str | None = None
    issuer: str | None = None
    signing_time: dt.datetime | None = None
    timestamp: dt.datetime | None = None
    timestamp_ok: bool | None = None
    intact: bool = False
    valid: bool = False
    trusted: bool = False
    trust_source: str | None = None
    revocation_checked: bool = False
    revocation: str = ""
    qualification: str | None = None
    coverage: str | None = None
    modified_after: str | None = None
    bottom_line: bool | None = None  # giudizio complessivo di pyHanko, come controllo in più
    error: str | None = None  # errore interno: la firma non è stata giudicata
    problems: list[str] = field(default_factory=list)
    label_key: str = ""  # modello di label (testo italiano con segnaposto): si traduce quando si mostra
    label_values: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.label_key and not self.label:
            self.label = self.label_key.format(**self.label_values)  # nel JSON resta il testo, come prima

    @property
    def verdict(self) -> str:
        if self.error:
            return INDETERMINATE
        if not (self.intact and self.valid) or PROBLEMS["REVOKED"] in self.problems or self.modified_after:
            return INVALID
        if not self.trusted or not self.revocation_checked or self.timestamp_ok is False or self.bottom_line is False:
            return INDETERMINATE
        return VALID


def _quiet() -> None:
    for name in ("pyhanko", "pyhanko_certvalidator", "aiohttp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def _countries(certs) -> set[str]:
    found = set()
    for cert in certs:
        for name in (cert.subject, cert.issuer):
            country = name.native.get("country_name")
            if country:
                found.add(str(country).upper())
    return found


class Verifier:
    """Contesto di verifica condiviso da tutte le firme di un file (l'elenco europeo si carica una volta)."""

    def __init__(self, roots, offline: bool):
        self.roots = roots
        self.offline = offline
        self._registries: dict[frozenset[str], object] = {}

    def registry(self, certs):
        if not trust.status():
            return None
        key = frozenset(_countries(certs))
        if key not in self._registries:
            self._registries[key] = trust.load_registry(set(key))
        return self._registries[key]

    def context(self, certs):
        from pyhanko_certvalidator import ValidationContext
        from pyhanko_certvalidator.fetchers.requests_fetchers import (
            RequestsFetcherBackend,
        )

        registry = self.registry(certs)
        manager = trust.CombinedTrustManager(registry, self.roots)
        context = ValidationContext(
            trust_manager=manager,
            other_certs=list(certs),
            allow_fetching=not self.offline,
            revocation_mode="soft-fail" if self.offline else "hard-fail",
            # requests chiude da sé le connessioni (con aiohttp restano sessioni aperte e avvisi a video)
            fetcher_backend=RequestsFetcherBackend(per_request_timeout=15),
        )
        return context, manager, registry


def _has_revocation_pointers(cert) -> bool:
    try:
        return bool(cert.crl_distribution_points or cert.ocsp_urls)
    except (ValueError, TypeError):
        return False


def _fill_common(report: Report, status, manager, registry, offline: bool) -> None:
    cert = status.signing_cert
    subject, issuer = cert.subject.native, cert.issuer.native
    report.signer = subject.get("common_name") or join_names(subject.get("given_name"), subject.get("surname"))
    report.fiscal_code = fiscal_code(subject.get("serial_number"))
    report.issuer = issuer.get("organization_name") or issuer.get("common_name")
    report.signing_time = getattr(status, "signer_reported_dt", None)
    report.intact = bool(status.intact)
    report.valid = bool(status.valid)
    report.trusted = bool(status.trusted) and not status.revoked
    report.bottom_line = bool(status.bottom_line)
    if status.revoked:
        report.problems.append(PROBLEMS["REVOKED"])
    indication = getattr(status, "trust_problem_indic", None)
    if indication is not None and not report.trusted:
        report.problems.append(PROBLEMS.get(indication.name, indication.name.lower()))
    path = getattr(status, "validation_path", None)
    if report.trusted and path is not None:
        anchor_cert = getattr(path.trust_anchor.authority, "certificate", None)
        if anchor_cert is not None and anchor_cert.dump() in manager.custom_roots:
            report.trust_source = N_("radice indicata con --trust-root")
        else:
            report.trust_source = N_("elenco europeo dei certificatori fidati")
            report.qualification = _qualification(path, registry, report)
    if offline:
        report.revocation = N_("non verificata (modalità offline)")
    elif not report.trusted:
        report.revocation = N_("non verificata (certificatore non riconosciuto)")
    elif _has_revocation_pointers(cert):
        report.revocation = N_("verificata online (OCSP/CRL)")
        report.revocation_checked = True
    else:
        # Nessun servizio di revoca indicato: non c'è nulla da controllare, e lo si dice.
        report.revocation = N_("il certificato non indica servizi di revoca")
        report.revocation_checked = True
    ts = getattr(status, "timestamp_validity", None)
    if ts is not None:
        report.timestamp = ts.timestamp
        # Una marca vale come data certa solo se emessa da un servizio fidato.
        report.timestamp_ok = bool(ts.intact and ts.valid and ts.trusted)
        if not report.timestamp_ok:
            report.problems.append(N_("marca temporale non attendibile"))  # tradotto quando si mostra


def _qualification(path, registry, report: Report) -> str | None:
    if registry is None:
        return None
    try:
        from pyhanko.sign.validation.qualified.assess import QualificationAssessor
        from pyhanko.sign.validation.qualified.q_status import (
            QcPrivateKeyManagementType,
        )
        from pyhanko.sign.validation.qualified.tsp import QcCertType

        # Il momento: la marca temporale se attendibile, altrimenti oggi (la data dichiarata non è una prova).
        moment = report.timestamp if report.timestamp_ok else None
        result = QualificationAssessor(registry).check_entity_cert_qualified(path, moment=moment)
    except Exception:  # noqa: BLE001 - la qualificazione è un'informazione in più, non deve far fallire la verifica
        return None
    status = result.status
    if not status.qualified:
        return N_("firma elettronica avanzata (certificato non qualificato)")
    secure = status.qc_key_security in (QcPrivateKeyManagementType.QSCD, QcPrivateKeyManagementType.QSCD_DELEGATED,
                                        QcPrivateKeyManagementType.QSCD_BY_POLICY)
    if status.qc_type == QcCertType.QC_ESEAL:
        return N_("sigillo elettronico qualificato") if secure else N_("sigillo con certificato qualificato")
    return N_("firma elettronica qualificata (QES)") if secure else N_("firma con certificato qualificato")


def _error(report: Report, exc: Exception) -> None:
    text = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__
    report.error = text
    report.problems.append(t("verifica non completata: {text}", text=text))


# --- PDF -----------------------------------------------------------------------------------


def verify_pdf(data: bytes, roots, offline: bool) -> list[Report]:
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature

    from wmt_pdf.pades import COVERAGE, MODIFICATION

    _quiet()
    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        embedded = list(reader.embedded_signatures)
    except Exception as exc:  # noqa: BLE001
        raise CliError(t("struttura del PDF non leggibile per le firme: {exc}", exc=exc)) from None
    verifier = Verifier(roots, offline)
    reports = []
    for sig in embedded:
        report = Report(kind="PAdES" if "ETSI" in str(sig.sig_object.get("/SubFilter", "")) else "PDF",
                        label="", label_key=N_("campo {field}"), label_values={"field": sig.field_name})
        certs = [c.chosen for c in (sig.signed_data["certificates"] or []) if c.name == "certificate"]
        context, manager, registry = verifier.context(certs)  # errori dell'elenco: CliError, non un esito
        try:
            status = validate_pdf_signature(sig, signer_validation_context=context, ts_validation_context=context)
            _fill_common(report, status, manager, registry, offline)
            if status.coverage is not None:
                report.coverage = COVERAGE.get(status.coverage.name, N_("solo una parte del file"))
            level = status.modification_level.name if status.modification_level is not None else None
            if status.docmdp_ok is False or level == "OTHER":
                report.modified_after = MODIFICATION.get(level or "OTHER", N_("modifiche dopo la firma"))
        except Exception as exc:  # noqa: BLE001 - un errore interno rende la firma non verificabile, non non valida
            _error(report, exc)
        reports.append(report)
    return reports


# --- p7m -----------------------------------------------------------------------------------------


def _signing_time(info) -> dt.datetime | None:
    for attr in info["signed_attrs"] or []:
        if attr["type"].native == "signing_time":
            return attr["values"][0].native
    return None


def verify_p7m(data: bytes, roots, offline: bool) -> list[Report]:
    from asn1crypto import cms
    from pyhanko.sign.validation.generic_cms import async_validate_detached_cms

    _quiet()
    verifier = Verifier(roots, offline)
    reports = []
    current = data
    for depth in range(1, MAX_LAYERS + 2):
        try:
            signed = load_signed_data(decode_p7m(current))
        except P7mError as exc:
            if depth == 1:
                raise CliError(str(exc)) from None
            break
        if depth > MAX_LAYERS:
            raise CliError(t("più di {MAX_LAYERS} livelli di firma annidati: file sospetto", MAX_LAYERS=MAX_LAYERS))
        content = signed["encap_content_info"]["content"].native
        if content is None:
            raise CliError(t("firma staccata: il contenuto firmato non è nel file"))
        certs = [c.chosen for c in (signed["certificates"] or []) if c.name == "certificate"]
        context, manager, registry = verifier.context(certs)
        infos = list(signed["signer_infos"])
        if not infos:
            reports.append(Report(kind="CAdES", label="", label_key=N_("livello {depth}"), label_values={"depth": depth},
                                  problems=[N_("nessun firmatario")]))
        for k, info in enumerate(infos, start=1):
            label = N_("livello {depth}, firmatario {k}") if len(infos) > 1 else N_("livello {depth}")
            report = Report(kind="CAdES", label="", label_key=label, label_values={"depth": depth, "k": k})
            single = signed.copy()
            single["signer_infos"] = cms.SignerInfos([info])  # pyhanko verifica un firmatario per volta
            # Busta senza contenuto + contenuto a parte: così pyhanko valuta anche la marca temporale.
            single["encap_content_info"] = cms.ContentInfo(
                {"content_type": signed["encap_content_info"]["content_type"].native})
            try:
                # La variante "detached" riceve il contenuto a parte e valuta anche la marca temporale.
                status = asyncio.run(async_validate_detached_cms(
                    content, single, signer_validation_context=context, ts_validation_context=context))
                _fill_common(report, status, manager, registry, offline)
                if report.signing_time is None:
                    report.signing_time = _signing_time(info)  # pyhanko non la riporta per le buste CMS
            except Exception as exc:  # noqa: BLE001
                _error(report, exc)
            reports.append(report)
        current = content
    return reports

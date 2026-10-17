"""Lettura e verifica crittografica dei file .p7m (CAdES, firma con contenuto incluso).

Controlla che il contenuto non sia stato alterato e che la firma corrisponda al certificato
incluso. NON verifica ancora che il certificato sia emesso da un certificatore fidato,
né lo stato di revoca: sono nella fase 3 del piano.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field

from asn1crypto import cms, x509

# tsp aggiunge a CMSAttributeType nomi come signing_certificate_v2. asn1crypto calcola la tabella
# inversa dei nomi una volta sola: se lo fa prima di questo import, pyHanko non può più firmare.
from asn1crypto import tsp as _tsp  # noqa: F401
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509 import load_der_x509_certificate

MAX_LAYERS = 5

_HASHES = {
    "sha1": hashes.SHA1,
    "sha224": hashes.SHA224,
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
}


class P7mError(ValueError):
    pass


@dataclass
class Signer:
    name: str | None = None
    fiscal_code: str | None = None
    organization: str | None = None
    issuer: str | None = None
    serial: str | None = None
    not_before: dt.datetime | None = None
    not_after: dt.datetime | None = None
    signing_time: dt.datetime | None = None
    digest_algorithm: str | None = None
    content_intact: bool = False
    signature_valid: bool = False
    cert_valid_at_signing: bool | None = None
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.content_intact and self.signature_valid and self.cert_valid_at_signing is not False


@dataclass
class P7mResult:
    content: bytes
    layers: list[list[Signer]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # Ogni livello deve avere almeno un firmatario: un p7m senza firme non è valido.
        return bool(self.layers) and all(layer and all(s.ok for s in layer) for layer in self.layers)

    @property
    def content_kind(self) -> str:
        return guess_kind(self.content)


def looks_like_cms(data: bytes) -> bool:
    """Vero se il file sembra un p7m (DER o base64), anche se poi non si apre."""
    try:
        return _decode(data)[:1] == b"0"
    except P7mError:
        return False


def open_p7m(data: bytes) -> P7mResult:
    """Apre un p7m (DER o base64), anche annidato, e verifica ogni livello."""
    result = P7mResult(content=b"")
    current = _decode(data)
    for _ in range(MAX_LAYERS):
        signed_data = _load_signed_data(current)
        content = signed_data["encap_content_info"]["content"].native
        if content is None:
            raise P7mError("firma staccata: il contenuto firmato non è incluso nel file")
        result.layers.append([_safe_check(si, signed_data, content) for si in signed_data["signer_infos"]])
        current = content
        try:
            current = _decode(current)
            _load_signed_data(current)
        except P7mError:
            current = content
            break
    else:
        raise P7mError(f"più di {MAX_LAYERS} livelli di firma annidati: file sospetto")
    result.content = current
    return result


def guess_kind(content: bytes) -> str:
    head = content[:1024].lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"<"):
        if b"FatturaElettronica" in content[:4096]:
            return "xml (fattura elettronica)"
        return "xml"
    if head.startswith(b"PK"):
        return "zip/office"
    if content[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
        return "immagine"
    return "sconosciuto"


def _decode(data: bytes) -> bytes:
    if data[:1] == b"\x30":
        return data
    text = re.sub(rb"-----[A-Z0-9 ]+-----", b"", data)
    try:
        decoded = base64.b64decode(re.sub(rb"\s+", b"", text), validate=True)
    except (binascii.Error, ValueError):
        raise P7mError("non è un file p7m (né DER né base64)") from None
    if decoded[:1] != b"\x30":
        raise P7mError("non è un file p7m")
    return decoded


def _load_signed_data(data: bytes) -> cms.SignedData:
    if data[:1] != b"\x30":
        raise P7mError("non è un file p7m")
    try:
        info = cms.ContentInfo.load(data)
        if info["content_type"].native != "signed_data":
            raise P7mError("il file CMS non contiene una firma")
        signed_data = info["content"]
        _ = signed_data["signer_infos"].native  # forza il parsing completo
        return signed_data
    except P7mError:
        raise
    except Exception as exc:  # noqa: BLE001 - asn1crypto solleva tipi diversi
        raise P7mError(f"struttura p7m non leggibile: {exc}") from None


def _find_cert(signer_info: cms.SignerInfo, signed_data: cms.SignedData) -> x509.Certificate | None:
    sid = signer_info["sid"]
    for choice in signed_data["certificates"] or []:
        if choice.name != "certificate":
            continue
        cert = choice.chosen
        if sid.name == "issuer_and_serial_number":
            if (
                cert.serial_number == sid.chosen["serial_number"].native
                and cert.issuer == sid.chosen["issuer"]
            ):
                return cert
        elif cert.key_identifier == sid.chosen.native:
            return cert
    return None


def _safe_check(signer_info: cms.SignerInfo, signed_data: cms.SignedData, content: bytes) -> Signer:
    """Un firmatario malformato diventa un firmatario non valido, mai un crash."""
    try:
        return _check_signer(signer_info, signed_data, content)
    except Exception as exc:  # noqa: BLE001 - mai un crash su un file malformato
        return Signer(problem=f"firmatario non verificabile: {exc}")


def _check_signer(signer_info: cms.SignerInfo, signed_data: cms.SignedData, content: bytes) -> Signer:
    algo = signer_info["digest_algorithm"]["algorithm"].native
    cert = _find_cert(signer_info, signed_data)
    if cert is None:
        return Signer(digest_algorithm=algo, problem="certificato del firmatario non incluso nel p7m")

    subject = cert.subject.native
    issuer = cert.issuer.native
    validity = cert["tbs_certificate"]["validity"]
    signer = Signer(
        name=subject.get("common_name") or join_names(subject.get("given_name"), subject.get("surname")),
        fiscal_code=fiscal_code(subject.get("serial_number")),
        organization=subject.get("organization_name"),
        issuer=issuer.get("organization_name") or issuer.get("common_name"),
        serial=format(cert.serial_number, "x"),
        not_before=validity["not_before"].native,
        not_after=validity["not_after"].native,
        signing_time=None,
        digest_algorithm=algo,
        content_intact=False,
        signature_valid=False,
        cert_valid_at_signing=None,
    )
    if algo not in _HASHES:
        signer.problem = f"algoritmo di impronta non supportato: {algo}"
        return signer

    signed_attrs = signer_info["signed_attrs"]
    digest = hashlib.new(algo, content).digest()
    if signed_attrs:
        attrs = {a["type"].native: a["values"][0] for a in signed_attrs}
        message_digest = attrs.get("message_digest")
        signer.content_intact = message_digest is not None and message_digest.native == digest
        if "signing_time" in attrs:
            signer.signing_time = attrs["signing_time"].native
        to_verify = b"\x31" + signed_attrs.dump()[1:]  # da [0] IMPLICIT a SET
    else:
        signer.content_intact = True  # senza attributi la firma copre direttamente il contenuto
        to_verify = content

    try:
        _verify(cert, signer_info, to_verify, algo)
        signer.signature_valid = True
    except InvalidSignature:
        signer.problem = "la firma non corrisponde al certificato"
    except ValueError as exc:
        signer.problem = str(exc)
    if not signer.content_intact:
        signer.problem = "il contenuto è stato modificato dopo la firma"
    if signed_attrs and "content_type" in attrs:
        declared = attrs["content_type"].native
        encapsulated = signed_data["encap_content_info"]["content_type"].native
        if declared != encapsulated:
            signer.content_intact = False
            signer.problem = "tipo di contenuto firmato diverso da quello incluso"

    if signer.signing_time and signer.not_before and signer.not_after:
        signer.cert_valid_at_signing = signer.not_before <= signer.signing_time <= signer.not_after
        if not signer.cert_valid_at_signing and signer.problem is None:
            signer.problem = "alla data di firma il certificato non era valido"
    return signer


def _verify(cert: x509.Certificate, signer_info: cms.SignerInfo, data: bytes, algo: str) -> None:
    public_key = load_der_x509_certificate(cert.dump()).public_key()
    signature = signer_info["signature"].native
    sig_algo = signer_info["signature_algorithm"]
    hash_cls = _HASHES[algo]
    kind = sig_algo.signature_algo
    if isinstance(public_key, rsa.RSAPublicKey):
        if kind == "rsassa_pss":
            params = sig_algo["parameters"]
            pss_name = params["hash_algorithm"]["algorithm"].native
            if pss_name not in _HASHES:
                raise ValueError(f"algoritmo PSS non supportato: {pss_name}")
            pss_hash = _HASHES[pss_name]
            pad = padding.PSS(mgf=padding.MGF1(pss_hash()), salt_length=params["salt_length"].native)
            public_key.verify(signature, data, pad, pss_hash())
        else:
            public_key.verify(signature, data, padding.PKCS1v15(), hash_cls())
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(signature, data, ec.ECDSA(hash_cls()))
    else:
        raise ValueError(f"tipo di chiave non supportato: {type(public_key).__name__}")  # noqa: TRY004 - esito, non errore di tipo


def fiscal_code(serial: str | None) -> str | None:
    if not serial:
        return None
    # Nei certificati qualificati italiani: "TINIT-<codice fiscale>" (ETSI EN 319 412-1)
    return serial.split("-", 1)[1] if serial.upper().startswith(("TINIT-", "IDCIT-", "VATIT-")) else serial


def join_names(*parts: str | None) -> str | None:
    words = [p for p in parts if p]
    return " ".join(words) or None

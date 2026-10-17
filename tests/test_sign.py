"""Fase 4: firma PAdES e CAdES, verifica completa, firma grafica."""

import datetime as dt
import json

import pikepdf
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from PIL import Image

from wmt_pdf.cli import main

pytest.importorskip("aiohttp")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Ogni test con una cache vuota: l'elenco europeo non c'è e la rete non si usa."""
    monkeypatch.setenv("WPDF_CACHE_DIR", str(tmp_path / "cache"))


# --- infrastruttura di prova -------------------------------------------------------------


def _name(cn, extra=()):
    return x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"), x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      *extra])


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    now = dt.datetime.now(dt.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = _name("Certificatore di Prova")
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=10))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Mario Rossi", [x509.NameAttribute(NameOID.SERIAL_NUMBER, "TINIT-RSSMRA80A01F205X")]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    p12 = d / "mario.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(
        b"mario", key, cert, [ca], serialization.BestAvailableEncryption(b"segreta")))
    ca_pem = d / "ca.pem"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    # un certificato autofirmato, che nessuno ha emesso
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_name = _name("Sconosciuto")
    other = (
        x509.CertificateBuilder().subject_name(other_name).issuer_name(other_name)
        .public_key(other_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
        .sign(other_key, hashes.SHA256())
    )
    other_p12 = d / "sconosciuto.p12"
    other_p12.write_bytes(pkcs12.serialize_key_and_certificates(
        b"x", other_key, other, None, serialization.BestAvailableEncryption(b"segreta")))
    return {"p12": p12, "ca": ca_pem, "other": other_p12}


def sign(capsys, src, out, pki, *extra, p12=None):
    import os

    os.environ["P12_PW"] = "segreta"
    return run(capsys, "sign", src, "--p12", p12 or pki["p12"], "--p12-password-env", "P12_PW", "-o", out, *extra)


# --- firma PAdES ---------------------------------------------------------------------------


def test_pades_sign_and_verify(capsys, tmp_path, text_pdf, pki):
    out = tmp_path / "firmato.pdf"
    code, stdout, err = sign(capsys, text_pdf, out, pki, "--reason", "Approvazione")
    assert code == 0, err
    assert "Mario Rossi" in stdout
    code, report, err = run(capsys, "verify", out, "--trust-root", pki["ca"])
    assert code == 0, report + err
    assert "Esito: VALIDA" in report
    assert "RSSMRA80A01F205X" in report
    assert "radice indicata" in report
    assert "non indica servizi di revoca" in report  # il certificato di prova non ha OCSP né CRL


def test_offline_is_never_valid(capsys, tmp_path, text_pdf, pki):
    out = tmp_path / "firmato.pdf"
    sign(capsys, text_pdf, out, pki)
    code, report, _ = run(capsys, "verify", out, "--trust-root", pki["ca"], "--offline", "--json")
    data = json.loads(report)["signatures"][0]
    assert code == 3
    assert data["verdict"] == "NON VERIFICABILE" and data["revocation_checked"] is False


def test_unsigned_pdf_is_not_exit_zero(capsys, text_pdf):
    code, report, _ = run(capsys, "verify", text_pdf)
    assert code == 3 and "Nessuna firma" in report


def test_untrusted_timestamp_is_not_valid(capsys, tmp_path, text_pdf, pki, monkeypatch):
    from pyhanko.sign import signers, timestamps

    import wmt_pdf.sign as sign_mod

    rogue = signers.SimpleSigner.load_pkcs12(str(pki["other"]), passphrase=b"segreta")
    stamper = timestamps.DummyTimeStamper(tsa_cert=rogue.signing_cert, tsa_key=rogue.signing_key)
    monkeypatch.setattr(sign_mod, "timestamper", lambda url: stamper)
    out = tmp_path / "marca_falsa.pdf"
    assert sign(capsys, text_pdf, out, pki, "--tsa", "http://tsa.example")[0] == 0
    code, report, _ = run(capsys, "verify", out, "--trust-root", pki["ca"])
    assert code == 3 and "Esito: VALIDA" not in report


def test_stamp_refuses_signed_pdf(capsys, tmp_path, pki):
    from reportlab.pdfgen import canvas

    src = tmp_path / "l.pdf"
    c = canvas.Canvas(str(src))
    c.drawString(50, 200, "In fede")
    c.showPage()
    c.save()
    signed = tmp_path / "f.pdf"
    sign(capsys, src, signed, pki)
    img = tmp_path / "firma.png"
    Image.new("RGB", (30, 10)).save(img)
    code, _, err = run(capsys, "stamp", signed, "--image", img, "--after", "In fede", "-o", tmp_path / "x.pdf")
    assert code == 1 and "--break-signatures" in err


def test_verify_without_trusted_issuer_is_indeterminate(capsys, tmp_path, text_pdf, pki):
    out = tmp_path / "firmato.pdf"
    sign(capsys, text_pdf, out, pki)
    code, report, _ = run(capsys, "verify", out, "--offline")
    assert code == 3
    assert "NON VERIFICABILE" in report
    assert "wpdf trust update" in report  # dice come ottenere l'elenco europeo


def test_verify_modified_after_signing(capsys, tmp_path, text_pdf, pki):
    out = tmp_path / "firmato.pdf"
    sign(capsys, text_pdf, out, pki)
    changed = tmp_path / "cambiato.pdf"
    with pikepdf.open(out) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(changed)  # riscrittura completa: la firma non copre più il file
    code, report, _ = run(capsys, "verify", changed, "--trust-root", pki["ca"], "--offline")
    assert code == 2
    assert "NON VALIDA" in report


def test_visible_signature_with_image(capsys, tmp_path, pki):
    from reportlab.pdfgen import canvas

    src = tmp_path / "contratto.pdf"
    c = canvas.Canvas(str(src))
    c.drawString(50, 200, "Firma")
    c.showPage()
    c.save()
    img = tmp_path / "firma.png"
    Image.new("RGBA", (300, 100), (0, 0, 120, 255)).save(img)
    out = tmp_path / "firmato.pdf"
    code, _, err = sign(capsys, src, out, pki, "--after", "Firma", "--image", img)
    assert code == 0, err
    with pikepdf.open(out) as pdf:
        widget = next(a for a in pdf.pages[0].obj.Annots if a.get("/FT") == pikepdf.Name.Sig)
        x0, y0, x1, y1 = (float(v) for v in widget.Rect)
        assert x1 - x0 > 50 and abs((y0 + y1) / 2 - 204) < 30
        assert "/AP" in widget


def test_sign_existing_field(capsys, tmp_path, pki):
    from reportlab.pdfgen import canvas

    src = tmp_path / "modulo.pdf"
    c = canvas.Canvas(str(src))
    c.drawString(50, 200, "Firma del richiedente")
    c.showPage()
    c.save()
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"fields": [{"name": "firma_richiedente", "type": "signature",
                                            "after": "Firma del richiedente"}]}), encoding="utf-8")
    form = tmp_path / "m.pdf"
    assert run(capsys, "form", src, "--spec", spec, "-o", form)[0] == 0
    out = tmp_path / "firmato.pdf"
    code, _, err = sign(capsys, form, out, pki, "--field", "firma_richiedente")
    assert code == 0, err
    _, listing, _ = run(capsys, "fields", out)
    assert "firma_richiedente" in listing and "firmato" in listing


def test_second_signature_keeps_first(capsys, tmp_path, text_pdf, pki):
    first = tmp_path / "uno.pdf"
    sign(capsys, text_pdf, first, pki)
    second = tmp_path / "due.pdf"
    code, _, err = sign(capsys, first, second, pki)
    assert code == 0, err
    code, report, _ = run(capsys, "verify", second, "--trust-root", pki["ca"])
    assert code == 0, report + err
    assert report.count("Esito: VALIDA") == 2


def test_wrong_p12_password(capsys, tmp_path, text_pdf, pki, monkeypatch):
    monkeypatch.setenv("SBAGLIATA", "no")
    code, _, err = run(capsys, "sign", text_pdf, "--p12", pki["p12"], "--p12-password-env", "SBAGLIATA",
                       "-o", tmp_path / "x.pdf")
    assert code == 1 and "password" in err and "Traceback" not in err


def test_pkcs11_missing_module(capsys, tmp_path, text_pdf):
    code, _, err = run(capsys, "sign", text_pdf, "--pkcs11", tmp_path / "manca.dll", "-o", tmp_path / "x.pdf")
    assert code == 1 and "modulo PKCS#11" in err


def test_timestamp_with_tsa(capsys, tmp_path, text_pdf, pki, monkeypatch):
    from pyhanko.sign import signers, timestamps

    import wmt_pdf.sign as sign_mod

    signer = signers.SimpleSigner.load_pkcs12(str(pki["p12"]), passphrase=b"segreta")
    dummy = timestamps.DummyTimeStamper(tsa_cert=signer.signing_cert, tsa_key=signer.signing_key)
    monkeypatch.setattr(sign_mod, "timestamper", lambda url: dummy)
    out = tmp_path / "marcato.pdf"
    code, _, err = sign(capsys, text_pdf, out, pki, "--tsa", "http://tsa.example")
    assert code == 0, err
    _, report, _ = run(capsys, "verify", out, "--trust-root", pki["ca"])
    assert "marca temporale" in report.lower() and "presente" in report


# --- firma CAdES (.p7m) ------------------------------------------------------------------------


def test_cades_sign_any_file(capsys, tmp_path, pki):
    src = tmp_path / "fattura.xml"
    src.write_text("<FatturaElettronica>prova</FatturaElettronica>", encoding="utf-8")
    code, _stdout, err = sign(capsys, src, tmp_path / "fattura.xml.p7m", pki, "--format", "cades")
    assert code == 0, err
    p7m = tmp_path / "fattura.xml.p7m"
    code, info, _ = run(capsys, "p7m", p7m, "-o", tmp_path / "estratto.xml")
    assert code == 0, info
    assert (tmp_path / "estratto.xml").read_bytes() == src.read_bytes()
    assert "fattura elettronica" in info
    code, report, err = run(capsys, "verify", p7m, "--trust-root", pki["ca"])
    assert code == 0, report + err
    assert "CAdES" in report and "VALIDA" in report


def test_cades_default_for_non_pdf_and_name(capsys, tmp_path, pki, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "nota.txt"
    src.write_text("ciao", encoding="utf-8")
    code, _stdout, err = sign(capsys, src, tmp_path / "nota.txt.p7m", pki)
    assert code == 0, err
    assert (tmp_path / "nota.txt.p7m").exists()


def test_verify_tampered_p7m(capsys, tmp_path, p7m_tampered):
    code, report, _ = run(capsys, "verify", p7m_tampered, "--offline")
    assert code == 2 and "NON VALIDA" in report


def test_verify_untrusted_self_signed(capsys, tmp_path, text_pdf, pki):
    out = tmp_path / "firmato.pdf"
    code, _, err = sign(capsys, text_pdf, out, pki, p12=pki["other"])
    assert code == 0, err
    code, report, _ = run(capsys, "verify", out, "--trust-root", pki["ca"], "--offline")
    assert code == 3 and "NON riconosciuto" in report


# --- firma grafica ----------------------------------------------------------------------------


def test_graphic_stamp(capsys, tmp_path, pki):
    from reportlab.pdfgen import canvas

    src = tmp_path / "lettera.pdf"
    c = canvas.Canvas(str(src))
    c.drawString(50, 200, "In fede")
    c.showPage()
    c.save()
    img = tmp_path / "firma.png"
    Image.new("RGBA", (300, 100), (0, 0, 0, 0)).save(img)
    out = tmp_path / "lettera_firmata.pdf"
    code, _, err = run(capsys, "stamp", src, "--image", img, "--after", "In fede", "-o", out)
    assert code == 0, err
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages[0].get_images()) == 1
    _, sigs, _ = run(capsys, "signatures", out)
    assert "Nessuna firma" in sigs  # firma grafica: nessun valore legale da firma digitale


# --- elenco europeo dei fidati -------------------------------------------------------------------


def test_trust_status_without_cache(capsys):
    code, out, _ = run(capsys, "trust", "status")
    assert code == 0
    assert "non scaricato" in out and "wpdf trust update" in out


def test_cades_timestamp_is_read(capsys, tmp_path, pki, monkeypatch):
    from pyhanko.sign import signers, timestamps

    import wmt_pdf.sign as sign_mod

    signer = signers.SimpleSigner.load_pkcs12(str(pki["p12"]), passphrase=b"segreta")
    stamper = timestamps.DummyTimeStamper(tsa_cert=signer.signing_cert, tsa_key=signer.signing_key)
    monkeypatch.setattr(sign_mod, "timestamper", lambda url: stamper)
    src = tmp_path / "nota.txt"
    src.write_text("ciao", encoding="utf-8")
    out = tmp_path / "nota.txt.p7m"
    assert sign(capsys, src, out, pki, "--tsa", "http://tsa.example")[0] == 0
    _, report, _ = run(capsys, "verify", out, "--trust-root", pki["ca"])
    assert "Marca temporale: presente" in report

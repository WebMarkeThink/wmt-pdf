"""Fixture generate al volo: nessun PDF binario nel repo."""

import datetime as dt
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

HEADER = "Studio Esempio S.r.l. - Documento riservato"


def _draw_header_footer(c: canvas.Canvas, page: int) -> None:
    c.setFont("Helvetica", 9)
    c.drawString(50, 810, HEADER)
    c.drawString(50, 30, f"Pagina {page} di 3")


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("fixtures")


@pytest.fixture(scope="session")
def text_pdf(fixtures_dir) -> Path:
    path = fixtures_dir / "testo.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Contratto di prova")
    c.setAuthor("WebMarkeThink")
    bodies = [
        ["Articolo 1 - Oggetto", "Il fornitore si impegna a consegnare il servizio.", "La città è Milano."],
        ["Articolo 2 - Durata", "Il contratto dura dodici mesi.", "Parola chiave: zafferano."],
        ["Articolo 3 - Recesso", "Il recesso è possibile con preavviso di 30 giorni."],
    ]
    for i, lines in enumerate(bodies, start=1):
        _draw_header_footer(c, i)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, 760, lines[0])
        c.setFont("Helvetica", 11)
        for j, line in enumerate(lines[1:]):
            c.drawString(50, 730 - j * 18, line)
        c.showPage()
    c.save()
    return path


@pytest.fixture(scope="session")
def table_pdf(fixtures_dir) -> Path:
    path = fixtures_dir / "tabella.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(50, 790, "Preventivo lavori")
    data = [["Voce", "Quantità", "Prezzo"], ["Sito web", "1", "1500"], ["Hosting", "12", "10"]]
    table = Table(data, colWidths=[150, 80, 80])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.8, colors.black)]))
    _, h = table.wrapOn(c, 400, 400)
    table.drawOn(c, 50, 740 - h)
    c.drawString(50, 600, "Totale da concordare.")
    c.showPage()
    c.save()
    return path


@pytest.fixture(scope="session")
def scanned_pdf(fixtures_dir) -> Path:
    """Pagina con solo un'immagine di testo, come una scansione."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default(size=64)
    draw.text((60, 120), "FATTURA NUMERO 4521", fill="black", font=font)
    img_path = fixtures_dir / "scan.png"
    img.save(img_path)
    path = fixtures_dir / "scansione.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(str(img_path), 0, 500, width=595, height=192)
    c.showPage()
    c.save()
    return path


@pytest.fixture(scope="session")
def cert_and_key(fixtures_dir):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Mario Rossi"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, "TINIT-RSSMRA80A01F205X"),
        ]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(
            x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True
        )
        .sign(key, hashes.SHA256())
    )
    key_path = fixtures_dir / "key.pem"
    cert_path = fixtures_dir / "cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key, cert_path, key_path


def _sign_p7m(data: bytes, cert, key) -> bytes:
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(data)
        .add_signer(cert, key, hashes.SHA256())
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )


@pytest.fixture(scope="session")
def p7m_file(fixtures_dir, text_pdf, cert_and_key) -> Path:
    cert, key, *_ = cert_and_key
    path = fixtures_dir / "testo.pdf.p7m"
    path.write_bytes(_sign_p7m(text_pdf.read_bytes(), cert, key))
    return path


@pytest.fixture(scope="session")
def p7m_base64(fixtures_dir, p7m_file) -> Path:
    import base64

    path = fixtures_dir / "testo_b64.pdf.p7m"
    path.write_bytes(base64.encodebytes(p7m_file.read_bytes()))
    return path


@pytest.fixture(scope="session")
def p7m_nested(fixtures_dir, p7m_file, cert_and_key) -> Path:
    cert, key, *_ = cert_and_key
    path = fixtures_dir / "testo.pdf.p7m.p7m"
    path.write_bytes(_sign_p7m(p7m_file.read_bytes(), cert, key))
    return path


@pytest.fixture(scope="session")
def p7m_tampered(fixtures_dir, p7m_file, text_pdf) -> Path:
    raw = bytearray(p7m_file.read_bytes())
    start = raw.find(text_pdf.read_bytes()[200:240])
    assert start > 0
    raw[start + 5] ^= 0x01  # un byte del contenuto firmato
    path = fixtures_dir / "alterato.pdf.p7m"
    path.write_bytes(bytes(raw))
    return path


@pytest.fixture(scope="session")
def signed_pdf(fixtures_dir, text_pdf, cert_and_key) -> Path:
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    _, _, cert_path, key_path = cert_and_key
    signer = signers.SimpleSigner.load(str(key_path), str(cert_path))
    path = fixtures_dir / "firmato.pdf"
    with text_pdf.open("rb") as inf:
        w = IncrementalPdfFileWriter(inf)
        meta = signers.PdfSignatureMetadata(field_name="Firma1")
        with path.open("wb") as out:
            signers.sign_pdf(w, meta, signer=signer, output=out)
    return path


@pytest.fixture(scope="session")
def signed_then_modified_pdf(fixtures_dir, signed_pdf) -> Path:
    """PDF firmato e poi modificato con un aggiornamento incrementale (testo aggiunto in pagina)."""
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    path = fixtures_dir / "firmato_modificato.pdf"
    with signed_pdf.open("rb") as inf:
        w = IncrementalPdfFileWriter(inf)
        stream = generic.StreamObject(stream_data=b"BT /F1 24 Tf 100 100 Td (999999 EUR) Tj ET")
        w.add_stream_to_page(0, w.add_object(stream))
        with path.open("wb") as out:
            w.write(out)
    return path


@pytest.fixture(scope="session")
def p7m_no_signers(fixtures_dir, text_pdf) -> Path:
    from asn1crypto import cms

    signed = cms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [],
            "encap_content_info": {"content_type": "data", "content": text_pdf.read_bytes()},
            "signer_infos": [],
        }
    )
    path = fixtures_dir / "senza_firme.pdf.p7m"
    path.write_bytes(cms.ContentInfo({"content_type": "signed_data", "content": signed}).dump())
    return path


@pytest.fixture(scope="session")
def p7m_broken(fixtures_dir, p7m_file) -> Path:
    raw = bytearray(p7m_file.read_bytes())
    raw[1] ^= 0x7F  # lunghezza della struttura esterna rovinata
    path = fixtures_dir / "rotto.pdf.p7m"
    path.write_bytes(bytes(raw))
    return path


@pytest.fixture(autouse=True)
def _italian_and_private_settings(monkeypatch, tmp_path_factory):
    """I test controllano i messaggi in italiano e non devono leggere né scrivere le impostazioni dell'utente."""
    from wmt_pdf.gui import documents

    monkeypatch.setenv("WPDF_LANG", "it")
    settings_file = tmp_path_factory.mktemp("impostazioni") / "impostazioni.json"
    monkeypatch.setattr(documents, "_settings_path", lambda: settings_file)
    yield
    from wmt_pdf.i18n import set_language

    set_language("it")

"""Modifica del testo sulle pagine scansionate: righe dell'OCR, copertura e riscrittura."""

import io

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from wmt_pdf.cli import main
from wmt_pdf.ocrlayer import edit_scan_line, scan_lines, scan_status, upright_pages
from wmt_pdf.source import CliError


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def scanned(tmp_path):
    """Una scansione color crema con il testo scuro, più lo strato invisibile come lo scrive l'OCR di wmt-pdf."""
    image = Image.new("RGB", (595, 842), (250, 245, 230))
    ImageDraw.Draw(image).rectangle([72, 100, 300, 112], fill=(20, 30, 90))  # la riga «scritta»
    picture = io.BytesIO()
    image.save(picture, "PNG")
    picture.seek(0)
    base = tmp_path / "base.pdf"
    c = canvas.Canvas(str(base), pagesize=(595, 842))
    c.drawImage(ImageReader(picture), 0, 0, 595, 842)
    c.showPage()
    c.save()
    layer = io.BytesIO()
    c = canvas.Canvas(layer, pagesize=(595, 842))
    for y, line in ((730, "Riga da cambiare"), (600, "Riga da tenere")):
        text = c.beginText()
        text.setTextRenderMode(3)
        text.setFont("Helvetica", 12)
        text.setTextOrigin(72, y)
        text.textOut(line)
        c.drawText(text)
    c.showPage()
    c.save()
    out = tmp_path / "scansione.pdf"
    with pikepdf.open(base) as pdf, pikepdf.open(io.BytesIO(layer.getvalue())) as overlay:
        pdf.pages[0].add_overlay(overlay.pages[0])
        pdf.save(out)
    return out


def _texts(path):
    return [line["text"] for line in scan_lines(path.read_bytes(), 1)["lines"]]


def _pixel(path, x, y):
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].render(scale=1).to_pil().convert("RGB").getpixel((x, y))
    finally:
        doc.close()


def test_lines_of_a_scanned_page(scanned):
    info = scan_lines(scanned.read_bytes(), 1)
    assert info["image_page"] is True
    assert [line["text"] for line in info["lines"]] == ["Riga da cambiare", "Riga da tenere"]


def test_text_page_is_not_a_scan(tmp_path):
    path = tmp_path / "testo.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 700, "Testo vero")
    c.showPage()
    c.save()
    assert scan_lines(path.read_bytes(), 1) == {"image_page": False, "lines": []}


def test_edit_replaces_the_line_also_in_the_hidden_text(scanned, tmp_path):
    line = scan_lines(scanned.read_bytes(), 1)["lines"][0]
    out = tmp_path / "cambiato.pdf"
    edit_scan_line(scanned.read_bytes(), out, 1, line["bbox"], "Testo nuovo")
    texts = _texts(out)
    assert "Testo nuovo" in texts and "Riga da tenere" in texts and "Riga da cambiare" not in texts


def test_cover_uses_the_background_colour_and_text_the_ink(scanned, tmp_path):
    # La riga «scritta» dell'immagine: va coperta col crema dello sfondo, non di bianco.
    out = tmp_path / "coperto.pdf"
    edit_scan_line(scanned.read_bytes(), out, 1, [72, 730, 300, 742], "")
    red, green, blue = _pixel(out, 200, 106)
    assert abs(red - 250) <= 2 and abs(green - 245) <= 2 and abs(blue - 230) <= 2


def test_empty_text_removes_the_line(scanned, tmp_path):
    line = scan_lines(scanned.read_bytes(), 1)["lines"][0]
    out = tmp_path / "tolto.pdf"
    edit_scan_line(scanned.read_bytes(), out, 1, line["bbox"], "")
    assert _texts(out) == ["Riga da tenere"]


def test_signed_pdf_is_refused(signed_pdf, tmp_path):
    with pytest.raises(CliError):
        edit_scan_line(signed_pdf.read_bytes(), tmp_path / "x.pdf", 1, [10, 10, 100, 30], "x")
    assert scan_status(signed_pdf.read_bytes())["signed"] is True


def test_cli(capsys, scanned, tmp_path):
    out = tmp_path / "cli.pdf"
    code, stdout, err = run(capsys, "scanedit", scanned, "--page", 1, "--rect", "72,726,300,742", "--text", "Da riga di comando",
                            "-o", out)
    assert code == 0, err
    assert "riga modificata" in stdout
    code, _, err = run(capsys, "scanedit", scanned, "--page", 1, "--rect", "72,726", "-o", tmp_path / "y.pdf")
    assert code != 0 and "--rect" in err


@pytest.mark.parametrize("angle", [90, 180, 270])
@pytest.mark.parametrize("box", [[0, 0, 400, 600], [20, 30, 420, 630]])
def test_upright_pages_look_the_same(tmp_path, angle, box):
    path = tmp_path / "base.pdf"
    c = canvas.Canvas(str(path), pagesize=(400, 600))
    c.setFillColorRGB(0.8, 0.1, 0.1)
    c.rect(40, 500, 120, 60, fill=1)
    c.setFillColorRGB(0.1, 0.1, 0.8)
    c.rect(250, 60, 90, 40, fill=1)
    c.showPage()
    c.save()
    with pikepdf.open(path) as pdf:
        pdf.pages[0].mediabox = box
        pdf.pages[0].obj.Rotate = angle
        pdf.save(tmp_path / "ruotato.pdf")
    rotated = (tmp_path / "ruotato.pdf").read_bytes()
    straight = upright_pages(rotated)
    with pikepdf.open(io.BytesIO(straight)) as pdf:
        assert "/Rotate" not in pdf.pages[0].obj

    def picture(data):
        doc = pdfium.PdfDocument(data)
        try:
            return doc[0].render(scale=1).to_pil().convert("RGB")
        finally:
            doc.close()

    before, after = picture(rotated), picture(straight)
    assert before.size == after.size
    for point in ((before.width // 4, before.height // 4), (before.width // 2, before.height // 2), (60, 60)):
        assert before.getpixel(point) == after.getpixel(point)
    small = zip(before.resize((40, 60)).getdata(), after.resize((40, 60)).getdata(), strict=True)
    assert sum(abs(p - q) for x, y in small for p, q in zip(x, y, strict=True)) < 2000


def test_edit_refuses_rotated_page_with_foreign_ocr(scanned, tmp_path):
    with pikepdf.open(scanned) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(tmp_path / "ruotato.pdf")
    with pytest.raises(CliError, match="pagina ruotata"):
        edit_scan_line((tmp_path / "ruotato.pdf").read_bytes(), tmp_path / "x.pdf", 1, [72, 726, 300, 742], "x")


def _background_pdf(path):
    picture = io.BytesIO()
    Image.new("RGB", (100, 140), (240, 240, 255)).save(picture, "PNG")
    picture.seek(0)
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.drawImage(ImageReader(picture), 0, 0, 595, 842)
    c.drawString(72, 700, "Testo vero sopra uno sfondo")
    c.showPage()
    c.save()
    return path


def test_digital_pdf_with_background_is_not_a_scan(tmp_path):
    assert scan_lines(_background_pdf(tmp_path / "sfondo.pdf").read_bytes(), 1)["image_page"] is False


def test_second_edit_of_a_line_replaces_the_first(scanned, tmp_path):
    line = scan_lines(scanned.read_bytes(), 1)["lines"][0]
    first = tmp_path / "primo.pdf"
    edit_scan_line(scanned.read_bytes(), first, 1, line["bbox"], "Primo")
    info = scan_lines(first.read_bytes(), 1)
    assert info["image_page"] is True  # ora ha testo visibile, ma resta una scansione
    again = next(item for item in info["lines"] if item["text"] == "Primo")
    second = tmp_path / "secondo.pdf"
    edit_scan_line(first.read_bytes(), second, 1, again["bbox"], "Secondo")
    assert sorted(_texts(second)) == ["Riga da tenere", "Secondo"]


def test_cover_ignores_transforms_left_by_the_page(scanned, tmp_path):
    with pikepdf.open(scanned) as pdf:
        pdf.pages[0].contents_add(pdf.make_stream(b"0.5 0 0 0.5 0 0 cm\n"))
        pdf.save(tmp_path / "scala.pdf")
    out = tmp_path / "coperto.pdf"
    edit_scan_line((tmp_path / "scala.pdf").read_bytes(), out, 1, [72, 730, 300, 742], "")
    assert _pixel(out, 200, 106)[0] > 200  # coperto dove si vede, non a metà scala


def test_shared_form_stays_on_the_other_page(scanned, tmp_path):
    with pikepdf.open(scanned) as pdf:
        pdf.pages.append(pdf.pages[0])
        pdf.save(tmp_path / "doppia.pdf")
    data = (tmp_path / "doppia.pdf").read_bytes()
    line = scan_lines(data, 1)["lines"][0]
    out = tmp_path / "una.pdf"
    edit_scan_line(data, out, 1, line["bbox"], "")
    assert "Riga da cambiare" in [item["text"] for item in scan_lines(out.read_bytes(), 2)["lines"]]


def test_offset_mediabox_edit(scanned, tmp_path):
    with pikepdf.open(scanned) as pdf:
        pdf.pages[0].mediabox = [20, 30, 615, 872]
        pdf.save(tmp_path / "spostato.pdf")
    data = (tmp_path / "spostato.pdf").read_bytes()
    line = scan_lines(data, 1)["lines"][0]
    out = tmp_path / "cambiato.pdf"
    edit_scan_line(data, out, 1, line["bbox"], "Nuova")
    lines = scan_lines(out.read_bytes(), 1)["lines"]
    assert "Riga da cambiare" not in [item["text"] for item in lines]
    new = next(item for item in lines if item["text"] == "Nuova")
    assert new["bbox"][0] == pytest.approx(line["bbox"][0], abs=3) and new["bbox"][1] == pytest.approx(line["bbox"][1], abs=6)


def test_upright_keeps_stamped_pages_and_restrictions(tmp_path):
    path = tmp_path / "base.pdf"
    c = canvas.Canvas(str(path), pagesize=(400, 600))
    c.drawString(72, 500, "x")
    c.showPage()
    c.save()
    with pikepdf.open(path) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.pages[0].obj.Annots = pdf.make_indirect(pikepdf.Array([pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Stamp, Rect=[10, 10, 50, 50])]))
        pdf.save(tmp_path / "timbro.pdf")
    stamped = (tmp_path / "timbro.pdf").read_bytes()
    assert upright_pages(stamped) == stamped
    with pikepdf.open(path) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(tmp_path / "limitato.pdf", encryption=pikepdf.Encryption(owner="segreto", user="", allow=pikepdf.Permissions(extract=False)))
    with pikepdf.open(io.BytesIO(upright_pages((tmp_path / "limitato.pdf").read_bytes()))) as pdf:
        assert pdf.is_encrypted

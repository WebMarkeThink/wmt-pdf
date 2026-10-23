"""wpdf assemble: pagine scelte, ordinate e ruotate da uno o più file."""

import pikepdf
import pypdfium2 as pdfium
import pytest
from reportlab.pdfgen import canvas

from wmt_pdf.cli import main
from wmt_pdf.ops import Sheet, parse_sheets


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def make(path, label, pages):
    c = canvas.Canvas(str(path))
    for n in range(1, pages + 1):
        c.setFont("Helvetica", 30)
        c.drawString(72, 700, f"{label}{n}")
        c.showPage()
    c.save()
    return path


def texts(path):
    doc = pdfium.PdfDocument(str(path))
    try:
        return [doc[i].get_textpage().get_text_range().strip() for i in range(len(doc))]
    finally:
        doc.close()


def rotations(path):
    with pikepdf.open(path) as pdf:
        return [int(p.obj.get("/Rotate", 0)) % 360 for p in pdf.pages]


@pytest.fixture
def two(tmp_path):
    return make(tmp_path / "a.pdf", "A", 3), make(tmp_path / "b.pdf", "B", 2)


def test_parse_sheets():
    assert parse_sheets("", [2, 1]) == [Sheet(0, 1), Sheet(0, 2), Sheet(1, 1)]
    assert parse_sheets("2:1@90,1:3-1", [3, 1]) == [Sheet(1, 1, 90), Sheet(0, 3), Sheet(0, 2), Sheet(0, 1)]
    assert parse_sheets("2,2@-90", [3]) == [Sheet(0, 2), Sheet(0, 2, 270)]


@pytest.mark.parametrize("spec, message", [
    ("3:1", "inesistente"), ("1:9", "pagine"), ("1:1@45", "multiplo"), ("x", "non valida"), (",", "nessuna pagina"),
])
def test_parse_sheets_errors(spec, message):
    from wmt_pdf.source import CliError

    with pytest.raises(CliError, match=message):
        parse_sheets(spec, [3, 2])


def test_assemble_order_and_rotation(capsys, tmp_path, two):
    out = tmp_path / "composto.pdf"
    code, stdout, err = run(capsys, "assemble", *two, "--pages", "2:2,1:1-2,2:1@90", "-o", out)
    assert code == 0, err
    assert texts(out) == ["B2", "A1", "A2", "B1"]
    assert rotations(out) == [0, 0, 0, 90]
    assert "4 pagine" in stdout


def test_same_page_twice_rotated_independently(capsys, tmp_path, two):
    out = tmp_path / "doppia.pdf"
    code, _, err = run(capsys, "assemble", two[0], "--pages", "2,2@180,3-1", "-o", out)
    assert code == 0, err
    assert texts(out) == ["A2", "A2", "A3", "A2", "A1"]
    assert rotations(out) == [0, 180, 0, 0, 0]


def test_all_pages_and_bookmarks(capsys, tmp_path, two):
    out = tmp_path / "tutto.pdf"
    code, _, err = run(capsys, "assemble", *two, "--bookmarks", "-o", out)
    assert code == 0, err
    assert texts(out) == ["A1", "A2", "A3", "B1", "B2"]
    with pikepdf.open(out) as pdf, pdf.open_outline() as outline:
        assert [item.title for item in outline.root] == ["a", "b"]


def test_fields_survive(capsys, tmp_path, two):
    form = tmp_path / "modulo.pdf"
    spec = tmp_path / "campi.json"
    spec.write_text('{"fields": [{"name": "nome", "type": "text", "page": 2, "rect": [72, 600, 300, 620]}]}')
    assert run(capsys, "form", two[0], "--spec", spec, "-o", form)[0] == 0
    out = tmp_path / "con_campi.pdf"
    code, _, err = run(capsys, "assemble", form, two[1], "--pages", "2:1,1:2", "-o", out)
    assert code == 0, err
    with pikepdf.open(out) as pdf:
        assert [f.fully_qualified_name for f in pdf.acroform.fields] == ["nome"]


def test_existing_bookmarks_kept(capsys, tmp_path, two):
    with pikepdf.open(two[0], allow_overwriting_input=True) as pdf:
        with pdf.open_outline() as outline:
            outline.root.extend([pikepdf.OutlineItem("Inizio", 0), pikepdf.OutlineItem("Fine", 2)])
        pdf.save(two[0])
    out = tmp_path / "riordinato.pdf"
    code, _, err = run(capsys, "assemble", two[0], "--pages", "3@90,2", "-o", out)
    assert code == 0, err
    with pikepdf.open(out) as pdf, pdf.open_outline() as outline:
        assert [(item.title, pdf.pages.index(pikepdf.Page(item.destination[0]))) for item in outline.root] == [
            ("Fine", 0)]  # la pagina di «Inizio» non c'è più

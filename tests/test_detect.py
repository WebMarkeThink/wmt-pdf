"""«Prepara modulo»: campi proposti da trattini, quadratini, linee e riquadri di un PDF piatto."""

import json
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen import canvas

from wmt_pdf.cli import main
from wmt_pdf.detect import detect_fields


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def flat_form(tmp_path):
    """Modulo piatto come quelli fatti con un programma di grafica: testo con ____ e □, niente campi."""
    barcode = pytest.importorskip("barcode")  # porta con sé DejaVu Sans Mono, che ha il quadratino □
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("DejaVuMono", str(Path(barcode.__file__).parent / "fonts" / "DejaVuSansMono.ttf")))
    path = tmp_path / "piatto.pdf"
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.setFont("DejaVuMono", 10)
    c.drawString(50, 780, "Cognome ____________________ Nome ____________________")
    c.drawString(50, 755, "Codice fiscale ________________________")
    c.drawString(50, 730, "□ Proprietario   □ Inquilino   □ Usufruttuario")
    c.drawString(50, 705, "Note ______________________________________")
    c.drawString(50, 690, "     ______________________________________")
    c.drawString(50, 675, "     ______________________________________")
    c.setFont("Helvetica", 10)
    c.drawString(50, 640, "Firma")
    c.line(90, 638, 300, 638)  # linea disegnata con spazio vuoto sopra
    c.drawString(50, 610, "Testo sottolineato")
    c.line(50, 607, 140, 607)  # sottolineatura: c'è testo sopra, non è un campo
    c.rect(50, 570, 12, 12)  # quadratino disegnato
    c.drawString(68, 572, "Accetto")
    c.rect(50, 480, 300, 60)  # riquadro vuoto: testo su più righe
    c.drawString(50, 545, "Osservazioni")
    c.showPage()
    c.save()
    return path


def by_name(spec):
    return {f["name"]: f for f in spec["fields"]}


def test_detects_underscores_boxes_lines(flat_form):
    fields = by_name(detect_fields(flat_form.read_bytes()))
    assert fields["cognome"]["type"] == "text" and fields["nome"]["type"] == "text"
    assert fields["codice_fiscale"]["tooltip"] == "Codice fiscale"
    for name in ("proprietario", "inquilino", "usufruttuario", "accetto"):
        assert fields[name]["type"] == "checkbox", name
    assert fields["note"]["multiline"] is True  # tre righe di trattini una sotto l'altra: un campo solo
    assert fields["firma"]["type"] == "text"
    assert fields["osservazioni"]["multiline"] is True
    assert not any(n.startswith("testo_sottolineato") for n in fields)
    assert len(fields) == 10


def test_rects_follow_the_marks(flat_form):
    fields = by_name(detect_fields(flat_form.read_bytes()))
    x0, y0, x1, y1 = fields["cognome"]["rect"]
    assert 95 < x0 < 120 and x1 > 200 and 776 <= y0 <= 781 and y1 - y0 <= 18
    note = fields["note"]["rect"]
    assert note[1] < 680 and note[3] > 705  # dalla prima all'ultima riga
    box = fields["proprietario"]["rect"]
    assert 5 < box[2] - box[0] < 10 and abs((box[2] - box[0]) - (box[3] - box[1])) < 0.5


def test_names_avoid_existing_fields(flat_form, tmp_path):
    with pikepdf.open(flat_form) as pdf:
        pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=pikepdf.Array([
            pdf.make_indirect(pikepdf.Dictionary(T=pikepdf.String("nome"), FT=pikepdf.Name.Tx))])))
        pdf.save(tmp_path / "con_campo.pdf")
    fields = by_name(detect_fields((tmp_path / "con_campo.pdf").read_bytes()))
    assert "nome_2" in fields and "nome" not in fields


def test_cli_detect_then_create(capsys, flat_form, tmp_path):
    spec = tmp_path / "proposta.json"
    code, _, err = run(capsys, "form", flat_form, "--detect", "-o", spec)
    assert code == 0, err
    assert len(json.loads(spec.read_text(encoding="utf-8"))["fields"]) == 10
    out = tmp_path / "modulo.pdf"
    code, stdout, err = run(capsys, "form", flat_form, "--spec", spec, "-o", out)
    assert code == 0, err
    code, stdout, err = run(capsys, "fields", out, "--json")
    names = {f["name"] for f in json.loads(stdout)}
    assert {"cognome", "proprietario", "note"} <= names


def test_cli_auto(capsys, flat_form, tmp_path):
    out = tmp_path / "modulo.pdf"
    code, stdout, err = run(capsys, "form", flat_form, "--auto", "-o", out)
    assert code == 0, err
    assert "10 campi" in stdout


def test_plain_text_has_no_fields(tmp_path):
    path = tmp_path / "lettera.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 700, "Una lettera senza spazi da riempire.")
    c.showPage()
    c.save()
    assert detect_fields(path.read_bytes())["fields"] == []


def _simple(path, extra=None):
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.drawString(50, 780, "Nome ____________________")
    c.rect(50, 570, 12, 12)
    c.drawString(68, 572, "Accetto")
    if extra:
        extra(c)
    c.showPage()
    c.save()
    return path


def test_offset_mediabox_keeps_positions(tmp_path):
    src = _simple(tmp_path / "base.pdf")
    expected = by_name(detect_fields(src.read_bytes()))
    for box in ([20, 30, 615, 872], [-20, -30, 575, 812]):
        with pikepdf.open(src) as pdf:
            pdf.pages[0].mediabox = box
            pdf.save(tmp_path / "spostato.pdf")
        got = by_name(detect_fields((tmp_path / "spostato.pdf").read_bytes()))
        for name in ("nome", "accetto"):
            assert got[name]["rect"] == pytest.approx(expected[name]["rect"], abs=0.1), (box, name)
    assert expected["accetto"]["rect"] == pytest.approx([51, 571, 61, 581], abs=0.1)


def test_rotated_pages_are_skipped(tmp_path):
    src = _simple(tmp_path / "base.pdf")
    with pikepdf.open(src) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(tmp_path / "ruotato.pdf")
    spec = detect_fields((tmp_path / "ruotato.pdf").read_bytes())
    assert spec["fields"] == [] and spec["skipped"][0]["page"] == 1


def test_existing_widgets_are_not_proposed_again(capsys, tmp_path):
    src = _simple(tmp_path / "base.pdf")
    out = tmp_path / "modulo.pdf"
    assert run(capsys, "form", src, "--auto", "-o", out)[0] == 0
    spec = detect_fields(out.read_bytes())
    assert spec["fields"] == [] and len(spec["existing"]) == 2 and "nome" in spec["names"]


def test_table_of_contents_and_rules_are_not_fields(tmp_path):
    def extra(c):
        c.drawString(50, 400, "Capitolo primo ........................ 12")
        c.line(20, 300, 575, 300)  # filetto da margine a margine
        c.line(200, 250, 400, 250)  # linea senza niente accanto
    names = by_name(detect_fields(_simple(tmp_path / "indice.pdf", extra).read_bytes()))
    assert set(names) == {"nome", "accetto"}

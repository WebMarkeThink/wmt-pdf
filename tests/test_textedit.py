"""Inserimento e modifica del testo."""

import json

import pikepdf
import pypdfium2 as pdfium
import pytest
from reportlab.pdfgen import canvas

from wmt_pdf.cli import main


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def text_of(path, page=1) -> str:
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[page - 1].get_textpage().get_text_range().replace(" ", " ")
    finally:
        doc.close()


@pytest.fixture
def letter(tmp_path):
    path = tmp_path / "lettera.pdf"
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Il canone annuo è 1.200 euro")
    c.drawString(72, 670, "Scadenza: 31 dicembre")
    c.showPage()
    c.save()
    return path


@pytest.fixture
def embedded_font_pdf(tmp_path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    from wmt_pdf.convert import _font_files

    files = _font_files()
    if not files:
        pytest.skip("nessun font TrueType di sistema")
    pdfmetrics.registerFont(TTFont("Prova", files[0]))
    path = tmp_path / "incorporato.pdf"
    c = canvas.Canvas(str(path))
    c.setFont("Prova", 16)
    c.drawString(72, 700, "Mario Rossi")
    c.showPage()
    c.save()
    return path


def test_textobjects_list(capsys, letter):
    code, out, _ = run(capsys, "textobjects", letter, "--page", "1", "--json")
    assert code == 0
    objs = json.loads(out)
    assert [o["text"] for o in objs] == ["Il canone annuo è 1.200 euro", "Scadenza: 31 dicembre"]
    x0, y0, _x1, y1 = objs[0]["bbox"]
    assert 70 < x0 < 74 and y0 < 704 < y1
    assert objs[0]["font_size"] == 14 and objs[0]["embedded"] is False


def test_replace_in_place(capsys, tmp_path, letter):
    out = tmp_path / "nuova.pdf"
    code, stdout, err = run(capsys, "replace", letter, "1.200", "1.350 €", "-o", out)
    assert code == 0, err
    assert "1 sostituzion" in stdout
    text = text_of(out)
    assert "1.350 €" in text and "1.200" not in text
    assert "Scadenza: 31 dicembre" in text


def test_replace_not_found(capsys, tmp_path, letter):
    out = tmp_path / "x.pdf"
    code, _, err = run(capsys, "replace", letter, "inesistente", "nuovo", "-o", out)
    assert code == 1 and "non trovato" in err
    assert not out.exists()


def test_replace_with_missing_glyphs_recreates(capsys, tmp_path, embedded_font_pdf):
    out = tmp_path / "nuova.pdf"
    code, stdout, err = run(capsys, "replace", embedded_font_pdf, "Mario Rossi", "Zoë Bianchi", "-o", out)
    assert code == 0, err
    assert "font di sistema" in stdout  # le lettere nuove non erano nel font incorporato
    assert "Zoë Bianchi" in text_of(out)


def test_replace_same_letters_stays_in_place(capsys, tmp_path, embedded_font_pdf):
    out = tmp_path / "nuova.pdf"
    code, stdout, _ = run(capsys, "replace", embedded_font_pdf, "Mario Rossi", "Rosi Mario", "-o", out)
    assert code == 0
    assert "font di sistema" not in stdout
    assert "Rosi Mario" in text_of(out)


def test_edit_object_at_point(capsys, tmp_path, letter):
    out = tmp_path / "modificata.pdf"
    code, _, err = run(capsys, "edittext", letter, "--page", "1", "--at", "100,674", "--text", "Scadenza: 30 giugno",
                       "-o", out)
    assert code == 0, err
    text = text_of(out)
    assert "Scadenza: 30 giugno" in text and "31 dicembre" not in text
    assert "1.200" in text


def test_edit_object_nothing_at_point(capsys, tmp_path, letter):
    code, _, err = run(capsys, "edittext", letter, "--page", "1", "--at", "500,100", "--text", "x",
                       "-o", tmp_path / "x.pdf")
    assert code == 1 and "nessun testo" in err


def test_add_text_multiline_with_accents(capsys, tmp_path, letter):
    out = tmp_path / "aggiunto.pdf"
    code, _, err = run(capsys, "addtext", letter, "--page", "1", "--at", "72,600",
                       "--text", "Nota aggiunta: perché è così\nseconda riga €", "--size", "12", "--color", "#c00000",
                       "-o", out)
    assert code == 0, err
    text = text_of(out)
    assert "Nota aggiunta: perché è così" in text and "seconda riga €" in text
    _, listing, _ = run(capsys, "textobjects", out, "--page", "1", "--json")
    added = [o for o in json.loads(listing) if o["text"].startswith("Nota")]
    assert added and added[0]["bbox"][1] < 612 and added[0]["font_size"] == 12


def test_text_edit_keeps_password(capsys, tmp_path, letter):
    locked = tmp_path / "protetta.pdf"
    run(capsys, "protect", letter, "--new-password", "pw", "-o", locked)
    out = tmp_path / "nuova.pdf"
    code, _, err = run(capsys, "replace", locked, "1.200", "1.300", "--password", "pw", "-o", out)
    assert code == 0, err
    with pikepdf.open(out, password="pw") as pdf:
        assert pdf.is_encrypted


def test_text_edit_refuses_signed(capsys, tmp_path, signed_pdf):
    code, _, err = run(capsys, "replace", signed_pdf, "Milano", "Roma", "-o", tmp_path / "x.pdf")
    assert code == 1 and "firm" in err


def test_bad_coordinates_and_color(capsys, tmp_path, letter):
    code, _, err = run(capsys, "addtext", letter, "--page", "1", "--at", "abc", "--text", "x", "-o", tmp_path / "x.pdf")
    assert code == 1 and "--at" in err
    code, _, err = run(capsys, "addtext", letter, "--page", "1", "--at", "1,1", "--text", "x", "--color", "rosso",
                       "-o", tmp_path / "x.pdf")
    assert code == 1 and "--color" in err


def test_recreated_text_keeps_invisible_render_mode(capsys, tmp_path, embedded_font_pdf):
    import pikepdf

    invisible = tmp_path / "ocr.pdf"
    with pikepdf.open(embedded_font_pdf) as pdf:
        page = pdf.pages[0]
        page.contents_coalesce()
        data = page.obj.Contents.read_bytes().replace(b"BT", b"BT 3 Tr", 1)  # testo invisibile, come l'OCR
        page.obj.Contents = pdf.make_stream(data)
        pdf.save(invisible)
    out = tmp_path / "nuova.pdf"
    code, stdout, err = run(capsys, "replace", invisible, "Mario Rossi", "Zoë Bianchi", "-o", out)
    assert code == 0 and "font di sistema" in stdout, err
    with pikepdf.open(out) as pdf:
        pdf.pages[0].contents_coalesce()
        assert b"3 Tr" in pdf.pages[0].obj.Contents.read_bytes()


def test_repeated_additions_do_not_duplicate_fonts(capsys, tmp_path, letter):
    current = letter
    for i in range(3):
        nxt = tmp_path / f"v{i}.pdf"
        run(capsys, "addtext", current, "--page", "1", "--at", f"72,{500 - i * 20}", "--text", f"Riga {i}", "-o", nxt)
        current = nxt
    first = (tmp_path / "v0.pdf").stat().st_size
    # Ogni aggiunta incorpora solo le lettere usate (decine di KB), mai il font intero (centinaia di KB).
    assert current.stat().st_size - first < 2 * 100_000


def test_edit_by_index(capsys, tmp_path, letter):
    out = tmp_path / "m.pdf"
    code, _, err = run(capsys, "edittext", letter, "--page", "1", "--index", "1", "--text", "Scadenza: mai", "-o", out)
    assert code == 0, err
    assert "Scadenza: mai" in text_of(out)


def _pixel(path, page, x, y, dpi=36):
    doc = pdfium.PdfDocument(str(path))
    try:
        image = doc[page - 1].render(scale=dpi / 72).to_pil().convert("RGB")
        return image.getpixel((int(x * dpi / 72), int(image.height - y * dpi / 72)))
    finally:
        doc.close()


def test_edit_keeps_other_fills_colors(capsys, tmp_path, letter):
    """Regressione: rigenerando la pagina, i riempimenti in spazi colore non RGB diventavano neri."""
    colored = tmp_path / "colori.pdf"
    with pikepdf.open(letter) as pdf:
        page = pdf.pages[0]
        lab = pikepdf.Array([pikepdf.Name.Lab, pikepdf.Dictionary(WhitePoint=[0.9505, 1.0, 1.089])])
        page.obj.Resources.ColorSpace = pikepdf.Dictionary(CS0=lab)
        page.contents_add(pdf.make_stream(b"q /CS0 cs 95 0 0 scn 300 300 200 100 re f Q"), prepend=True)
        pdf.save(colored)
    before = _pixel(colored, 1, 400, 350)
    assert sum(before) > 600  # chiaro, quasi bianco
    out = tmp_path / "modificato.pdf"
    code, _, err = run(capsys, "replace", colored, "1.200", "1.350", "-o", out)
    assert code == 0, err
    assert _pixel(out, 1, 400, 350) == before
    out2 = tmp_path / "ricreato.pdf"
    code, _, err = run(capsys, "replace", colored, "Scadenza", "Scadenza Łódź", "-o", out2)
    assert code == 0, err
    assert "Scadenza Łódź" in text_of(out2)
    assert _pixel(out2, 1, 400, 350) == before


def test_added_text_is_editable_afterwards(capsys, tmp_path, letter):
    added = tmp_path / "aggiunto.pdf"
    run(capsys, "addtext", letter, "--page", "1", "--at", "72,500", "--text", "Nota provvisoria", "-o", added)
    out = tmp_path / "corretto.pdf"
    code, _, err = run(capsys, "replace", added, "provvisoria", "definitiva", "-o", out)
    assert code == 0, err
    assert "Nota definitiva" in text_of(out)

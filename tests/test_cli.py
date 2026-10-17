import json

import pytest

from wmt_pdf.cli import main
from wmt_pdf.pages import parse_pages


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


# --- intervalli di pagine -------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        (None, [1, 2, 3, 4, 5]),
        ("2", [2]),
        ("1-3", [1, 2, 3]),
        ("4-", [4, 5]),
        ("-2", [1, 2]),
        ("1,3-4,1", [1, 3, 4]),
        ("last", [5]),
    ],
)
def test_parse_pages(spec, expected):
    assert parse_pages(spec, 5) == expected


@pytest.mark.parametrize("spec", ["0", "6", "3-2", "a", "1-x"])
def test_parse_pages_invalid(spec):
    with pytest.raises(ValueError):
        parse_pages(spec, 5)


# --- info -----------------------------------------------------------------


def test_info(capsys, text_pdf):
    code, out, _ = run(capsys, "info", text_pdf)
    assert code == 0
    assert "Pagine: 3" in out
    assert "Contratto di prova" in out
    assert "senza testo: 0" in out


def test_info_json(capsys, scanned_pdf):
    code, out, _ = run(capsys, "info", scanned_pdf, "--json")
    data = json.loads(out)
    assert code == 0
    assert data["pages"] == 1
    assert data["pages_without_text"] == [1]


def test_missing_file(capsys, tmp_path):
    code, _, err = run(capsys, "info", tmp_path / "nonce.pdf")
    assert code == 1
    assert "non trovato" in err


# --- text -----------------------------------------------------------------


def test_text_pages_and_headers(capsys, text_pdf):
    code, out, _ = run(capsys, "text", text_pdf)
    assert code == 0
    assert "## Pagina 1" in out and "## Pagina 3" in out
    assert "La città è Milano." in out
    # intestazione ripetuta su ogni pagina: scritta una volta sola come nota
    assert out.count("Documento riservato") == 1
    assert "Pagina 2 di 3" not in out


def test_text_keep_headers(capsys, text_pdf):
    _, out, _ = run(capsys, "text", text_pdf, "--keep-headers")
    assert out.count("Documento riservato") == 3


def test_text_page_range(capsys, text_pdf):
    _, out, _ = run(capsys, "text", text_pdf, "--pages", "2")
    assert "zafferano" in out
    assert "Milano" not in out


def test_text_budget_truncates_with_hint(capsys, text_pdf):
    _, out, _ = run(capsys, "text", text_pdf, "--max-tokens", "40")
    assert "troncato" in out
    assert "--pages" in out
    assert "Recesso" not in out


def test_text_json(capsys, text_pdf):
    _, out, _ = run(capsys, "text", text_pdf, "--json", "--pages", "1")
    data = json.loads(out)
    assert data["pages"][0]["page"] == 1
    assert "Milano" in data["pages"][0]["text"]


def test_text_table_as_markdown(capsys, table_pdf):
    _, out, _ = run(capsys, "text", table_pdf)
    assert "| Voce | Quantità | Prezzo |" in out
    assert "| Sito web | 1 | 1500 |" in out
    assert "Totale da concordare." in out
    # il testo della tabella non deve comparire due volte
    assert out.count("Hosting") == 1


def test_text_scanned_without_ocr_flags_page(capsys, scanned_pdf):
    _, out, _ = run(capsys, "text", scanned_pdf, "--ocr", "never")
    assert "senza testo" in out
    assert "immagin" in out


def test_tables_command(capsys, table_pdf):
    code, out, _ = run(capsys, "tables", table_pdf)
    assert code == 0
    assert "| Hosting | 12 | 10 |" in out
    assert "Preventivo" not in out


def test_tables_csv(capsys, table_pdf):
    _, out, _ = run(capsys, "tables", table_pdf, "--csv")
    assert "Voce,Quantità,Prezzo" in out


# --- search ---------------------------------------------------------------


def test_search(capsys, text_pdf):
    code, out, _ = run(capsys, "search", text_pdf, "ZAFFERANO")
    assert code == 0
    assert "p.2" in out
    assert "1 risultat" in out


def test_search_no_match(capsys, text_pdf):
    code, out, _ = run(capsys, "search", text_pdf, "inesistente")
    assert code == 0
    assert "0 risultati" in out


# --- render ---------------------------------------------------------------


def test_render_png(capsys, text_pdf, tmp_path):
    from PIL import Image

    target = tmp_path / "p1.png"
    code, out, _ = run(capsys, "render", text_pdf, "--page", "1", "--dpi", "50", "-o", target)
    assert code == 0
    assert str(target) in out
    with Image.open(target) as img:
        assert img.size == (413, 585) or abs(img.size[0] - 413) <= 1


def test_render_crop_gray(capsys, text_pdf, tmp_path):
    from PIL import Image

    target = tmp_path / "crop.png"
    run(capsys, "render", text_pdf, "--page", "1", "--dpi", "50", "--crop", "0,0,0.5,0.5", "--gray", "-o", target)
    with Image.open(target) as img:
        assert img.mode == "L"
        assert abs(img.size[0] - 207) <= 1


# --- p7m ------------------------------------------------------------------


def test_p7m_info(capsys, p7m_file):
    code, out, _ = run(capsys, "p7m", p7m_file)
    assert code == 0
    assert "Mario Rossi" in out
    assert "RSSMRA80A01F205X" in out
    assert "integra" in out.lower()
    assert "pdf" in out.lower()


def test_p7m_extract(capsys, p7m_file, text_pdf, tmp_path):
    target = tmp_path / "estratto.pdf"
    code, _, _ = run(capsys, "p7m", p7m_file, "-o", target)
    assert code == 0
    assert target.read_bytes() == text_pdf.read_bytes()


@pytest.mark.parametrize("fixture", ["p7m_base64", "p7m_nested"])
def test_p7m_variants(capsys, request, fixture, text_pdf, tmp_path):
    path = request.getfixturevalue(fixture)
    target = tmp_path / "estratto.pdf"
    code, out, _ = run(capsys, "p7m", path, "-o", target)
    assert code == 0
    assert target.read_bytes() == text_pdf.read_bytes()
    if fixture == "p7m_nested":
        assert "livelli di firma: 2" in out
        assert "- Livello 2: Mario Rossi" in out


def test_p7m_tampered(capsys, p7m_tampered):
    code, out, _ = run(capsys, "p7m", p7m_tampered)
    assert code == 2
    assert "NON" in out


def test_text_reads_inside_p7m(capsys, p7m_file):
    code, out, _ = run(capsys, "text", p7m_file, "--pages", "1")
    assert code == 0
    assert "Milano" in out
    assert "p7m" in out


# --- firme PAdES ----------------------------------------------------------


def test_signatures(capsys, signed_pdf):
    code, out, _ = run(capsys, "signatures", signed_pdf)
    assert code == 0
    assert "Firma1" in out
    assert "Mario Rossi" in out
    assert "integra" in out.lower()


def test_signatures_none(capsys, text_pdf):
    code, out, _ = run(capsys, "signatures", text_pdf)
    assert code == 0
    assert "Nessuna firma" in out


def test_info_counts_signatures(capsys, signed_pdf):
    _, out, _ = run(capsys, "info", signed_pdf)
    assert "Firme: 1" in out


# --- OCR (lento, scarica i modelli al primo uso) --------------------------


@pytest.mark.ocr
def test_ocr_auto(capsys, scanned_pdf):
    pytest.importorskip("rapidocr")
    code, out, _ = run(capsys, "text", scanned_pdf)
    assert code == 0
    assert "4521" in out
    assert "OCR" in out


# --- euristiche -------------------------------------------------------------


def test_looks_like_table():
    from wmt_pdf.extract import looks_like_table

    assert looks_like_table([["Voce", "Prezzo"], ["Sito", "1500"]])
    crowded = " ".join(["parola"] * 20)
    assert not looks_like_table([["Titolo", "", ""], [crowded, crowded, crowded]])
    assert not looks_like_table([["A", "", "", ""], ["", "", "", "B"]])


def test_strip_lines_handles_split_chunks():
    from wmt_pdf.extract import _norm, strip_lines

    repeated = {_norm("Capitale Sociale: Sottoscritto"), _norm("€ 30.000.000 - Versato")}
    text = "Capitale Sociale: Sottoscritto € 30.000.000 - Versato\nTesto vero con € 30.000.000"
    assert strip_lines(text, repeated) == "Testo vero con € 30.000.000"


def test_info_blank_page_is_not_scan(capsys, tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "vuota.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(50, 700, "Prima pagina con testo sufficiente.")
    c.showPage()
    c.showPage()
    c.save()
    _, out, _ = run(capsys, "info", path)
    assert "senza testo: 0" in out
    assert "vuote: 1 (pagine 2)" in out


# --- regressioni dalla revisione ---------------------------------------------


def test_p7m_without_signers_is_invalid(capsys, p7m_no_signers):
    code, _, _ = run(capsys, "p7m", p7m_no_signers)
    assert code == 2
    _, out, _ = run(capsys, "text", p7m_no_signers, "--pages", "1")
    assert "NON integro" in out


def test_broken_p7m_is_not_opened_as_pdf(capsys, p7m_broken):
    code, out, err = run(capsys, "text", p7m_broken)
    assert code == 1
    assert "p7m non leggibile" in err
    assert "Milano" not in out


def test_pdf_modified_after_signing_is_invalid(capsys, signed_then_modified_pdf):
    code, out, err = run(capsys, "signatures", signed_then_modified_pdf)
    assert code == 2
    assert "modificato dopo la firma" in out
    assert "Traceback" not in err


def test_p7m_output_never_overwrites_input(capsys, p7m_file, tmp_path):
    import shutil

    copy = tmp_path / "copia.pdf.p7m"
    shutil.copy(p7m_file, copy)
    code, _, err = run(capsys, "p7m", copy, "-o", copy)
    assert code == 1 and "coincide" in err
    assert copy.read_bytes() == p7m_file.read_bytes()


def test_p7m_output_needs_force(capsys, p7m_file, tmp_path):
    target = tmp_path / "esiste.pdf"
    target.write_bytes(b"vecchio")
    code, _, err = run(capsys, "p7m", p7m_file, "-o", target)
    assert code == 1 and "--force" in err
    assert run(capsys, "p7m", p7m_file, "-o", target, "--force")[0] == 0
    assert target.read_bytes().startswith(b"%PDF")


def test_cut_page_can_be_continued(capsys, text_pdf):
    _, first, _ = run(capsys, "text", text_pdf, "--pages", "1", "--max-tokens", "1")
    import re

    m = re.search(r"--from-char (\d+)", first)
    assert m, first
    _, second, _ = run(capsys, "text", text_pdf, "--pages", "1", "--from-char", m.group(1))
    full = run(capsys, "text", text_pdf, "--pages", "1")[1]
    body = lambda s: s.split("## Pagina 1", 1)[1]
    assert "Milano" in body(full)
    assert int(m.group(1)) > 0
    assert len(body(second)) < len(body(full))


def test_json_respects_budget(capsys, text_pdf):
    _, out, _ = run(capsys, "text", text_pdf, "--json", "--max-tokens", "1")
    data = json.loads(out)
    assert data["truncated"] is True
    assert data["next_from_char"] == len(data["pages"][0]["text"])


def test_render_dpi_bounds(capsys, text_pdf, tmp_path):
    code, _, err = run(capsys, "render", text_pdf, "--dpi", "5000", "-o", tmp_path / "x.png")
    assert code == 1 and "--dpi" in err


def test_search_snippet_aligned_with_accents():
    from wmt_pdf.extract import fold_with_map

    text = "Perché ßtrasse città"
    folded, index = fold_with_map(text)
    j = folded.find("citta")
    assert text[index[j] : index[j + 4] + 1] == "città"


def test_repeated_dates_do_not_remove_table_rows():
    from wmt_pdf.extract import PageStats, repeated_margin_lines, strip_lines

    stats = [PageStats(100, 0, 0, ["17/10/2026"]) for _ in range(4)]
    assert repeated_margin_lines(stats) == set()
    assert strip_lines("| 15/03/2024 | 01/04/2024 |", {"#/#/#"}) == "| 15/03/2024 | 01/04/2024 |"

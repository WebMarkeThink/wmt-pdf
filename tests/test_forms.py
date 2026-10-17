"""Fase 3: moduli compilabili, e campi e segnalibri conservati in merge, pages e split."""

import json

import pikepdf
import pypdfium2 as pdfium
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from wmt_pdf.cli import main


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def text_of(path) -> str:
    doc = pdfium.PdfDocument(str(path))
    out = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    doc.close()
    return "\n".join(out)


def fields_of(capsys, path) -> dict:
    code, out, err = run(capsys, "fields", path, "--json")
    assert code == 0, err
    return {f["name"]: f for f in json.loads(out)}


@pytest.fixture
def form_pdf(tmp_path):
    path = tmp_path / "modulo.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(50, 750, "Nome:")
    c.acroForm.textfield(name="nome", x=100, y=740, width=200, height=20)
    c.drawString(50, 705, "Privacy:")
    c.acroForm.checkbox(name="privacy", x=100, y=700, size=14)
    c.acroForm.choice(name="citta", value="Milano", options=["Milano", "Roma", "Torino"], x=100, y=660,
                      width=120, height=20)
    c.acroForm.radio(name="sesso", value="M", selected=True, x=100, y=620, size=12)
    c.acroForm.radio(name="sesso", value="F", selected=False, x=130, y=620, size=12)
    c.showPage()
    c.drawString(50, 750, "Seconda pagina")
    c.acroForm.textfield(name="note", x=100, y=700, width=300, height=40)
    c.showPage()
    c.save()
    return path


@pytest.fixture
def plain_pdf(tmp_path):
    path = tmp_path / "domanda.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 12)
    c.drawString(50, 750, "Il sottoscritto")
    c.drawString(50, 720, "Data di nascita")
    c.drawString(50, 690, "Accetto le condizioni")
    c.drawString(50, 600, "Firma")
    c.showPage()
    c.save()
    return path


# --- elenco ----------------------------------------------------------------------------


def test_fields_list(capsys, form_pdf):
    code, out, _ = run(capsys, "fields", form_pdf)
    assert code == 0
    assert "nome" in out and "testo" in out
    assert "privacy" in out and "casella" in out
    assert "citta" in out and "Milano" in out and "Roma" in out
    assert "sesso" in out and "scelta singola" in out
    assert "5 campi" in out


def test_fields_json(capsys, form_pdf):
    fields = fields_of(capsys, form_pdf)
    assert fields["nome"]["type"] == "text" and fields["nome"]["page"] == 1
    assert fields["privacy"]["type"] == "checkbox" and fields["privacy"]["value"] is False
    assert fields["citta"]["options"] == ["Milano", "Roma", "Torino"]
    assert set(fields["sesso"]["options"]) == {"M", "F"}
    assert fields["note"]["page"] == 2


def test_fields_none(capsys, plain_pdf):
    code, out, _ = run(capsys, "fields", plain_pdf)
    assert code == 0 and "Nessun campo" in out


# --- compilazione --------------------------------------------------------------------------


def test_fill_json(capsys, tmp_path, form_pdf):
    data = tmp_path / "dati.json"
    data.write_text(json.dumps({"nome": "Mario Rossi", "privacy": True, "citta": "Roma", "sesso": "F",
                                "note": "Città di nascita: Milano"}), encoding="utf-8")
    out = tmp_path / "compilato.pdf"
    code, stdout, err = run(capsys, "fill", form_pdf, "--data", data, "-o", out)
    assert code == 0, err
    assert "5 campi" in stdout
    fields = fields_of(capsys, out)
    assert fields["nome"]["value"] == "Mario Rossi"
    assert fields["privacy"]["value"] is True
    assert fields["citta"]["value"] == "Roma"
    assert fields["sesso"]["value"] == "F"
    with pikepdf.open(out) as pdf:
        widget = pdf.pages[0].obj.Annots[0]
        assert "/AP" in widget  # aspetto generato: si vede anche nei visualizzatori che non lo rigenerano


def test_fill_and_flatten(capsys, tmp_path, form_pdf):
    data = tmp_path / "dati.json"
    data.write_text(json.dumps({"nome": "Mario Rossi", "privacy": "sì"}), encoding="utf-8")
    out = tmp_path / "piatto.pdf"
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "--flatten", "-o", out)
    assert code == 0, err
    assert "Mario Rossi" in text_of(out)
    _, listing, _ = run(capsys, "fields", out)
    assert "Nessun campo" in listing


def test_fill_unknown_field(capsys, tmp_path, form_pdf):
    data = tmp_path / "dati.json"
    data.write_text(json.dumps({"cognome": "Rossi"}), encoding="utf-8")
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "-o", tmp_path / "x.pdf")
    assert code == 1
    assert "cognome" in err and "nome" in err  # dice anche quali campi esistono


def test_fill_invalid_option(capsys, tmp_path, form_pdf):
    data = tmp_path / "dati.json"
    data.write_text(json.dumps({"citta": "Napoli"}), encoding="utf-8")
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "-o", tmp_path / "x.pdf")
    assert code == 1
    assert "Napoli" in err and "Torino" in err


def test_fill_csv_many(capsys, tmp_path, form_pdf):
    data = tmp_path / "elenco.csv"
    data.write_text("nome;privacy;citta\nMario Rossi;sì;Roma\nAnna Bianchi;no;Torino\n", encoding="utf-8")
    outdir = tmp_path / "moduli"
    code, _stdout, err = run(capsys, "fill", form_pdf, "--data", data, "--name-column", "nome", "-o", outdir)
    assert code == 0, err
    names = sorted(p.name for p in outdir.glob("*.pdf"))
    assert names == ["Anna Bianchi.pdf", "Mario Rossi.pdf"]
    assert fields_of(capsys, outdir / "Anna Bianchi.pdf")["citta"]["value"] == "Torino"
    assert fields_of(capsys, outdir / "Anna Bianchi.pdf")["privacy"]["value"] is False


def test_fill_csv_single_row_to_file(capsys, tmp_path, form_pdf):
    data = tmp_path / "uno.csv"
    data.write_text("nome,citta\nLuca Verdi,Milano\n", encoding="utf-8")
    out = tmp_path / "uno.pdf"
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "-o", out)
    assert code == 0, err
    assert fields_of(capsys, out)["nome"]["value"] == "Luca Verdi"


def test_flatten_keeps_other_annotations(capsys, tmp_path, form_pdf):
    marked = tmp_path / "evidenziato.pdf"
    run(capsys, "highlight", form_pdf, "Nome", "-o", marked)
    out = tmp_path / "piatto.pdf"
    code, _, err = run(capsys, "flatten", marked, "-o", out)
    assert code == 0, err
    _, listing, _ = run(capsys, "annotations", out)
    assert "Evidenziazione" in listing
    _, fields, _ = run(capsys, "fields", out)
    assert "Nessun campo" in fields


# --- creazione di campi ----------------------------------------------------------------


def test_form_add_fields(capsys, tmp_path, plain_pdf):
    spec = tmp_path / "campi.json"
    spec.write_text(json.dumps({"fields": [
        {"name": "nome", "type": "text", "after": "Il sottoscritto", "width": 220, "required": True},
        {"name": "nascita", "type": "date", "after": "Data di nascita"},
        {"name": "accetto", "type": "checkbox", "after": "Accetto le condizioni"},
        {"name": "provincia", "type": "choice", "options": ["MI", "MB", "VA"], "rect": [300, 640, 380, 660]},
        {"name": "note", "type": "text", "multiline": True, "rect": [50, 450, 400, 550]},
        {"name": "firma", "type": "signature", "after": "Firma", "width": 180, "height": 40},
    ]}), encoding="utf-8")
    out = tmp_path / "modulo.pdf"
    code, stdout, err = run(capsys, "form", plain_pdf, "--spec", spec, "-o", out)
    assert code == 0, err
    assert "6 campi" in stdout
    fields = fields_of(capsys, out)
    assert fields["nome"]["type"] == "text" and fields["nome"]["required"] is True
    assert fields["nascita"]["type"] == "text"
    assert fields["accetto"]["type"] == "checkbox"
    assert fields["provincia"]["options"] == ["MI", "MB", "VA"]
    assert fields["firma"]["type"] == "signature"
    # il campo dopo l'etichetta sta sulla stessa riga, a destra
    with pikepdf.open(out) as pdf:
        rect = next(a.Rect for a in pdf.pages[0].obj.Annots if str(a.get("/T", "")) == "nome")
        x0, y0, _, y1 = (float(v) for v in rect)
        assert x0 > 120 and y0 < 750 < y1 + 4
    # e il modulo creato si compila
    data = tmp_path / "dati.json"
    data.write_text(json.dumps({"nome": "Mario Rossi", "accetto": True, "provincia": "MB",
                                "nascita": "01/02/1980"}), encoding="utf-8")
    filled = tmp_path / "compilato.pdf"
    code, _, err = run(capsys, "fill", out, "--data", data, "--flatten", "-o", filled)
    assert code == 0, err
    text = text_of(filled)
    assert "Mario Rossi" in text and "01/02/1980" in text


def test_form_add_bad_spec(capsys, tmp_path, plain_pdf):
    spec = tmp_path / "campi.json"
    spec.write_text(json.dumps({"fields": [{"name": "x", "type": "text", "after": "Testo che non esiste"}]}),
                    encoding="utf-8")
    code, _, err = run(capsys, "form", plain_pdf, "--spec", spec, "-o", tmp_path / "x.pdf")
    assert code == 1 and "Testo che non esiste" in err
    spec.write_text(json.dumps({"fields": [{"name": "x", "type": "pulsante", "rect": [0, 0, 10, 10]}]}),
                    encoding="utf-8")
    code, _, err = run(capsys, "form", plain_pdf, "--spec", spec, "-o", tmp_path / "x.pdf")
    assert code == 1 and "pulsante" in err


def test_form_add_duplicate_name(capsys, tmp_path, form_pdf):
    spec = tmp_path / "campi.json"
    spec.write_text(json.dumps({"fields": [{"name": "nome", "type": "text", "rect": [50, 50, 200, 70]}]}),
                    encoding="utf-8")
    code, _, err = run(capsys, "form", form_pdf, "--spec", spec, "-o", tmp_path / "x.pdf")
    assert code == 1 and "esiste già" in err


# --- campi e segnalibri conservati ------------------------------------------------------------


def test_merge_keeps_fields(capsys, tmp_path, form_pdf):
    out = tmp_path / "unito.pdf"
    code, stdout, err = run(capsys, "merge", form_pdf, form_pdf, "-o", out)
    assert code == 0, err
    fields = fields_of(capsys, out)
    assert "nome" in fields and "nome+1" in fields  # i doppioni vengono rinominati
    assert "rinominati" in stdout
    data = tmp_path / "dati.json"
    data.write_text(json.dumps({"nome+1": "Seconda copia"}), encoding="utf-8")
    assert run(capsys, "fill", out, "--data", data, "-o", tmp_path / "c.pdf")[0] == 0


def test_pages_keeps_fields_of_kept_pages(capsys, tmp_path, form_pdf):
    out = tmp_path / "pagina2.pdf"
    run(capsys, "pages", form_pdf, "--keep", "2", "-o", out)
    assert set(fields_of(capsys, out)) == {"note"}


def test_split_keeps_fields(capsys, tmp_path, form_pdf):
    outdir = tmp_path / "parti"
    run(capsys, "split", form_pdf, "--every", "1", "-o", outdir)
    assert "nome" in fields_of(capsys, outdir / "modulo_1.pdf")
    assert set(fields_of(capsys, outdir / "modulo_2.pdf")) == {"note"}


@pytest.fixture
def outlined_pdf(tmp_path):
    path = tmp_path / "capitoli.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(1, 4):
        c.drawString(100, 700, f"Capitolo {i}")
        c.bookmarkPage(f"cap{i}")
        c.addOutlineEntry(f"Capitolo {i}", f"cap{i}", level=0)
        c.showPage()
    c.save()
    return path


def outline_titles(path) -> list:
    with pikepdf.open(path) as pdf, pdf.open_outline() as outline:
        def walk(items):
            return [(i.title, walk(i.children)) if i.children else i.title for i in items]
        return walk(outline.root)


def test_merge_nests_source_outlines(capsys, tmp_path, outlined_pdf, form_pdf):
    out = tmp_path / "unito.pdf"
    run(capsys, "merge", outlined_pdf, form_pdf, "-o", out)
    titles = outline_titles(out)
    assert titles[0] == ("capitoli", ["Capitolo 1", "Capitolo 2", "Capitolo 3"])
    assert titles[1] == "modulo"


def test_pages_keeps_outline_of_kept_pages(capsys, tmp_path, outlined_pdf):
    out = tmp_path / "scelte.pdf"
    run(capsys, "pages", outlined_pdf, "--order", "3,1", "-o", out)
    assert outline_titles(out) == ["Capitolo 3", "Capitolo 1"]
    with pikepdf.open(out) as pdf, pdf.open_outline() as outline:
        first = outline.root[0]
        assert pdf.pages.index(pikepdf.Page(first.destination[0])) == 0


def test_flatten_refuses_signed_pdf(capsys, tmp_path, signed_pdf):
    code, _, err = run(capsys, "flatten", signed_pdf, "-o", tmp_path / "x.pdf")
    assert code == 1 and "firme" in err


# --- regressioni dalla revisione della fase 3 ---


def _json(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_accents_survive_flatten(capsys, tmp_path, form_pdf):
    data = _json(tmp_path, "d.json", {"nome": "Niccolò Città perché €", "note": "Lunedì però"})
    out = tmp_path / "piatto.pdf"
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "--flatten", "-o", out)
    assert code == 0, err
    text = text_of(out)
    assert "Niccolò Città perché €" in text
    assert "?" not in text


def test_accents_in_appearance_without_flatten(capsys, tmp_path, form_pdf):
    data = _json(tmp_path, "d.json", {"nome": "àèìòù"})
    out = tmp_path / "c.pdf"
    run(capsys, "fill", form_pdf, "--data", data, "-o", out)
    with pikepdf.open(out) as pdf:
        widget = next(a for a in pdf.pages[0].obj.Annots if str(a.get("/T", "")) == "nome")
        assert "àèìòù".encode("cp1252") in widget.AP.N.read_bytes()


def test_non_cp1252_text_is_left_to_viewer(capsys, tmp_path, form_pdf):
    data = _json(tmp_path, "d.json", {"nome": "Ελληνικά"})
    out = tmp_path / "c.pdf"
    code, stdout, _ = run(capsys, "fill", form_pdf, "--data", data, "-o", out)
    assert code == 0 and "visualizzatore" in stdout
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "--flatten", "-o", tmp_path / "p.pdf")
    assert code == 1 and "non rappresentabili" in err


def test_batch_names_differing_by_case(capsys, tmp_path, form_pdf):
    data = tmp_path / "e.csv"
    data.write_text("nome\nRossi\nrossi\n", encoding="utf-8")
    outdir = tmp_path / "out"
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "--name-column", "nome", "-o", outdir)
    assert code == 0, err
    assert len(list(outdir.glob("*.pdf"))) == 2


def test_batch_validates_all_rows_first(capsys, tmp_path, form_pdf):
    data = tmp_path / "e.csv"
    data.write_text("nome;citta\nA;Roma\nB;Napoli\n", encoding="utf-8")
    outdir = tmp_path / "out"
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "-o", outdir)
    assert code == 1 and "riga 2" in err
    assert not outdir.exists() or not list(outdir.glob("*.pdf"))


def test_csv_extra_columns_and_blank_choices(capsys, tmp_path, form_pdf):
    data = tmp_path / "e.csv"
    data.write_text("file;nome;sesso;citta\nprimo;Anna;;\nsecondo;Bea;F;Roma\n", encoding="utf-8")
    outdir = tmp_path / "out"
    code, stdout, err = run(capsys, "fill", form_pdf, "--data", data, "--name-column", "file", "-o", outdir)
    assert code == 0, err
    assert "ignorate" in stdout and "file" in stdout
    assert fields_of(capsys, outdir / "primo.pdf")["sesso"]["value"] == "M"  # vuoto: invariato


def test_reserved_windows_names():
    from wmt_pdf.forms import safe_filename

    assert safe_filename("CON") == "_CON"
    assert safe_filename("nul.txt") == "_nul.txt"
    assert safe_filename("../../segreto") == "_.._segreto"


def test_date_format_cannot_inject_javascript(capsys, tmp_path, plain_pdf):
    spec = _json(tmp_path, "s.json", {"fields": [
        {"name": "d", "type": "date", "rect": [50, 50, 150, 70], "format": 'dd");app.alert("x");//'}]})
    code, _, err = run(capsys, "form", plain_pdf, "--spec", spec, "-o", tmp_path / "x.pdf")
    assert code == 1 and "formato data" in err


def test_fill_and_form_refuse_signed(capsys, tmp_path, signed_pdf):
    spec = _json(tmp_path, "s.json", {"fields": [{"name": "x", "type": "text", "rect": [50, 50, 150, 70]}]})
    code, _, err = run(capsys, "form", signed_pdf, "--spec", spec, "-o", tmp_path / "x.pdf")
    assert code == 1 and "firme" in err


@pytest.mark.parametrize("bad,needle", [
    (["solo testo"], "campo n. 1"),
    ([{"name": "r", "type": "radio", "rect": [50, 50, 60, 60], "options": [{"rect": [0, 0, 5, 5]}]}], "value"),
    ([{"name": "t", "type": "text", "rect": "abc"}], "rect"),
    ([{"name": "t", "type": "text", "rect": [0, 0, 10, 10], "page": "x"}], "page"),
    ([{"name": "t", "type": "text", "after": "Firma", "width": -5}], "positivi"),
    ([{"name": "t", "type": "text", "after": "Firma", "page": 9}], "fuori dal documento"),
    ([{"name": "a.b", "type": "text", "rect": [0, 0, 10, 10]}], "punto"),
])
def test_malformed_spec_errors(capsys, tmp_path, plain_pdf, bad, needle):
    spec = _json(tmp_path, "s.json", {"fields": bad})
    code, _, err = run(capsys, "form", plain_pdf, "--spec", spec, "-o", tmp_path / "x.pdf")
    assert code == 1 and needle in err and "errore (" not in err


def test_checkbox_value_false_string(capsys, tmp_path, plain_pdf):
    spec = _json(tmp_path, "s.json", {"fields": [
        {"name": "c", "type": "checkbox", "rect": [50, 50, 62, 62], "value": "false"}]})
    out = tmp_path / "m.pdf"
    run(capsys, "form", plain_pdf, "--spec", spec, "-o", out)
    assert fields_of(capsys, out)["c"]["value"] is False


def test_readonly_field_is_refused(capsys, tmp_path, plain_pdf):
    spec = _json(tmp_path, "s.json", {"fields": [
        {"name": "ro", "type": "text", "rect": [50, 50, 150, 70], "readonly": True}]})
    form = tmp_path / "m.pdf"
    run(capsys, "form", plain_pdf, "--spec", spec, "-o", form)
    data = _json(tmp_path, "d.json", {"ro": "x"})
    code, _, err = run(capsys, "fill", form, "--data", data, "-o", tmp_path / "c.pdf")
    assert code == 1 and "sola lettura" in err


def test_empty_data_rejected(capsys, tmp_path, form_pdf):
    data = _json(tmp_path, "d.json", [])
    code, _, err = run(capsys, "fill", form_pdf, "--data", data, "-o", tmp_path / "out")
    assert code == 1 and "nessun dato" in err

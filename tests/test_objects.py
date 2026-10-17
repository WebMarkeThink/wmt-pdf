"""Spostamento degli oggetti della pagina: immagini, elementi grafici, annotazioni, testo."""

import json

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from wmt_pdf.cli import main


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def objects(capsys, path, page=1) -> list[dict]:
    code, out, err = run(capsys, "objects", path, "--page", str(page), "--json")
    assert code == 0, err
    return json.loads(out)


def render(path):
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].render(scale=1).to_pil().convert("RGB")
    finally:
        doc.close()


def region(img, bbox, page_height, dx=0, dy=0):
    """La zona dell'oggetto sull'immagine della pagina (a scala 1 un punto è un pixel)."""
    x0, y0, x1, y1 = (round(v) for v in bbox)
    return img.crop((x0 + dx + 1, page_height - y1 - dy + 1, x1 + dx - 1, page_height - y0 - dy - 1))


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "quadrati.png"
    img = Image.new("RGB", (80, 60), (220, 30, 30))
    for x in range(40, 80):
        for y in range(60):
            img.putpixel((x, y), (30, 30, 200))  # metà rossa e metà blu: si vede se il ritaglio resta giusto
    img.save(path)
    return path


@pytest.fixture
def page_with_images(tmp_path, picture):
    path = tmp_path / "immagini.pdf"
    c = canvas.Canvas(str(path), pagesize=(400, 400))
    c.setFont("Helvetica", 12)
    c.drawString(40, 360, "Titolo della pagina")
    c.drawImage(str(picture), 40, 220, 80, 60)  # immagine come XObject
    c.saveState()  # immagine ritagliata: se ne vede solo la metà rossa
    p = c.beginPath()
    p.rect(200, 220, 40, 60)
    c.clipPath(p, stroke=0, fill=0)
    c.drawImage(str(picture), 200, 220, 80, 60)
    c.restoreState()
    c.drawInlineImage(str(picture), 40, 100, 40, 30)  # immagine in linea
    c.save()
    return path


def test_objects_lists_text_and_images(capsys, page_with_images):
    items = objects(capsys, page_with_images)
    kinds = [o["kind"] for o in items]
    assert kinds.count("text") == 1 and kinds.count("image") == 3
    code, out, _ = run(capsys, "objects", page_with_images)
    assert code == 0 and "immagine" in out and "Titolo della pagina" in out


@pytest.mark.parametrize("index", [0, 1, 2])
def test_move_image_looks_the_same(capsys, tmp_path, page_with_images, index):
    before = objects(capsys, page_with_images)
    image = [o for o in before if o["kind"] == "image"][index]
    out = tmp_path / f"spostata{index}.pdf"
    code, _, err = run(capsys, "move", page_with_images, "--page", "1", "--kind", "image", "--index", str(index),
                       "--by", "0,-70", "-o", out)
    assert code == 0, err
    after = objects(capsys, out)
    moved = [o for o in after if o["kind"] == "image"][-1]  # lo spostato va in fondo
    assert moved["bbox"] == pytest.approx([v + (0 if i % 2 == 0 else -70) for i, v in enumerate(image["bbox"])], abs=0.6)
    others_before = sorted(o["bbox"] for o in before if o is not image)
    others_after = sorted(o["bbox"] for o in after if o is not moved)
    assert others_after == others_before
    # Pixel per pixel: la zona nuova è identica a quella di prima (ritaglio compreso), la vecchia è bianca.
    old, new = render(page_with_images), render(out)
    assert region(new, image["bbox"], 400, 0, -70).tobytes() == region(old, image["bbox"], 400).tobytes()
    left = region(new, image["bbox"], 400)
    assert left.getcolors() == [(left.width * left.height, (255, 255, 255))]


def test_move_image_to_point(capsys, tmp_path, page_with_images):
    out = tmp_path / "punto.pdf"
    code, _, err = run(capsys, "move", page_with_images, "--page", "1", "--kind", "image", "--index", "0",
                       "--to", "250,50", "-o", out)
    assert code == 0, err
    moved = [o for o in objects(capsys, out) if o["kind"] == "image"][-1]
    assert moved["bbox"][:2] == pytest.approx([250, 50], abs=0.6)


def test_move_graphic_signature_and_move_again(capsys, tmp_path, page_with_images, picture):
    signed = tmp_path / "firmato.pdf"
    code, _, err = run(capsys, "stamp", page_with_images, "--image", picture, "--rect", "250,40,370,100",
                       "--page", "1", "-o", signed)
    assert code == 0, err
    graphic = [o for o in objects(capsys, signed) if o["kind"] == "graphic"]
    assert len(graphic) == 1
    once = tmp_path / "spostata1.pdf"
    code, _, err = run(capsys, "move", signed, "--page", "1", "--kind", "graphic", "--index", "0",
                       "--by=-200,250", "-o", once)
    assert code == 0, err
    twice = tmp_path / "spostata2.pdf"
    code, _, err = run(capsys, "move", once, "--page", "1", "--kind", "graphic", "--index", "0",
                       "--by", "10,5", "-o", twice)
    assert code == 0, err
    final = next(o for o in objects(capsys, twice) if o["kind"] == "graphic")
    assert final["bbox"] == pytest.approx([v + (-190 if i % 2 == 0 else 255) for i, v in enumerate(graphic[0]["bbox"])],
                                          abs=0.6)


def test_move_watermark_keeps_transparency(capsys, tmp_path, page_with_images):
    marked = tmp_path / "filigrana.pdf"
    code, _, err = run(capsys, "watermark", page_with_images, "--text", "BOZZA", "--opacity", "0.3", "--angle", "0",
                       "--size", "40", "-o", marked)
    assert code == 0, err
    graphic = [o for o in objects(capsys, marked) if o["kind"] == "graphic"]
    assert graphic and graphic[0]["label"] == "Filigrana"
    out = tmp_path / "filigrana_spostata.pdf"
    code, _, err = run(capsys, "move", marked, "--page", "1", "--kind", "graphic", "--index", "0",
                       "--by", "0,-150", "-o", out)
    assert code == 0, err
    old, new = render(marked), render(out)
    # Stessa trasparenza: il colore più scuro della filigrana è lo stesso (piena sarebbe più scura).
    def darkest(img):
        return min((sum(color), color) for _, color in img.getcolors(1 << 20))[1]

    old_color = darkest(region(old, graphic[0]["bbox"], 400))
    assert old_color != (255, 255, 255)
    assert darkest(region(new, graphic[0]["bbox"], 400, 0, -150)) == old_color
    # Spostata resta una filigrana: si toglie come prima.
    clean = tmp_path / "pulita.pdf"
    code, _, err = run(capsys, "unwatermark", out, "-o", clean)
    assert code == 0, err
    assert not [o for o in objects(capsys, clean) if o["kind"] == "graphic"]


def test_move_highlight_annotation(capsys, tmp_path, page_with_images):
    marked = tmp_path / "evidenziato.pdf"
    code, _, err = run(capsys, "highlight", page_with_images, "Titolo", "-o", marked)
    assert code == 0, err
    annot = [o for o in objects(capsys, marked) if o["kind"] == "annotation"]
    assert annot and annot[0]["label"] == "Evidenziazione"
    out = tmp_path / "evidenziato_spostato.pdf"
    code, _, err = run(capsys, "move", marked, "--page", "1", "--kind", "annotation", "--index", str(annot[0]["index"]),
                       "--by", "100,-200", "-o", out)
    assert code == 0, err
    moved = next(o for o in objects(capsys, out) if o["kind"] == "annotation")
    assert moved["bbox"] == pytest.approx([v + (100 if i % 2 == 0 else -200) for i, v in enumerate(annot[0]["bbox"])])
    with pikepdf.open(out) as pdf, pikepdf.open(marked) as orig:
        quads = [float(v) for v in pdf.pages[0].Annots[0].QuadPoints]
        old = [float(v) for v in orig.pages[0].Annots[0].QuadPoints]
        assert quads == pytest.approx([v + (100 if i % 2 == 0 else -200) for i, v in enumerate(old)])


def test_move_text_through_objects(capsys, tmp_path, page_with_images):
    out = tmp_path / "titolo.pdf"
    code, _, err = run(capsys, "move", page_with_images, "--page", "1", "--kind", "text", "--index", "0",
                       "--to", "100,300", "-o", out)
    assert code == 0, err
    text = next(o for o in objects(capsys, out) if o["kind"] == "text")
    assert text["label"] == "Titolo della pagina"
    assert text["bbox"][:2] == pytest.approx([100, 300], abs=0.6)


@pytest.mark.parametrize("argv, message", [
    (["--kind", "image", "--index", "9", "--by", "1,1"], "inesistente"),
    (["--kind", "annotation", "--index", "0", "--by", "1,1"], "inesistente"),
    (["--kind", "text", "--index", "0", "--by", "1"], "due numeri"),
])
def test_move_bad_arguments(capsys, tmp_path, page_with_images, argv, message):
    code, _, err = run(capsys, "move", page_with_images, "--page", "1", *argv, "-o", tmp_path / "x.pdf")
    assert code != 0 and message in err


def test_move_image_keeps_page_transparency(capsys, tmp_path, picture):
    path = tmp_path / "trasparente.pdf"
    c = canvas.Canvas(str(path), pagesize=(400, 400))
    c.setFillAlpha(0.3)  # trasparenza impostata sulla pagina, prima dell'immagine
    c.drawImage(str(picture), 40, 220, 80, 60)
    c.save()
    image = next(o for o in objects(capsys, path) if o["kind"] == "image")
    out = tmp_path / "trasparente_spostata.pdf"
    code, _, err = run(capsys, "move", path, "--page", "1", "--kind", "image", "--index", "0", "--by", "0,-100",
                       "-o", out)
    assert code == 0, err
    before = region(render(path), image["bbox"], 400)
    assert before.getpixel((5, 5)) != (220, 30, 30)  # davvero semitrasparente
    assert region(render(out), image["bbox"], 400, 0, -100).tobytes() == before.tobytes()


def _wrap_content(path, out, before: bytes, resources=None):
    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        original = b"".join(s.read_bytes() for s in ([page.Contents] if isinstance(page.Contents, pikepdf.Stream)
                                                   else page.Contents))
        if resources:
            resources(pdf, page)
        page.Contents = pdf.make_stream(b"q " + before + b"\n" + original + b"\nQ")
        pdf.save(out)


def test_move_refuses_even_odd_clip(capsys, tmp_path, page_with_images):
    clipped = tmp_path / "pari_dispari.pdf"
    _wrap_content(page_with_images, clipped, b"0 0 400 400 re 60 230 20 20 re W* n")
    code, _, err = run(capsys, "move", clipped, "--page", "1", "--kind", "image", "--index", "0", "--by", "0,-70",
                       "-o", tmp_path / "x.pdf")
    assert code != 0 and "pari-dispari" in err
    assert not (tmp_path / "x.pdf").exists()


def test_move_refuses_soft_mask(capsys, tmp_path, page_with_images):
    def add_mask(pdf, page):
        group = pdf.make_stream(b"1 g 0 0 400 400 re f", BBox=[0, 0, 400, 400], Subtype=pikepdf.Name.Form,
                                Group=pikepdf.Dictionary(S=pikepdf.Name.Transparency, CS=pikepdf.Name.DeviceGray))
        mask = pikepdf.Dictionary(Type=pikepdf.Name.Mask, S=pikepdf.Name.Luminosity, G=group)
        page.Resources.ExtGState = pikepdf.Dictionary(GSm=pikepdf.Dictionary(SMask=mask))

    masked = tmp_path / "maschera.pdf"
    _wrap_content(page_with_images, masked, b"/GSm gs", add_mask)
    code, _, err = run(capsys, "move", masked, "--page", "1", "--kind", "image", "--index", "0", "--by", "0,-70",
                       "-o", tmp_path / "x.pdf")
    assert code != 0 and "maschera" in err

"""Riduzione delle dimensioni: immagini ricampionate e ricompresse, struttura compattata."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from PIL import Image

from wmt_pdf.ops import open_pdf


@dataclass(frozen=True)
class Profile:
    dpi: int | None  # None: immagini intatte
    quality: int


PROFILES = {
    "email": Profile(dpi=110, quality=60),
    "standard": Profile(dpi=150, quality=75),
    "stampa": Profile(dpi=300, quality=85),
    "lossless": Profile(dpi=None, quality=100),
}


@dataclass
class Report:
    before: int
    after: int
    images_changed: int
    images_total: int
    kept_original: bool = False


def optimize(data: bytes, target: Path, profile: Profile, *, linearize: bool = False,
             password: str | None = None) -> Report:
    with open_pdf(data, password) as pdf:
        changed = total = 0
        if profile.dpi:
            done: set[tuple[int, int]] = set()
            for page in pdf.pages:
                limit = _pixel_limit(page, profile.dpi)
                for raw in page.get_images().values():
                    key = raw.objgen
                    if key in done:
                        continue  # stessa immagine usata su più pagine
                    done.add(key)
                    total += 1
                    if _recompress(raw, limit, profile.quality):
                        changed += 1
        pdf.remove_unreferenced_resources()
        buf = io.BytesIO()
        pdf.save(
            buf,
            encryption=pdf.is_encrypted,  # un file protetto resta protetto
            compress_streams=True,
            recompress_flate=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            linearize=linearize,
        )
    result = buf.getvalue()
    report = Report(before=len(data), after=len(result), images_changed=changed, images_total=total)
    if len(result) >= len(data) and not linearize:
        result, report.after, report.kept_original = data, len(data), True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result)
    return report


def _pixel_limit(page: pikepdf.Page, dpi: int) -> int:
    """Lato massimo in pixel: un'immagine che occupa tutta la pagina alla risoluzione scelta."""
    box = page.mediabox
    longest = max(abs(float(box[2]) - float(box[0])), abs(float(box[3]) - float(box[1])))
    return max(64, int(longest / 72 * dpi))


MAX_DECODE_PIXELS = 100_000_000  # oltre, decodificare costerebbe gigabyte di memoria


def _recompress(raw: pikepdf.Object, limit: int, quality: int) -> bool:
    if raw.get("/ImageMask") or "/Mask" in raw or raw.get("/SMaskInData"):
        return False  # maschere e trasparenze: la compressione con perdita le rovina
    try:
        pixels = int(raw.Width) * int(raw.Height)
    except (AttributeError, TypeError, ValueError):
        return False
    if pixels > MAX_DECODE_PIXELS:
        return False
    if int(raw.get("/BitsPerComponent", 8)) < 8:
        return False  # bianco e nero (CCITT, JBIG2): già compatti
    try:
        image = pikepdf.PdfImage(raw).as_pil_image()
    except Exception:  # noqa: BLE001 - formati che pikepdf non decodifica: si lasciano stare
        return False
    if image.mode in ("P", "RGBA"):
        image = image.convert("RGB")
    elif image.mode == "LA":
        image = image.convert("L")
    if image.mode not in ("RGB", "L"):
        return False  # CMYK e profili particolari: meglio non toccarli
    if max(image.size) > limit:
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)
    new = buf.getvalue()
    if len(new) >= len(raw.read_raw_bytes()):
        return False
    colorspace = _keep_icc(raw, image.mode) or {"RGB": pikepdf.Name.DeviceRGB, "L": pikepdf.Name.DeviceGray}[image.mode]
    raw.write(new, filter=pikepdf.Name.DCTDecode)
    raw.Width, raw.Height = image.size
    raw.ColorSpace = colorspace
    raw.BitsPerComponent = 8
    for key in ("/DecodeParms", "/Decode"):
        if key in raw:
            del raw[key]
    return True


def _keep_icc(raw: pikepdf.Object, mode: str):
    """Il profilo colore ICC originale, se ha lo stesso numero di canali: senza, i colori cambiano."""
    cs = raw.get("/ColorSpace")
    if isinstance(cs, pikepdf.Array) and len(cs) == 2 and cs[0] == pikepdf.Name.ICCBased:
        channels = int(cs[1].get("/N", 0))
        if channels == {"RGB": 3, "L": 1}[mode]:
            return cs
    return None

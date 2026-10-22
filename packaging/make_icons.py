"""Icona dell'applicazione (come il marchio nella barra laterale): .ico per Windows, .icns per macOS, .png.

Uso: uv run --group build python packaging/make_icons.py  → packaging/build/icons/
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "build" / "icons"
SIZE = 1024
TOP, BOTTOM = (91, 141, 239), (47, 92, 196)  # azzurro del marchio, sfumato


def draw(size: int = SIZE) -> Image.Image:
    scale = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Fondo: quadrato arrotondato con sfumatura verticale.
    gradient = Image.new("RGBA", (size, size))
    g = ImageDraw.Draw(gradient)
    for y in range(size):
        k = y / (size - 1)
        g.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * k) for a, b in zip(TOP, BOTTOM, strict=True)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40 * scale, 40 * scale, 984 * scale, 984 * scale], radius=220 * scale, fill=255)
    img.paste(gradient, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # Foglio con l'angolo piegato.
    x0, y0, x1, y1, fold = 300 * scale, 210 * scale, 724 * scale, 814 * scale, 150 * scale
    d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=(255, 255, 255, 255))
    d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(206, 220, 250, 255))
    # Righe di testo.
    for i, width in enumerate((300, 300, 220)):
        top = (470 + i * 92) * scale
        d.rounded_rectangle([370 * scale, top, (370 + width) * scale, top + 40 * scale], radius=20 * scale,
                            fill=(47, 92, 196, 255))
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = draw()
    icon.save(OUT / "wmt-pdf.png")
    icon.save(OUT / "wmt-pdf.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    icon.save(OUT / "wmt-pdf.icns")
    print(f"icone in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

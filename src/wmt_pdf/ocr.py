"""OCR delle pagine scansionate con RapidOCR (extra opzionale `ocr`)."""

from __future__ import annotations

import importlib.util
from functools import lru_cache

OCR_DPI = 200


def available() -> bool:
    return importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None


@lru_cache(maxsize=4)
def engine(lang: str):
    from rapidocr import RapidOCR

    # Codici dei modelli PP-OCRv6: it, en, de, fr, es, ch, ...
    return RapidOCR(params={"Rec.lang_type": lang, "Global.log_level": "error"})


def page_text(page, lang: str = "it") -> str:
    import numpy as np

    image = page.render(scale=OCR_DPI / 72).to_pil().convert("RGB")
    result = engine(lang)(np.asarray(image))
    if result.txts is None or result.boxes is None:
        return ""
    return _lines(list(zip(result.boxes, result.txts)))


def _lines(items) -> str:
    """Raggruppa i riquadri riconosciuti in righe, dall'alto in basso e da sinistra a destra."""
    entries = []
    for box, text in items:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        entries.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), text))
    entries.sort()
    lines: list[list[tuple]] = []
    for entry in entries:
        if lines and abs(entry[0] - lines[-1][0][0]) < max(entry[2], lines[-1][0][2]) * 0.5:
            lines[-1].append(entry)
        else:
            lines.append([entry])
    return "\n".join(" ".join(e[3] for e in sorted(line, key=lambda e: e[1])) for line in lines)

"""Intervalli di pagine: "1-3,7", "4-", "-2", "last"."""

from collections.abc import Iterable

from wmt_pdf.i18n import t


def parse_pages(spec: str | None, count: int) -> list[int]:
    """Restituisce i numeri di pagina (da 1) in ordine, senza duplicati."""
    if count < 1:
        return []
    if spec is None or not spec.strip():
        return list(range(1, count + 1))

    seen: set[int] = set()
    result: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        part = part.lower().replace("last", str(count))
        if "-" in part:
            left, right = part.split("-", 1)
            start = _number(left, count) if left else 1
            end = _number(right, count) if right else count
            if start > end:
                raise ValueError(t("intervallo al contrario: {part}", part=part))
            numbers: Iterable[int] = range(start, end + 1)
        else:
            numbers = [_number(part, count)]
        for n in numbers:
            if n not in seen:
                seen.add(n)
                result.append(n)
    return sorted(result)


def _number(text: str, count: int) -> int:
    if not text.isdigit():
        raise ValueError(t("pagina non valida: {text}", text=text))
    n = int(text)
    if not 1 <= n <= count:
        raise ValueError(t("pagina {n} fuori dal documento (1-{count})", n=n, count=count))
    return n

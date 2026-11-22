"""Read a pack size out of free text like '500 g', '1 L', '2 x 250 g', 'Pack of 6'.

Instamart only gives pack size as text (`quantityDescription`); there is no numeric
field. Anything we can't read returns None, and the offer is shown to the customer
as "size not recognised" instead of being priced on a guess.
"""
import re

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = (
    r"(kilograms?|kgs?|grams?|gms?|g|millilitres?|milliliters?|ml|"
    r"litres?|liters?|ltrs?|ltr|l|pieces?|pcs?|units?|count|nos?)(?![a-z])"
)
_X = r"\s*(?:x|\*)\s*"

_MULTI_FIRST = re.compile(rf"{_NUM}{_X}{_NUM}\s*{_UNIT}")          # 2 x 250 g
_MULTI_LAST = re.compile(rf"{_NUM}\s*{_UNIT}{_X}{_NUM}(?![\d.])")  # 250 g x 2
_PACK_OF = re.compile(rf"(?:pack|set|box|combo)\s+of\s+{_NUM}|{_NUM}\s*(?:pack|set)\b")
_DOZEN = re.compile(rf"(?:{_NUM}\s*)?dozen")
_RANGE = re.compile(rf"{_NUM}\s*(?:-|to)\s*{_NUM}\s*{_UNIT}")      # 100-120 g
_SINGLE = re.compile(rf"{_NUM}\s*{_UNIT}")


def _unit(raw: str) -> tuple[str, float]:
    u = raw.lower()
    if u.startswith("k"):
        return "g", 1000.0
    if u.startswith(("g",)):
        return "g", 1.0
    if u.startswith("m"):
        return "ml", 1.0
    if u.startswith("l"):
        return "ml", 1000.0
    return "count", 1.0  # pieces / pcs / units / count / nos


def parse_quantity(text: str | None) -> tuple[float, str] | None:
    """'2 x 250 g' -> (500.0, 'g'). Returns (size, unit) with unit in g / ml / count."""
    if not text:
        return None
    t = text.lower().replace("×", "x")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 1,000 g -> 1000 g

    if m := _MULTI_FIRST.search(t):
        n, size, raw = float(m.group(1)), float(m.group(2)), m.group(3)
        unit, factor = _unit(raw)
        return n * size * factor, unit
    if m := _MULTI_LAST.search(t):
        size, raw, n = float(m.group(1)), m.group(2), float(m.group(3))
        unit, factor = _unit(raw)
        return n * size * factor, unit
    if m := _RANGE.search(t):  # a range: take the low end, so we never under-buy
        unit, factor = _unit(m.group(3))
        return float(m.group(1)) * factor, unit
    if m := _SINGLE.search(t):
        unit, factor = _unit(m.group(2))
        size = float(m.group(1)) * factor
        return (size, unit) if size > 0 else None
    if m := _PACK_OF.search(t):
        n = float(m.group(1) or m.group(2))
        return (n, "count") if n > 0 else None
    if m := _DOZEN.search(t):
        return float(m.group(1) or 1) * 12, "count"
    return None

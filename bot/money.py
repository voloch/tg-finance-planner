"""BRL money parsing/formatting. Everything downstream of parse_amount_brl
deals in integer centavos -- floats only ever exist at the LLM boundary."""
from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"-?\d[\d.,]*\d|-?\d")
_NOISE_RE = re.compile(
    r"r\$|\bbrl\b|\breais?\b|\bconto(s)?\b|\bpila(s)?\b|\bpaus?\b", re.IGNORECASE
)


def parse_amount_brl(text: str) -> int | None:
    """Parse a Brazilian-Portuguese-or-English money expression into centavos.

    Handles: "50", "50,90", "R$ 1.234,56", "1234.56", "50 reais", "50 conto".
    Returns None if no number could be found.
    """
    if text is None:
        return None
    s = _NOISE_RE.sub("", text.strip().lower())
    m = _NUMBER_RE.search(s)
    if not m:
        return None
    num = m.group(0)

    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            # comma is the decimal separator, dots are thousands separators
            num = num.replace(".", "").replace(",", ".")
        else:
            # dot is the decimal separator, commas are thousands separators
            num = num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        if len(tail) == 2:
            num = f"{head.replace(',', '')}.{tail}"
        else:
            num = num.replace(",", "")
    elif "." in num:
        parts = num.split(".")
        # "50.90" (one dot, two trailing digits) reads as a decimal amount;
        # "1.234" / "1.234.567" (three-digit groups) reads as thousands.
        if not (len(parts) == 2 and len(parts[1]) == 2) and all(len(p) == 3 for p in parts[1:]):
            num = num.replace(".", "")

    try:
        value = float(num)
    except ValueError:
        return None
    return round(value * 100)


def format_brl(cents: int | None) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    reais, centavos = divmod(cents, 100)
    grouped = f"{reais:,}".replace(",", ".")
    return f"{sign}R$ {grouped},{centavos:02d}"

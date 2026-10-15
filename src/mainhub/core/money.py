"""USD amounts as integer cents (ADR 0001). Negative = money out, positive = money in."""

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from mainhub.errors import MoneyParseError

_THOUSANDS = re.compile(r"-?\d{1,3}(,\d{3})+(\.\d*)?")


def parse_cents(text: str) -> int:
    """Parse '$1,234.56', '-12.3', or '(5.00)' (negative) into integer cents."""
    cleaned = text.strip().replace("$", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if "," in cleaned:
        # Commas are only valid as thousands separators ("1,234.56"), never "1,2,3".
        if not _THOUSANDS.fullmatch(cleaned):
            raise MoneyParseError("not a valid amount")
        cleaned = cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise MoneyParseError("not a valid amount") from None
    if not value.is_finite():
        raise MoneyParseError("not a valid amount")
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def format_cents(cents: int) -> str:
    """Format integer cents for display, e.g. -1234 -> '-$12.34'."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"

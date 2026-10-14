"""USD amounts as integer cents (ADR 0001). Negative = money out, positive = money in."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from mainhub.errors import MoneyParseError


def parse_cents(text: str) -> int:
    """Parse '$1,234.56', '-12.3', or '(5.00)' (negative) into integer cents."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = "-" + cleaned[1:-1]
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

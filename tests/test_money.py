import pytest

from mainhub.core.money import format_cents, parse_cents
from mainhub.errors import MoneyParseError


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("12.34", 1234),
        ("-12.34", -1234),
        ("$1,234.56", 123456),
        ("(5.00)", -500),
        ("0", 0),
        ("1.005", 100),  # half-even
        ("1.015", 102),
        (" 7 ", 700),
    ],
)
def test_parse_cents(text: str, cents: int) -> None:
    assert parse_cents(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "NaN", "Infinity", "1.2.3"])
def test_parse_cents_rejects_garbage(text: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_cents(text)


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (1234, "$12.34"),
        (-1234, "-$12.34"),
        (0, "$0.00"),
        (5, "$0.05"),
        (123456789, "$1,234,567.89"),
    ],
)
def test_format_cents(cents: int, text: str) -> None:
    assert format_cents(cents) == text

"""Deterministic, editable categorizer for sources that provide no categories (e.g. SimpleFIN).

Rules are (substring, category) pairs matched case-insensitively against the description;
the first match wins. User rules (categories.toml) are checked before the built-in ones.
"""

import tomllib
from collections.abc import Sequence
from pathlib import Path

from mainhub.core.models import INCOME_CATEGORY, TRANSFER_CATEGORY
from mainhub.errors import MainHubError

Rule = tuple[str, str]

DEFAULT_RULES: tuple[Rule, ...] = (
    ("payroll", INCOME_CATEGORY),
    ("direct dep", INCOME_CATEGORY),
    ("transfer", TRANSFER_CATEGORY),
    ("autopay", TRANSFER_CATEGORY),
    ("payment thank you", TRANSFER_CATEGORY),
    ("credit card payment", TRANSFER_CATEGORY),
    ("whole foods", "Groceries"),
    ("trader joe", "Groceries"),
    ("safeway", "Groceries"),
    ("grocery", "Groceries"),
    ("coffee", "Dining"),
    ("restaurant", "Dining"),
    ("doordash", "Dining"),
    ("uber eats", "Dining"),
    ("uber", "Transport"),
    ("lyft", "Transport"),
    ("shell", "Transport"),
    ("chevron", "Transport"),
    ("netflix", "Subscriptions"),
    ("spotify", "Subscriptions"),
    ("rent", "Housing"),
)


def load_rules(path: Path | None) -> tuple[Rule, ...]:
    """User rules from a TOML file ([[rule]] match/category), followed by the defaults."""
    if path is None or not path.is_file():
        return DEFAULT_RULES
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        user = tuple((str(r["match"]).lower(), str(r["category"])) for r in data.get("rule", []))
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise MainHubError(f"invalid category rules file {path.name}") from exc
    return user + DEFAULT_RULES


def categorize(description: str, rules: Sequence[Rule] = DEFAULT_RULES) -> str | None:
    text = description.lower()
    for needle, category in rules:
        if needle in text:
            return category
    return None

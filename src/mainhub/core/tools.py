"""Tool layer: pure, typed, tested functions. Agents and the CLI read data only through these."""

from dataclasses import dataclass
from datetime import date

from mainhub.core.models import (
    INCOME_CATEGORY,
    TRANSFER_CATEGORY,
    UNCATEGORIZED,
    AccountId,
)
from mainhub.core.store import FinanceStore


@dataclass(frozen=True, slots=True)
class AccountBalance:
    account_id: AccountId
    name: str
    balance_cents: int


@dataclass(frozen=True, slots=True)
class NetWorth:
    as_of: date
    total_cents: int
    assets_cents: int
    liabilities_cents: int  # zero or negative
    by_account: tuple[AccountBalance, ...]


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category: str
    spent_cents: int  # positive = net money spent


@dataclass(frozen=True, slots=True)
class SpendingSummary:
    start: date
    end: date
    total_cents: int  # positive = net money spent
    by_category: tuple[CategoryTotal, ...]  # largest first


def net_worth(store: FinanceStore, as_of: date) -> NetWorth:
    """Sum of the latest balance per account on or before `as_of` (liabilities are negative)."""
    names = {a.id: a.name for a in store.list_accounts()}
    rows = tuple(
        sorted(
            (
                AccountBalance(b.account_id, names[b.account_id], b.amount_cents)
                for b in store.latest_balances(as_of)
            ),
            key=lambda r: r.name,
        )
    )
    assets = sum(r.balance_cents for r in rows if r.balance_cents > 0)
    liabilities = sum(r.balance_cents for r in rows if r.balance_cents < 0)
    return NetWorth(as_of, assets + liabilities, assets, liabilities, rows)


def get_spending(store: FinanceStore, start: date, end: date) -> SpendingSummary:
    """Net spending per category over [start, end], excluding transfers and income.

    Refunds (positive amounts in a spending category) reduce that category's total.
    """
    totals: dict[str, int] = {}
    for t in store.list_transactions(start, end):
        if t.category in (TRANSFER_CATEGORY, INCOME_CATEGORY):
            continue
        key = t.category or UNCATEGORIZED
        totals[key] = totals.get(key, 0) - t.amount_cents
    by_category = tuple(
        CategoryTotal(c, v) for c, v in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return SpendingSummary(start, end, sum(totals.values()), by_category)

from datetime import date

from mainhub.core.models import (
    Account,
    AccountId,
    AccountType,
    Balance,
    Transaction,
    TransactionId,
)
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth

CHK = AccountId("chk")
CARD = AccountId("card")


def _tx(i: str, day: int, cents: int, category: str | None) -> Transaction:
    return Transaction(TransactionId(i), CHK, date(2026, 3, day), cents, f"tx {i}", category)


def _seed(store: SqliteStore) -> None:
    store.upsert_accounts(
        [
            Account(CHK, "Checking", AccountType.CHECKING),
            Account(CARD, "Card", AccountType.CREDIT),
        ]
    )


def test_upserts_are_idempotent(store: SqliteStore) -> None:
    _seed(store)
    txs = [_tx("a", 1, -500, "Dining")]
    store.upsert_transactions(txs)
    store.upsert_transactions(txs)
    assert len(store.list_transactions(date(2026, 3, 1), date(2026, 3, 31))) == 1


def test_upsert_updates_changed_fields(store: SqliteStore) -> None:
    _seed(store)
    store.upsert_transactions([_tx("a", 1, -500, None)])
    store.upsert_transactions([_tx("a", 1, -500, "Dining")])
    [t] = store.list_transactions(date(2026, 3, 1), date(2026, 3, 1))
    assert t.category == "Dining"


def test_net_worth_uses_latest_balance_on_or_before_date(store: SqliteStore) -> None:
    _seed(store)
    store.upsert_balances(
        [
            Balance(CHK, date(2026, 3, 1), 100_000),
            Balance(CHK, date(2026, 3, 10), 150_000),
            Balance(CHK, date(2026, 3, 20), 999_999),
            Balance(CARD, date(2026, 3, 10), -20_000),
        ]
    )
    result = net_worth(store, date(2026, 3, 15))
    assert result.total_cents == 130_000
    assert result.assets_cents == 150_000
    assert result.liabilities_cents == -20_000


def test_net_worth_empty_store_is_zero(store: SqliteStore) -> None:
    assert net_worth(store, date(2026, 3, 15)).total_cents == 0


def test_spending_excludes_transfers_and_income_and_nets_refunds(store: SqliteStore) -> None:
    _seed(store)
    store.upsert_transactions(
        [
            _tx("a", 1, -1_000, "Dining"),
            _tx("b", 2, -2_000, "Dining"),
            _tx("c", 3, 500, "Dining"),  # refund
            _tx("d", 4, -9_000, "Transfer"),
            _tx("e", 5, 300_000, "Income"),
            _tx("f", 6, -700, None),
            _tx("g", 31, -123, "Dining"),  # outside range
        ]
    )
    result = get_spending(store, date(2026, 3, 1), date(2026, 3, 30))
    assert result.total_cents == 2_500 + 700 - 0
    assert [(c.category, c.spent_cents) for c in result.by_category] == [
        ("Dining", 2_500),
        ("Uncategorized", 700),
    ]


def test_spending_range_is_inclusive(store: SqliteStore) -> None:
    _seed(store)
    store.upsert_transactions([_tx("a", 1, -100, "X"), _tx("b", 2, -200, "X")])
    assert get_spending(store, date(2026, 3, 1), date(2026, 3, 2)).total_cents == 300

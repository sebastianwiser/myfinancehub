from datetime import date, timedelta

from mainhub.connectors.base import SyncData
from mainhub.core.models import (
    INCOME_CATEGORY,
    TRANSFER_CATEGORY,
    Account,
    AccountId,
    AccountType,
    Balance,
    Transaction,
    TransactionId,
)

CHECKING = AccountId("mock-checking")
SAVINGS = AccountId("mock-savings")
CARD = AccountId("mock-card")

# (account, description, category, amount_cents, every_n_days, day_offset)
_RECURRING: tuple[tuple[AccountId, str, str, int, int, int], ...] = (
    (CHECKING, "Acme Corp Payroll", INCOME_CATEGORY, 240_000, 14, 3),
    (CHECKING, "Rent", "Housing", -150_000, 30, 1),
    (CARD, "Corner Grocery", "Groceries", -6_450, 4, 0),
    (CARD, "Coffee Shop", "Dining", -475, 2, 1),
    (CARD, "Streaming Service", "Subscriptions", -1_599, 30, 5),
    (CARD, "Ride Share", "Transport", -1_825, 6, 2),
    (CHECKING, "Transfer to Savings", TRANSFER_CATEGORY, -50_000, 30, 10),
    (SAVINGS, "Transfer from Checking", TRANSFER_CATEGORY, 50_000, 30, 10),
)


class MockConnector:
    """Deterministic fake data for demos and tests. `today` is injected, never read."""

    def __init__(self, today: date, days: int = 90) -> None:
        self._today = today
        self._days = days

    def fetch(self) -> SyncData:
        accounts = (
            Account(CHECKING, "Mock Checking", AccountType.CHECKING),
            Account(SAVINGS, "Mock Savings", AccountType.SAVINGS),
            Account(CARD, "Mock Credit Card", AccountType.CREDIT),
        )
        balances = (
            Balance(CHECKING, self._today, 482_315),
            Balance(SAVINGS, self._today, 1_250_000),
            Balance(CARD, self._today, -83_240),
        )
        transactions: list[Transaction] = []
        for offset in range(self._days):
            day = self._today - timedelta(days=offset)
            for account, desc, category, cents, every, shift in _RECURRING:
                if (offset + shift) % every == 0:
                    tx_id = TransactionId(f"mock-{day.isoformat()}-{account}-{desc}")
                    transactions.append(Transaction(tx_id, account, day, cents, desc, category))
        return SyncData(accounts, balances, tuple(transactions))

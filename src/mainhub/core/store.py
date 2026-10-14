from collections.abc import Iterable
from datetime import date
from typing import Protocol

from mainhub.core.models import Account, Balance, Transaction


class FinanceStore(Protocol):
    """Storage edge. All writes are idempotent upserts keyed on stable IDs."""

    def upsert_accounts(self, accounts: Iterable[Account]) -> None: ...

    def upsert_balances(self, balances: Iterable[Balance]) -> None: ...

    def upsert_transactions(self, transactions: Iterable[Transaction]) -> None: ...

    def list_accounts(self) -> list[Account]: ...

    def latest_balances(self, as_of: date) -> list[Balance]:
        """Most recent balance per account on or before `as_of`."""
        ...

    def list_transactions(self, start: date, end: date) -> list[Transaction]:
        """Transactions with start <= posted_on <= end, oldest first."""
        ...

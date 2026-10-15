from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from mainhub.core.models import (
    Account,
    Balance,
    ExpenseItem,
    Receipt,
    Transaction,
    TransactionId,
)


class FinanceStore(Protocol):
    """Storage edge. All writes are idempotent upserts keyed on stable IDs."""

    def upsert_accounts(self, accounts: Iterable[Account]) -> None: ...

    def upsert_balances(self, balances: Iterable[Balance]) -> None: ...

    def upsert_transactions(self, transactions: Iterable[Transaction]) -> None: ...

    def delete_transactions(self, ids: Iterable[TransactionId]) -> None: ...

    def get_cursor(self, key: str) -> str | None:
        """Incremental-sync cursor for a source item, or None before the first sync."""
        ...

    def set_cursor(self, key: str, cursor: str) -> None: ...

    def list_accounts(self) -> list[Account]: ...

    def latest_balances(self, as_of: date) -> list[Balance]:
        """Most recent balance per account on or before `as_of`."""
        ...

    def list_transactions(self, start: date, end: date) -> list[Transaction]:
        """Transactions with start <= posted_on <= end, oldest first."""
        ...

    def add_receipt(self, receipt: Receipt, items: Sequence[ExpenseItem]) -> None:
        """Store a receipt and its line items atomically."""
        ...

    def get_receipt(self, receipt_id: str) -> Receipt | None: ...

    def list_receipts(self, unmatched_only: bool = False) -> list[Receipt]: ...

    def list_expense_items(self, receipt_id: str) -> list[ExpenseItem]: ...

    def link_receipt(self, receipt_id: str, transaction_id: TransactionId) -> None: ...

    def linked_transaction_ids(self) -> set[TransactionId]: ...

"""Receipt validation and matching to bank transactions. Pure logic over the store."""

from dataclasses import dataclass, replace
from datetime import date, timedelta

from mainhub.core.models import Receipt, TransactionId
from mainhub.core.money import format_cents
from mainhub.core.store import FinanceStore
from mainhub.errors import ReceiptMismatchError

_BANK_LAG_BEFORE_DAYS = 1
_BANK_LAG_AFTER_DAYS = 7
_UNDATED_LOOKBACK_DAYS = 14


@dataclass(frozen=True, slots=True)
class ExtractedItem:
    description: str
    amount_cents: int


@dataclass(frozen=True, slots=True)
class ExtractedReceipt:
    merchant: str
    purchased_on: date | None
    items: tuple[ExtractedItem, ...]
    tax_cents: int
    total_cents: int


def validate_receipt(receipt: ExtractedReceipt) -> None:
    """Items + tax must equal the total; otherwise ask the user instead of guessing."""
    if not receipt.items or receipt.total_cents <= 0:
        raise ReceiptMismatchError("the receipt has no items or a non-positive total")
    items_total = sum(i.amount_cents for i in receipt.items)
    if items_total + receipt.tax_cents != receipt.total_cents:
        raise ReceiptMismatchError(
            f"items {format_cents(items_total)} + tax {format_cents(receipt.tax_cents)} "
            f"!= total {format_cents(receipt.total_cents)}"
        )


def reconcile_receipt(store: FinanceStore, receipt: Receipt, today: date) -> Receipt:
    """Link the receipt to the one unclaimed bank transaction with the same amount, if any."""
    if receipt.transaction_id is not None:
        return receipt
    if receipt.purchased_on is None:
        start, end = today - timedelta(days=_UNDATED_LOOKBACK_DAYS), today
    else:
        start = receipt.purchased_on - timedelta(days=_BANK_LAG_BEFORE_DAYS)
        end = receipt.purchased_on + timedelta(days=_BANK_LAG_AFTER_DAYS)
    claimed = store.linked_transaction_ids()
    candidates = [
        t
        for t in store.list_transactions(start, end)
        if t.amount_cents == -receipt.total_cents and t.id not in claimed
    ]
    if len(candidates) != 1:  # none yet, or ambiguous: leave for later
        return receipt
    store.link_receipt(receipt.id, candidates[0].id)
    return replace(receipt, transaction_id=TransactionId(candidates[0].id))


def reconcile_all(store: FinanceStore, today: date) -> int:
    """Try to match every unmatched receipt; returns how many were newly matched."""
    matched = 0
    for receipt in store.list_receipts(unmatched_only=True):
        if reconcile_receipt(store, receipt, today).transaction_id is not None:
            matched += 1
    return matched

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import NewType

AccountId = NewType("AccountId", str)
TransactionId = NewType("TransactionId", str)

TRANSFER_CATEGORY = "Transfer"
INCOME_CATEGORY = "Income"
UNCATEGORIZED = "Uncategorized"


class AccountType(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT = "credit"
    INVESTMENT = "investment"
    LOAN = "loan"


@dataclass(frozen=True, slots=True)
class Account:
    id: AccountId
    name: str
    type: AccountType


@dataclass(frozen=True, slots=True)
class Balance:
    """Signed balance in cents: assets positive, liabilities (credit, loans) negative."""

    account_id: AccountId
    as_of: date
    amount_cents: int


@dataclass(frozen=True, slots=True)
class Transaction:
    """Amount in cents: negative = money out, positive = money in. `id` is stable per source."""

    id: TransactionId
    account_id: AccountId
    posted_on: date
    amount_cents: int
    description: str
    category: str | None = None

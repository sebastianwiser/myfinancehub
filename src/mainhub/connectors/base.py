from dataclasses import dataclass
from typing import Protocol

from mainhub.core.models import Account, Balance, Transaction


@dataclass(frozen=True, slots=True)
class SyncData:
    """Normalized output of a connector: USD cents, negative = money out."""

    accounts: tuple[Account, ...]
    balances: tuple[Balance, ...]
    transactions: tuple[Transaction, ...]


class Connector(Protocol):
    def fetch(self) -> SyncData: ...

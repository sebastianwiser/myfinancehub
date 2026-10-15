from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from mainhub.core.models import Account, Balance, Transaction, TransactionId


@dataclass(frozen=True, slots=True)
class SyncData:
    """Normalized output of a connector: USD cents, negative = money out."""

    accounts: tuple[Account, ...]
    balances: tuple[Balance, ...]
    transactions: tuple[Transaction, ...]
    removed_transaction_ids: tuple[TransactionId, ...] = ()
    # New incremental-sync cursors by source key; persist only after the data is stored.
    cursors: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class Connector(Protocol):
    def fetch(self) -> SyncData: ...

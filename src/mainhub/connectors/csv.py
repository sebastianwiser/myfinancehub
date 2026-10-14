import csv
import hashlib
import re
from collections import Counter
from datetime import date
from pathlib import Path

from mainhub.connectors.base import SyncData
from mainhub.core.models import (
    Account,
    AccountId,
    AccountType,
    Balance,
    Transaction,
    TransactionId,
)
from mainhub.core.money import parse_cents
from mainhub.errors import ConnectorError, MoneyParseError


def _slug(name: str) -> AccountId:
    return AccountId("csv-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))


class CsvConnector:
    """Reads transactions (and optionally balances) from CSV files.

    transactions: date,description,amount,category,account   (amount in USD, negative = out)
    balances:     account,type,as_of,balance                 (liabilities as negative)
    """

    def __init__(self, transactions_path: Path, balances_path: Path | None = None) -> None:
        self._transactions_path = transactions_path
        self._balances_path = balances_path

    def fetch(self) -> SyncData:
        accounts: dict[AccountId, Account] = {}
        balances: list[Balance] = []
        transactions: list[Transaction] = []

        if self._balances_path is not None:
            for line, row in self._rows(
                self._balances_path, {"account", "type", "as_of", "balance"}
            ):
                try:
                    acct = Account(_slug(row["account"]), row["account"], AccountType(row["type"]))
                    accounts[acct.id] = acct
                    balances.append(
                        Balance(
                            acct.id, date.fromisoformat(row["as_of"]), parse_cents(row["balance"])
                        )
                    )
                except (ValueError, MoneyParseError) as exc:
                    raise ConnectorError(f"{self._balances_path.name} line {line}: {exc}") from exc

        seen: Counter[str] = Counter()
        wanted = {"date", "description", "amount", "category", "account"}
        for line, row in self._rows(self._transactions_path, wanted):
            try:
                posted = date.fromisoformat(row["date"])
                cents = parse_cents(row["amount"])
            except (ValueError, MoneyParseError) as exc:
                raise ConnectorError(f"{self._transactions_path.name} line {line}: {exc}") from exc
            account_id = _slug(row["account"])
            accounts.setdefault(
                account_id, Account(account_id, row["account"], AccountType.CHECKING)
            )
            # Stable ID: identical rows get an occurrence counter so re-syncs never duplicate.
            base = f"{account_id}|{posted}|{row['description']}|{cents}"
            seen[base] += 1
            digest = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()[:16]
            transactions.append(
                Transaction(
                    TransactionId(f"csv-{digest}"),
                    account_id,
                    posted,
                    cents,
                    row["description"],
                    row["category"] or None,
                )
            )
        return SyncData(tuple(accounts.values()), tuple(balances), tuple(transactions))

    @staticmethod
    def _rows(path: Path, required: set[str]) -> list[tuple[int, dict[str, str]]]:
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                missing = required - set(reader.fieldnames or [])
                if missing:
                    raise ConnectorError(f"{path.name}: missing columns {sorted(missing)}")
                return list(enumerate(reader, start=2))
        except OSError as exc:
            raise ConnectorError(f"cannot read {path.name}: {exc.strerror}") from exc

"""Strict importer for the hourly automation snapshot (docs/codex-automation.md).

The file is produced by an AI, so it is untrusted: it is validated completely before anything
is stored, and implausible-but-valid data produces warnings instead of silent acceptance.
Format: schema_version 1, amounts as USD decimal strings, negative = money out.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from mainhub.connectors.base import SyncData
from mainhub.core.categorize import DEFAULT_RULES, Rule, categorize
from mainhub.core.models import (
    Account,
    AccountId,
    AccountType,
    Balance,
    Transaction,
    TransactionId,
)
from mainhub.core.money import format_cents, parse_cents
from mainhub.core.store import FinanceStore
from mainhub.errors import ConnectorError, MoneyParseError

SCHEMA_VERSION = 1
CONVENTION = "negative_is_outflow"
BIG_TRANSACTION_CENTS = 5_000_000  # $50,000
BIG_BALANCE_JUMP_CENTS = 50_000  # $500, and also more than 50% of the old balance
STALE_AFTER = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class ImportResult:
    data: SyncData
    generated_at: datetime
    warnings: tuple[str, ...]


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(obj: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise ConnectorError(f"{where}: missing '{key}'")
    return obj[key]


def parse_snapshot(
    path: Path, now: datetime, rules: Sequence[Rule] = DEFAULT_RULES
) -> ImportResult:
    """Validate a snapshot file and normalize it. Raises ConnectorError on any problem."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"{path.name}: cannot read as JSON") from exc
    if not isinstance(doc, dict):
        raise ConnectorError(f"{path.name}: top level must be an object")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ConnectorError(f"{path.name}: schema_version must be {SCHEMA_VERSION}")
    if doc.get("amount_convention") != CONVENTION:
        raise ConnectorError(
            f"{path.name}: amount_convention must be '{CONVENTION}' "
            "(guards against a flipped sign convention)"
        )
    try:
        generated_at = datetime.fromisoformat(str(_require(doc, "generated_at", path.name)))
        if generated_at.tzinfo is None:
            raise ConnectorError(f"{path.name}: generated_at needs a timezone (e.g. 'Z')")
        accounts, balances = _parse_accounts(doc, path.name)
        transactions = _parse_transactions(doc, path.name, accounts, now.date(), rules)
    except (ValueError, TypeError, MoneyParseError) as exc:
        raise ConnectorError(f"{path.name}: {exc}") from exc

    warnings: list[str] = []
    if now - generated_at > STALE_AFTER:
        warnings.append(f"{path.name} was generated more than 6 hours ago")
    if not transactions:
        warnings.append(f"{path.name} contains no transactions")
    for t in transactions:
        if abs(t.amount_cents) >= BIG_TRANSACTION_CENTS:
            warnings.append(f"very large transaction ({format_cents(t.amount_cents)}): check it")
    data = SyncData(tuple(accounts.values()), tuple(balances), tuple(transactions))
    return ImportResult(data, generated_at, tuple(warnings))


def _parse_accounts(doc: Mapping[str, Any], name: str) -> tuple[dict[str, Account], list[Balance]]:
    accounts: dict[str, Account] = {}
    balances: list[Balance] = []
    for raw in _require(doc, "accounts", name):
        if str(raw.get("currency", "USD")) != "USD":
            raise ConnectorError(f"{name}: non-USD accounts are not supported (ADR 0001)")
        raw_id = str(_require(raw, "id", "account"))
        if raw_id in accounts:
            raise ConnectorError(f"{name}: duplicate account id")
        kind = AccountType(_require(raw, "type", "account"))
        account = Account(
            AccountId(f"import-{raw_id}"), str(_require(raw, "name", "account")), kind
        )
        accounts[raw_id] = account
        cents = parse_cents(str(_require(raw, "balance", "account")))
        if kind in (AccountType.CREDIT, AccountType.LOAN):
            cents = -abs(cents)  # amounts owed are stored as negative balances
        as_of = date.fromisoformat(str(_require(raw, "balance_as_of", "account")))
        balances.append(Balance(account.id, as_of, cents))
    if not accounts:
        raise ConnectorError(f"{name}: no accounts")
    return accounts, balances


def _parse_transactions(
    doc: Mapping[str, Any],
    name: str,
    accounts: Mapping[str, Account],
    today: date,
    rules: Sequence[Rule],
) -> list[Transaction]:
    out: list[Transaction] = []
    seen_ids: set[str] = set()
    occurrences: Counter[str] = Counter()
    for raw in _require(doc, "transactions", name):
        if raw.get("pending"):
            continue  # pending rows come back as posted ones later
        account = accounts.get(str(_require(raw, "account_id", "transaction")))
        if account is None:
            raise ConnectorError(f"{name}: transaction references an unknown account")
        posted = date.fromisoformat(str(_require(raw, "date", "transaction")))
        if posted > today + timedelta(days=1):
            raise ConnectorError(f"{name}: transaction dated in the future ({posted})")
        cents = parse_cents(str(_require(raw, "amount", "transaction")))
        explicit = raw.get("id")
        if explicit:
            if str(explicit) in seen_ids:
                raise ConnectorError(f"{name}: duplicate transaction id")
            seen_ids.add(str(explicit))
            tx_id = TransactionId(f"import-{explicit}")
        else:
            # No stable id: key on account+date+amount (+occurrence for genuine repeats), so a
            # reworded description in a later snapshot still merges instead of duplicating.
            key = f"{account.id}|{posted}|{cents}"
            occurrences[key] += 1
            digest = hashlib.sha256(key.encode()).hexdigest()[:16]
            tx_id = TransactionId(f"import-k{digest}-{occurrences[key]}")
        description = str(_require(raw, "description", "transaction"))[:200]
        category = str(raw["category"])[:60] if raw.get("category") else None
        out.append(
            Transaction(
                tx_id,
                account.id,
                posted,
                cents,
                description,
                category or categorize(description, rules),
            )
        )
    return out


def sanity_warnings(store: FinanceStore, result: ImportResult) -> list[str]:
    """Compare a valid snapshot against what is already stored; flag, don't reject."""
    warnings = list(result.warnings)
    latest = {b.account_id: b for b in store.latest_balances(date.max)}
    names = {a.id: a.name for a in result.data.accounts}
    for b in result.data.balances:
        old = latest.get(b.account_id)
        if old is None:
            continue
        jump = abs(b.amount_cents - old.amount_cents)
        if jump >= BIG_BALANCE_JUMP_CENTS and jump > abs(old.amount_cents) // 2:
            warnings.append(
                f"balance of '{names[b.account_id]}' jumped from {format_cents(old.amount_cents)} "
                f"to {format_cents(b.amount_cents)}"
            )
    present = {a.id for a in result.data.accounts}
    for account in store.list_accounts():
        if account.id.startswith("import-") and account.id not in present:
            warnings.append(f"account '{account.name}' is missing from this snapshot")
    return warnings


def apply_snapshot(store: FinanceStore, result: ImportResult) -> list[str]:
    """Store a validated snapshot; returns the warnings (computed before writing)."""
    warnings = sanity_warnings(store, result)
    store.upsert_accounts(result.data.accounts)
    store.upsert_balances(result.data.balances)
    store.upsert_transactions(result.data.transactions)
    return warnings


def import_dir(
    store: FinanceStore, directory: Path, now: datetime, rules: Sequence[Rule] = DEFAULT_RULES
) -> list[str]:
    """Import every new *.json snapshot in `directory`, oldest first. Returns report lines.

    Each file's content hash is remembered, so a file is processed once even if it stays in
    the synced folder; rejected files are remembered too, so errors are not repeated hourly.
    """
    if not directory.is_dir():
        raise ConnectorError(f"import folder not found: {directory}")
    report: list[str] = []
    parsed: list[tuple[datetime, Path, str, ImportResult]] = []
    for path in sorted(directory.glob("*.json")):
        digest = file_digest(path)
        key = f"import:{digest}"
        if store.get_cursor(key) is not None:
            continue
        try:
            result = parse_snapshot(path, now, rules)
        except ConnectorError as exc:
            store.set_cursor(key, "rejected")
            report.append(f"rejected: {exc}")
            continue
        parsed.append((result.generated_at, path, key, result))
    for _, path, key, result in sorted(parsed, key=lambda p: p[0]):
        report.extend(f"warning: {w}" for w in apply_snapshot(store, result))
        store.set_cursor(key, "imported")
        report.append(
            f"imported {path.name}: {len(result.data.accounts)} accounts, "
            f"{len(result.data.transactions)} transactions"
        )
    return report


def utc_now() -> datetime:
    return datetime.now(UTC)

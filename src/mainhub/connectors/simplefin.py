"""SimpleFIN Bridge connector (https://www.simplefin.org/protocol.html).

The user claims a one-time setup token once; we keep only the resulting Access URL, which
contains credentials and is therefore never logged, printed, or put in an error message.
Normalized to USD cents, negative = money out. SimpleFIN has no categories, investments
holdings, or loan details, so categories come from the local rules.
"""

import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

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
from mainhub.core.money import parse_cents
from mainhub.errors import ConnectorError, MainHubError

LAST_SYNC_KEY = "simplefin:last_sync"
MAX_WINDOW_DAYS = 90  # the protocol limits each request to 90 days
OVERLAP_DAYS = 14  # re-read recent days so late-posting transactions are not missed
MAX_HISTORY_DAYS = 730


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _day(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, UTC).date()


def claim_access_url(setup_token: str, transport: httpx.BaseTransport | None = None) -> str:
    """Exchange a one-time setup token for the permanent Access URL."""
    try:
        padded = setup_token.strip() + "=" * (-len(setup_token.strip()) % 4)
        claim_url = base64.b64decode(padded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ConnectorError("that is not a valid SimpleFIN setup token") from None
    if not claim_url.startswith("https://"):
        raise ConnectorError("that is not a valid SimpleFIN setup token")
    try:
        response = httpx.Client(timeout=30, transport=transport).post(claim_url)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"SimpleFIN claim failed: {type(exc).__name__}") from exc
    if response.status_code == 403:
        raise ConnectorError(
            "SimpleFIN rejected the token: it is unknown or was already claimed. "
            "If you did not claim it, treat your SimpleFIN account as compromised."
        )
    if response.status_code != 200 or not response.text.startswith("https://"):
        raise ConnectorError(f"SimpleFIN claim returned HTTP {response.status_code}")
    return response.text.strip()


class SimpleFinClient:
    def __init__(self, access_url: str, transport: httpx.BaseTransport | None = None) -> None:
        parts = urlsplit(access_url)
        if parts.scheme != "https" or not parts.hostname or parts.username is None:
            raise ConnectorError("SIMPLEFIN_ACCESS_URL is not a valid access URL")
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        self._url = f"https://{host}{parts.path.rstrip('/')}/accounts"
        auth = (unquote(parts.username), unquote(parts.password or ""))
        self._http = httpx.Client(timeout=60, auth=auth, transport=transport)

    def accounts(self, start: date, end: date) -> Mapping[str, Any]:
        """Accounts with transactions posted in [start, end) (the protocol's semantics)."""
        params = {"start-date": str(_epoch(start)), "end-date": str(_epoch(end))}
        try:
            response = self._http.get(self._url, params=params)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"SimpleFIN request failed: {type(exc).__name__}") from exc
        if response.status_code == 402:
            raise ConnectorError("SimpleFIN says payment is required (402); check your account")
        if response.status_code == 403:
            raise ConnectorError("SimpleFIN access was revoked or is invalid (403)")
        if response.status_code != 200:
            raise ConnectorError(f"SimpleFIN returned HTTP {response.status_code}")
        try:
            body: Mapping[str, Any] = response.json()
        except ValueError as exc:
            raise ConnectorError("SimpleFIN returned invalid JSON") from exc
        return body


def infer_account_type(name: str) -> AccountType:
    """SimpleFIN has no account types, so guess from the name (editable later via the DB)."""
    n = name.lower()
    if any(w in n for w in ("credit", "card", "visa", "mastercard", "amex")):
        return AccountType.CREDIT
    if any(w in n for w in ("loan", "mortgage", "heloc", "line of credit")):
        return AccountType.LOAN
    if any(w in n for w in ("401", "ira", "brokerage", "invest", "hsa", "roth")):
        return AccountType.INVESTMENT
    if "saving" in n:
        return AccountType.SAVINGS
    return AccountType.CHECKING


def _windows(start: date, end: date) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=MAX_WINDOW_DAYS), end)
        out.append((cursor, nxt))
        cursor = nxt
    return out


class SimpleFinConnector:
    def __init__(
        self,
        client: SimpleFinClient,
        get_cursor: Callable[[str], str | None],
        today: date,
        history_days: int = 90,
        rules: Sequence[Rule] = DEFAULT_RULES,
    ) -> None:
        self._client = client
        self._get_cursor = get_cursor
        self._today = today
        self._history_days = min(max(history_days, 1), MAX_HISTORY_DAYS)
        self._rules = rules

    def _start(self) -> date:
        last = self._get_cursor(LAST_SYNC_KEY)
        if last:
            return max(date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS), date.min)
        return self._today - timedelta(days=self._history_days)

    def fetch(self) -> SyncData:
        accounts: dict[AccountId, Account] = {}
        balances: dict[AccountId, Balance] = {}
        transactions: dict[TransactionId, Transaction] = {}
        warnings: list[str] = []
        end = self._today + timedelta(days=1)  # end-date is exclusive; include today
        try:
            for w_start, w_end in _windows(self._start(), end):
                body = self._client.accounts(w_start, w_end)
                warnings.extend(self._messages(body))
                for raw in body["accounts"]:
                    self._add_account(raw, accounts, balances, transactions)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, MainHubError):
                raise
            raise ConnectorError(f"unexpected SimpleFIN response: {type(exc).__name__}") from exc
        return SyncData(
            tuple(accounts.values()),
            tuple(balances.values()),
            tuple(transactions.values()),
            cursors={LAST_SYNC_KEY: self._today.isoformat()},
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _messages(body: Mapping[str, Any]) -> list[str]:
        errlist = body.get("errlist")
        if errlist:
            return [f"{e.get('code', 'error')}: {e.get('msg', '')}"[:200] for e in errlist]
        return [str(m)[:200] for m in body.get("errors") or []]

    def _add_account(
        self,
        raw: Mapping[str, Any],
        accounts: dict[AccountId, Account],
        balances: dict[AccountId, Balance],
        transactions: dict[TransactionId, Transaction],
    ) -> None:
        if str(raw.get("currency", "USD")) != "USD":
            raise ConnectorError("non-USD accounts are not supported (ADR 0001)")
        account_id = AccountId(f"simplefin-{raw['conn_id']}-{raw['id']}")
        account_type = infer_account_type(raw["name"])
        accounts[account_id] = Account(account_id, raw["name"], account_type)

        cents = parse_cents(str(raw["balance"]))
        if account_type in (AccountType.CREDIT, AccountType.LOAN):
            cents = -abs(cents)  # amounts owed are stored as negative balances
        stamp = int(raw.get("balance-date") or 0)
        as_of = _day(stamp) if stamp > 0 else self._today
        balances[account_id] = Balance(account_id, as_of, cents)

        for tx in raw.get("transactions") or []:
            if tx.get("pending") or int(tx["posted"]) == 0:
                continue  # pending rows reappear as posted ones; counting both would double up
            tx_id = TransactionId(f"{account_id}-{tx['id']}")
            description = str(tx["description"])
            transactions[tx_id] = Transaction(
                tx_id,
                account_id,
                _day(int(tx["posted"])),
                parse_cents(str(tx["amount"])),
                description,
                categorize(description, self._rules),
            )

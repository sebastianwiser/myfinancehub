"""Plaid connector: incremental /transactions/sync, normalized to USD cents (negative = out).

Talks to Plaid through the thin `PlaidClient` protocol so it can be tested against recorded
responses; `HttpPlaidClient` is the real implementation.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, Protocol

import httpx

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
from mainhub.core.money import parse_cents
from mainhub.errors import ConnectorError, MainHubError

_BASE_URLS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
_MAX_PAGES = 100


class PlaidClient(Protocol):
    def accounts_get(self, access_token: str) -> Mapping[str, Any]: ...

    def transactions_sync(self, access_token: str, cursor: str | None) -> Mapping[str, Any]: ...


class HttpPlaidClient:
    def __init__(
        self,
        client_id: str,
        secret: str,
        env: str = "sandbox",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if env not in _BASE_URLS:
            raise ConnectorError("PLAID_ENV must be 'sandbox' or 'production'")
        self._auth = {"client_id": client_id, "secret": secret}
        self._http = httpx.Client(base_url=_BASE_URLS[env], timeout=30, transport=transport)

    def accounts_get(self, access_token: str) -> Mapping[str, Any]:
        return self._post("/accounts/get", {"access_token": access_token})

    def transactions_sync(self, access_token: str, cursor: str | None) -> Mapping[str, Any]:
        body: dict[str, Any] = {"access_token": access_token, "count": 500}
        if cursor is not None:
            body["cursor"] = cursor
        return self._post("/transactions/sync", body)

    def create_sandbox_access_token(self, institution_id: str = "ins_109508") -> str:
        """Mint a sandbox access token without the browser Link flow (sandbox only)."""
        created = self._post(
            "/sandbox/public_token/create",
            {"institution_id": institution_id, "initial_products": ["transactions"]},
        )
        exchanged = self._post(
            "/item/public_token/exchange", {"public_token": created["public_token"]}
        )
        return str(exchanged["access_token"])

    def create_link_token(self, redirect_uri: str | None = None) -> str:
        """Create a short-lived token that lets the browser open Plaid Link."""
        body: dict[str, Any] = {
            "client_name": "MainHub",
            "language": "en",
            "country_codes": ["US"],
            "user": {"client_user_id": "mainhub-owner"},
            "products": ["transactions"],
        }
        if redirect_uri:
            body["redirect_uri"] = redirect_uri
        return str(self._post("/link/token/create", body)["link_token"])

    def exchange_public_token(self, public_token: str) -> str:
        """Swap the one-time public token from Link for a permanent access token."""
        result = self._post("/item/public_token/exchange", {"public_token": public_token})
        return str(result["access_token"])

    def _post(self, path: str, body: dict[str, Any]) -> Mapping[str, Any]:
        try:
            response = self._http.post(path, json={**self._auth, **body})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Plaid request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            # Report only Plaid's error code; never echo request or response bodies.
            try:
                code = str(response.json().get("error_code", "UNKNOWN"))
            except ValueError:
                code = "UNKNOWN"
            raise ConnectorError(f"Plaid {path} returned HTTP {response.status_code} ({code})")
        result: Mapping[str, Any] = response.json()
        return result


def cursor_key(item_id: str) -> str:
    return f"plaid:{item_id}"


def _account_type(plaid_type: str) -> AccountType:
    return {
        "credit": AccountType.CREDIT,
        "loan": AccountType.LOAN,
        "investment": AccountType.INVESTMENT,
    }.get(plaid_type, AccountType.CHECKING)


def _category(pfc: Mapping[str, Any] | None) -> str | None:
    if not pfc:
        return None
    primary = str(pfc.get("primary", ""))
    detailed = str(pfc.get("detailed", ""))
    if primary in ("TRANSFER_IN", "TRANSFER_OUT") or "CREDIT_CARD_PAYMENT" in detailed:
        return TRANSFER_CATEGORY
    if primary == "INCOME":
        return INCOME_CATEGORY
    return primary.replace("_", " ").title() or None


def _cents(value: object) -> int:
    # Go through str so a JSON float like 4.33 never does binary float arithmetic on money.
    return parse_cents(str(value))


class PlaidConnector:
    def __init__(
        self,
        client: PlaidClient,
        access_tokens: Sequence[str],
        get_cursor: Callable[[str], str | None],
        today: date,
    ) -> None:
        self._client = client
        self._access_tokens = access_tokens
        self._get_cursor = get_cursor
        self._today = today

    def fetch(self) -> SyncData:
        accounts: list[Account] = []
        balances: list[Balance] = []
        transactions: dict[TransactionId, Transaction] = {}
        removed: set[TransactionId] = set()
        cursors: dict[str, str] = {}
        try:
            for token in self._access_tokens:
                self._fetch_item(token, accounts, balances, transactions, removed, cursors)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, MainHubError):
                raise
            raise ConnectorError(f"unexpected Plaid response shape: {type(exc).__name__}") from exc
        return SyncData(
            tuple(accounts),
            tuple(balances),
            tuple(transactions.values()),
            tuple(sorted(removed - set(transactions))),
            cursors,
        )

    def _fetch_item(
        self,
        token: str,
        accounts: list[Account],
        balances: list[Balance],
        transactions: dict[TransactionId, Transaction],
        removed: set[TransactionId],
        cursors: dict[str, str],
    ) -> None:
        info = self._client.accounts_get(token)
        for raw in info["accounts"]:
            if raw["balances"].get("iso_currency_code", "USD") != "USD":
                raise ConnectorError("non-USD accounts are not supported (ADR 0001)")
            account_type = _account_type(raw["type"])
            account_id = AccountId(f"plaid-{raw['account_id']}")
            accounts.append(Account(account_id, raw["name"], account_type))
            current = _cents(raw["balances"]["current"])
            # Plaid reports amounts owed as positive; we store liabilities as negative.
            signed = -current if account_type in (AccountType.CREDIT, AccountType.LOAN) else current
            balances.append(Balance(account_id, self._today, signed))

        key = cursor_key(info["item"]["item_id"])
        cursor = self._get_cursor(key)
        for _ in range(_MAX_PAGES):
            page = self._client.transactions_sync(token, cursor)
            for raw in [*page["added"], *page["modified"]]:
                # Pending rows are replaced by a posted row later; counting both would double up.
                if raw["pending"]:
                    continue
                if raw.get("iso_currency_code", "USD") != "USD":
                    raise ConnectorError("non-USD transactions are not supported (ADR 0001)")
                tx_id = TransactionId(f"plaid-{raw['transaction_id']}")
                transactions[tx_id] = Transaction(
                    tx_id,
                    AccountId(f"plaid-{raw['account_id']}"),
                    date.fromisoformat(raw["date"]),
                    -_cents(raw["amount"]),  # Plaid: positive = outflow
                    raw.get("merchant_name") or raw["name"],
                    _category(raw.get("personal_finance_category")),
                )
            removed.update(TransactionId(f"plaid-{r['transaction_id']}") for r in page["removed"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                cursors[key] = cursor
                return
        raise ConnectorError("Plaid sync did not finish within the page limit")

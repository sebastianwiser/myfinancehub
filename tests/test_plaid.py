import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import TODAY

from mainhub.connectors.plaid import HttpPlaidClient, PlaidConnector, cursor_key
from mainhub.core.models import AccountId, AccountType, TransactionId
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth
from mainhub.errors import ConnectorError

FIXTURES = Path(__file__).parent / "fixtures" / "plaid"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


class RecordedClient:
    """Replays recorded-shape responses; sync pages are returned in order."""

    def __init__(self, *sync_pages: str) -> None:
        self._pages = [_load(p) for p in sync_pages]
        self.cursors_seen: list[str | None] = []

    def accounts_get(self, access_token: str) -> Mapping[str, Any]:
        return _load("accounts_get")

    def transactions_sync(self, access_token: str, cursor: str | None) -> Mapping[str, Any]:
        self.cursors_seen.append(cursor)
        return self._pages.pop(0)


def _connector(client: RecordedClient, store: SqliteStore) -> PlaidConnector:
    return PlaidConnector(client, ["tok"], store.get_cursor, TODAY)


def _apply(store: SqliteStore, connector: PlaidConnector) -> None:
    data = connector.fetch()
    store.upsert_accounts(data.accounts)
    store.upsert_balances(data.balances)
    store.upsert_transactions(data.transactions)
    store.delete_transactions(data.removed_transaction_ids)
    for key, cursor in data.cursors.items():
        store.set_cursor(key, cursor)


def test_normalizes_accounts_and_balances(store: SqliteStore) -> None:
    data = _connector(RecordedClient("sync_page1", "sync_page2"), store).fetch()
    by_id = {a.id: a for a in data.accounts}
    assert by_id[AccountId("plaid-acc_cc")].type is AccountType.CREDIT
    assert {b.account_id: b.amount_cents for b in data.balances} == {
        AccountId("plaid-acc_chk"): 110_050,
        AccountId("plaid-acc_sav"): 500_000,
        AccountId("plaid-acc_cc"): -41_030,  # owed -> negative
    }


def test_normalizes_transactions(store: SqliteStore) -> None:
    data = _connector(RecordedClient("sync_page1", "sync_page2"), store).fetch()
    txs = {t.id: t for t in data.transactions}
    assert txs[TransactionId("plaid-tx_1")].amount_cents == -433  # outflow flipped negative
    assert txs[TransactionId("plaid-tx_1")].category == "Food And Drink"
    assert txs[TransactionId("plaid-tx_2")].amount_cents == 240_000
    assert txs[TransactionId("plaid-tx_2")].category == "Income"
    assert txs[TransactionId("plaid-tx_3")].category == "Transfer"  # card payment
    assert txs[TransactionId("plaid-tx_5")].category is None
    assert TransactionId("plaid-tx_4") not in txs  # pending is skipped


def test_follows_pagination_and_returns_final_cursor(store: SqliteStore) -> None:
    client = RecordedClient("sync_page1", "sync_page2")
    data = _connector(client, store).fetch()
    assert client.cursors_seen == [None, "cursor_page1"]
    assert data.cursors == {cursor_key("item_1"): "cursor_page2"}


def test_incremental_sync_applies_modified_and_removed(store: SqliteStore) -> None:
    _apply(store, _connector(RecordedClient("sync_page1", "sync_page2"), store))
    assert store.get_cursor(cursor_key("item_1")) == "cursor_page2"

    client = RecordedClient("sync_incremental")
    _apply(store, _connector(client, store))
    assert client.cursors_seen == ["cursor_page2"]  # resumed from the stored cursor
    assert store.get_cursor(cursor_key("item_1")) == "cursor_page3"

    ids = {t.id: t for t in store.list_transactions(date(2026, 3, 1), date(2026, 3, 31))}
    assert ids[TransactionId("plaid-tx_1")].amount_cents == -500  # modified
    assert TransactionId("plaid-tx_5") not in ids  # removed


def test_end_to_end_tools_over_plaid_data(store: SqliteStore) -> None:
    _apply(store, _connector(RecordedClient("sync_page1", "sync_page2"), store))
    assert net_worth(store, TODAY).total_cents == 110_050 + 500_000 - 41_030
    spending = get_spending(store, date(2026, 3, 1), date(2026, 3, 31))
    assert spending.total_cents == 433 + 1_200  # coffee + uncategorized; transfer/income excluded


def test_rejects_non_usd(store: SqliteStore) -> None:
    class EurClient(RecordedClient):
        def accounts_get(self, access_token: str) -> Mapping[str, Any]:
            data = _load("accounts_get")
            data["accounts"][0]["balances"]["iso_currency_code"] = "EUR"
            return data

    with pytest.raises(ConnectorError, match="non-USD"):
        _connector(EurClient("sync_page1"), store).fetch()


def test_malformed_response_raises_connector_error(store: SqliteStore) -> None:
    class BadClient(RecordedClient):
        def accounts_get(self, access_token: str) -> Mapping[str, Any]:
            return {"unexpected": True}

    with pytest.raises(ConnectorError, match="shape"):
        _connector(BadClient(), store).fetch()


def test_http_client_sends_credentials_and_maps_errors() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if request.url.path == "/accounts/get":
            return httpx.Response(200, json=_load("accounts_get"))
        return httpx.Response(400, json={"error_code": "ITEM_LOGIN_REQUIRED", "secret": "leak"})

    client = HttpPlaidClient("cid", "sec", transport=httpx.MockTransport(handler))
    assert client.accounts_get("tok")["item"]["item_id"] == "item_1"
    assert seen[0] == {"client_id": "cid", "secret": "sec", "access_token": "tok"}
    with pytest.raises(ConnectorError, match="ITEM_LOGIN_REQUIRED") as err:
        client.transactions_sync("tok", None)
    assert "leak" not in str(err.value)


def test_http_client_rejects_unknown_env() -> None:
    with pytest.raises(ConnectorError):
        HttpPlaidClient("a", "b", env="staging")

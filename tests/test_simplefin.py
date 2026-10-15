import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import TODAY
from typer.testing import CliRunner

from mainhub.cli.app import app
from mainhub.connectors.simplefin import (
    LAST_SYNC_KEY,
    SimpleFinClient,
    SimpleFinConnector,
    claim_access_url,
    infer_account_type,
)
from mainhub.core.categorize import categorize, load_rules
from mainhub.core.models import AccountId, AccountType, TransactionId
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth
from mainhub.errors import ConnectorError, MainHubError

FIXTURE = Path(__file__).parent / "fixtures" / "simplefin" / "accounts.json"
ACCESS_URL = "https://user:p%40ss@bridge.example/simplefin"


def _body() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def _client(
    handler: httpx.MockTransport | None = None, requests: list[httpx.Request] | None = None
) -> SimpleFinClient:
    def default(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json=_body())

    return SimpleFinClient(ACCESS_URL, transport=handler or httpx.MockTransport(default))


def test_claim_posts_to_decoded_url_and_returns_access_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=ACCESS_URL)

    token = base64.b64encode(b"https://bridge.example/simplefin/claim/abc").decode()
    assert claim_access_url(token, httpx.MockTransport(handler)) == ACCESS_URL
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://bridge.example/simplefin/claim/abc"


def test_claim_errors_do_not_leak() -> None:
    token = base64.b64encode(b"https://bridge.example/claim/abc").decode()
    forbidden = httpx.MockTransport(lambda r: httpx.Response(403))
    with pytest.raises(ConnectorError, match="already claimed"):
        claim_access_url(token, forbidden)
    with pytest.raises(ConnectorError, match="not a valid"):
        claim_access_url("%%%not-base64%%%")
    with pytest.raises(ConnectorError, match="not a valid"):
        claim_access_url(base64.b64encode(b"http://insecure.example/claim").decode())


def test_client_uses_basic_auth_and_epoch_dates() -> None:
    requests: list[httpx.Request] = []
    _client(requests=requests).accounts(date(2026, 3, 1), date(2026, 3, 15))
    req = requests[0]
    assert req.url.path == "/simplefin/accounts"
    assert req.url.params["start-date"] == "1772323200"
    assert req.url.params["end-date"] == "1773532800"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"user:p@ss").decode()


@pytest.mark.parametrize(("status", "text"), [(402, "payment"), (403, "revoked"), (500, "500")])
def test_client_http_errors(status: int, text: str) -> None:
    client = _client(httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(ConnectorError, match=text) as err:
        client.accounts(TODAY, TODAY)
    assert "bridge.example" not in str(err.value)
    assert "p@ss" not in str(err.value)


def test_client_rejects_bad_access_url() -> None:
    with pytest.raises(ConnectorError):
        SimpleFinClient("http://user:pw@insecure.example/simplefin")
    with pytest.raises(ConnectorError):
        SimpleFinClient("https://no-credentials.example/simplefin")


def test_normalizes_accounts_balances_and_transactions(store: SqliteStore) -> None:
    data = SimpleFinConnector(_client(), store.get_cursor, TODAY).fetch()
    types = {a.name: a.type for a in data.accounts}
    assert types == {
        "Everyday Checking": AccountType.CHECKING,
        "Rewards Credit Card": AccountType.CREDIT,
        "Auto Loan": AccountType.LOAN,
        "Roth IRA": AccountType.INVESTMENT,
    }
    balances = {b.account_id: b.amount_cents for b in data.balances}
    assert balances[AccountId("simplefin-CON1-A1")] == 123_456
    assert balances[AccountId("simplefin-CON1-A2")] == -41_025
    assert balances[AccountId("simplefin-CON1-A3")] == -820_000  # owed, stored negative
    assert balances[AccountId("simplefin-CON1-A4")] == 1_500_010
    txs = {t.id: t for t in data.transactions}
    assert TransactionId("simplefin-CON1-A1-T4") not in txs  # pending skipped
    coffee = txs[TransactionId("simplefin-CON1-A1-T1")]
    assert (coffee.amount_cents, coffee.category) == (-475, "Dining")
    assert txs[TransactionId("simplefin-CON1-A1-T2")].category == "Income"
    assert txs[TransactionId("simplefin-CON1-A1-T3")].category == "Transfer"
    assert data.cursors == {LAST_SYNC_KEY: TODAY.isoformat()}


def test_end_to_end_with_tools(store: SqliteStore) -> None:
    data = SimpleFinConnector(_client(), store.get_cursor, TODAY).fetch()
    store.upsert_accounts(data.accounts)
    store.upsert_balances(data.balances)
    store.upsert_transactions(data.transactions)
    assert net_worth(store, TODAY).total_cents == 123_456 - 41_025 - 820_000 + 1_500_010
    spending = get_spending(store, date(2026, 3, 1), date(2026, 3, 31))
    assert spending.total_cents == 475 + 6_450  # coffee + groceries; transfer/income excluded


def test_first_sync_chunks_history_in_90_day_windows(store: SqliteStore) -> None:
    requests: list[httpx.Request] = []
    SimpleFinConnector(
        _client(requests=requests), store.get_cursor, TODAY, history_days=200
    ).fetch()
    assert len(requests) == 3
    # windows are contiguous: each start equals the previous end
    bounds = [(r.url.params["start-date"], r.url.params["end-date"]) for r in requests]
    assert all(bounds[i][1] == bounds[i + 1][0] for i in range(2))


def test_second_sync_resumes_with_overlap_and_dedupes(store: SqliteStore) -> None:
    store.set_cursor(LAST_SYNC_KEY, "2026-03-10")
    requests: list[httpx.Request] = []
    data = SimpleFinConnector(_client(requests=requests), store.get_cursor, TODAY).fetch()
    assert len(requests) == 1
    expected_start = int(datetime(2026, 2, 24, tzinfo=UTC).timestamp())  # cursor - 14 days
    assert requests[0].url.params["start-date"] == str(expected_start)
    ids = [t.id for t in data.transactions]
    assert len(ids) == len(set(ids))


def test_warnings_non_usd_and_bad_shape(store: SqliteStore) -> None:
    warned = _body()
    warned["errlist"] = [{"code": "con.auth", "msg": "Re-login needed at Example Bank"}]
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=warned)))
    data = SimpleFinConnector(client, store.get_cursor, TODAY).fetch()
    assert data.warnings == ("con.auth: Re-login needed at Example Bank",)

    eur = _body()
    eur["accounts"][0]["currency"] = "EUR"
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=eur)))
    with pytest.raises(ConnectorError, match="non-USD"):
        SimpleFinConnector(client, store.get_cursor, TODAY).fetch()

    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1})))
    with pytest.raises(ConnectorError, match="unexpected"):
        SimpleFinConnector(client, store.get_cursor, TODAY).fetch()


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Premier Savings", AccountType.SAVINGS),
        ("Chase Sapphire Visa", AccountType.CREDIT),
        ("Home Mortgage", AccountType.LOAN),
        ("Fidelity 401(k)", AccountType.INVESTMENT),
        ("Total Checking", AccountType.CHECKING),
    ],
)
def test_infer_account_type(name: str, kind: AccountType) -> None:
    assert infer_account_type(name) is kind


def test_categorize_defaults_and_user_rules(tmp_path: Path) -> None:
    assert categorize("WHOLE FOODS #123") == "Groceries"
    assert categorize("mystery vendor") is None
    rules_file = tmp_path / "c.toml"
    rules_file.write_text('[[rule]]\nmatch = "Blue Bottle"\ncategory = "Coffee"\n')
    rules = load_rules(rules_file)
    assert categorize("BLUE BOTTLE COFFEE", rules) == "Coffee"  # user rule beats built-in
    assert load_rules(tmp_path / "missing.toml") == load_rules(None)
    rules_file.write_text("not [valid toml")
    with pytest.raises(MainHubError):
        load_rules(rules_file)


def test_cli_simplefin_requires_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIMPLEFIN_ACCESS_URL", raising=False)
    result = CliRunner().invoke(app, ["sync", "--source", "simplefin", "--db", str(tmp_path / "d")])
    assert result.exit_code == 1
    assert "simplefin-claim" in result.output

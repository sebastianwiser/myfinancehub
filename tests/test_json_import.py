import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mainhub.cli.app import app
from mainhub.connectors.json_import import (
    apply_snapshot,
    import_dir,
    parse_snapshot,
)
from mainhub.core.models import AccountId, AccountType, TransactionId
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth
from mainhub.errors import ConnectorError

NOW = datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
DEMO = Path(__file__).parent.parent / "demo" / "snapshot.json"


def _doc() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DEMO.read_text())
    return data


def _write(tmp_path: Path, doc: dict[str, Any], name: str = "s.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc))
    return path


def test_parses_and_normalizes_demo_snapshot(tmp_path: Path) -> None:
    result = parse_snapshot(_write(tmp_path, _doc()), NOW)
    types = {a.name: a.type for a in result.data.accounts}
    assert types == {"Demo Checking": AccountType.CHECKING, "Demo Card": AccountType.CREDIT}
    balances = {b.account_id: b.amount_cents for b in result.data.balances}
    assert balances[AccountId("import-acc1")] == 482_315
    assert balances[AccountId("import-acc2")] == -83_240  # owed -> negative
    assert {t.id for t in result.data.transactions} == {
        TransactionId("import-tx1"),
        TransactionId("import-tx2"),
    }
    assert result.warnings == ()


def test_end_to_end_tools(store: SqliteStore, tmp_path: Path) -> None:
    apply_snapshot(store, parse_snapshot(_write(tmp_path, _doc()), NOW))
    assert net_worth(store, date(2026, 3, 15)).total_cents == 482_315 - 83_240
    assert get_spending(store, date(2026, 3, 1), date(2026, 3, 31)).total_cents == 475 + 6_450


def _mutate(path: str, value: Any) -> dict[str, Any]:
    doc = _doc()
    target: Any = doc
    *parents, last = path.split(".")
    for p in parents:
        target = target[int(p)] if p.isdigit() else target[p]
    if value is KeyError:
        del target[last]
    else:
        target[int(last) if last.isdigit() else last] = value
    return doc


@pytest.mark.parametrize(
    "doc",
    [
        _mutate("schema_version", 2),
        _mutate("amount_convention", KeyError),
        _mutate("amount_convention", "positive_is_outflow"),
        _mutate("generated_at", "2026-03-15T14:00:00"),  # no timezone
        _mutate("accounts.0.type", "crypto"),
        _mutate("accounts.0.balance", "twelve"),
        _mutate("accounts.0.currency", "EUR"),
        _mutate("accounts", []),
        _mutate("transactions.0.account_id", "nope"),
        _mutate("transactions.0.date", "2027-01-01"),
        _mutate("transactions.0.amount", "1,2,3"),
        _mutate("transactions.1.id", "tx1"),  # duplicate id
    ],
)
def test_invalid_snapshots_are_rejected_whole(tmp_path: Path, doc: dict[str, Any]) -> None:
    with pytest.raises(ConnectorError):
        parse_snapshot(_write(tmp_path, doc), NOW)


def test_non_json_and_non_object_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    with pytest.raises(ConnectorError):
        parse_snapshot(bad, NOW)
    bad.write_text("[]")
    with pytest.raises(ConnectorError):
        parse_snapshot(bad, NOW)


def _idless() -> dict[str, Any]:
    doc = _doc()
    for t in doc["transactions"]:
        del t["id"]
    doc["transactions"].append(copy.deepcopy(doc["transactions"][0]))  # a genuine repeat
    return doc


def test_idless_transactions_merge_despite_reworded_description(
    store: SqliteStore, tmp_path: Path
) -> None:
    first = parse_snapshot(_write(tmp_path, _idless()), NOW)
    apply_snapshot(store, first)
    reworded = _idless()
    reworded["transactions"][0]["description"] = "COFFEE SHOP #12 SAN FRANCISCO"
    apply_snapshot(store, parse_snapshot(_write(tmp_path, reworded, "t.json"), NOW))
    txs = store.list_transactions(date(2026, 3, 1), date(2026, 3, 31))
    assert len(txs) == 3  # two identical coffees kept, nothing duplicated by the rewording
    assert len({t.id for t in txs}) == 3


def test_reimport_is_idempotent(store: SqliteStore, tmp_path: Path) -> None:
    result = parse_snapshot(_write(tmp_path, _doc()), NOW)
    apply_snapshot(store, result)
    apply_snapshot(store, result)
    assert len(store.list_transactions(date(2026, 3, 1), date(2026, 3, 31))) == 2


def test_pending_skipped(tmp_path: Path) -> None:
    doc = _doc()
    doc["transactions"][0]["pending"] = True
    result = parse_snapshot(_write(tmp_path, doc), NOW)
    assert [t.id for t in result.data.transactions] == [TransactionId("import-tx2")]


def test_warnings_stale_big_jump_and_missing_account(store: SqliteStore, tmp_path: Path) -> None:
    apply_snapshot(store, parse_snapshot(_write(tmp_path, _doc()), NOW))
    doc = _doc()
    doc["generated_at"] = "2026-03-15T01:00:00Z"
    doc["accounts"][0]["balance"] = "99999.00"
    del doc["accounts"][1]
    doc["transactions"] = [doc["transactions"][0]]
    doc["transactions"][0]["amount"] = "-60000.00"
    warnings = apply_snapshot(store, parse_snapshot(_write(tmp_path, doc, "w.json"), NOW))
    text = " | ".join(warnings)
    assert "more than 6 hours" in text
    assert "very large transaction" in text
    assert "jumped" in text
    assert "missing from this snapshot" in text


def test_import_dir_processes_each_file_once_and_remembers_rejects(
    store: SqliteStore, tmp_path: Path
) -> None:
    folder = tmp_path / "inbox"
    folder.mkdir()
    _write(folder, _doc(), "a.json")
    (folder / "broken.json").write_text("{}")
    first = import_dir(store, folder, NOW)
    assert any(line.startswith("imported a.json") for line in first)
    assert any(line.startswith("rejected") for line in first)
    assert import_dir(store, folder, NOW) == []  # nothing new, no repeated errors

    newer = _doc()
    newer["generated_at"] = "2026-03-15T14:30:00Z"
    _write(folder, newer, "b.json")
    assert any("imported b.json" in line for line in import_dir(store, folder, NOW))


def test_import_dir_missing_folder(store: SqliteStore, tmp_path: Path) -> None:
    with pytest.raises(ConnectorError):
        import_dir(store, tmp_path / "nope", NOW)


def test_cli_import_and_import_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    db = str(tmp_path / "hub.sqlite")
    snap = _write(tmp_path, _doc() | {"generated_at": datetime.now(UTC).isoformat()})
    result = runner.invoke(app, ["import", str(snap), "--db", db])
    assert result.exit_code == 0, result.output
    assert "imported 2 accounts, 2 transactions" in result.output

    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert runner.invoke(app, ["import", str(bad), "--db", db]).exit_code == 1

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox, _doc() | {"generated_at": datetime.now(UTC).isoformat()}, "x.json")
    out = runner.invoke(app, ["import-dir", str(inbox), "--db", db])
    assert out.exit_code == 0
    assert "imported x.json" in out.output

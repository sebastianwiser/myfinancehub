from pathlib import Path

import pytest
from conftest import TODAY

from mainhub.connectors.csv import CsvConnector
from mainhub.connectors.mock import MockConnector
from mainhub.core.models import AccountType
from mainhub.errors import ConnectorError

TX_HEADER = "date,description,amount,category,account\n"


def test_mock_is_deterministic_with_unique_ids() -> None:
    a = MockConnector(TODAY).fetch()
    b = MockConnector(TODAY).fetch()
    assert a == b
    ids = [t.id for t in a.transactions]
    assert len(ids) == len(set(ids))
    assert all(t.posted_on <= TODAY for t in a.transactions)


def test_csv_parses_transactions_and_balances(tmp_path: Path) -> None:
    tx = tmp_path / "t.csv"
    tx.write_text(TX_HEADER + "2026-03-01,Coffee,-4.50,Dining,My Checking\n")
    bal = tmp_path / "b.csv"
    bal.write_text('account,type,as_of,balance\nMy Checking,checking,2026-03-01,"$1,000.00"\n')
    data = CsvConnector(tx, bal).fetch()
    assert data.transactions[0].amount_cents == -450
    assert data.transactions[0].category == "Dining"
    assert data.balances[0].amount_cents == 100_000
    assert data.accounts[0].type is AccountType.CHECKING


def test_csv_duplicate_rows_get_distinct_stable_ids(tmp_path: Path) -> None:
    tx = tmp_path / "t.csv"
    row = "2026-03-01,Coffee,-4.50,Dining,Chk\n"
    tx.write_text(TX_HEADER + row + row)
    first = CsvConnector(tx).fetch().transactions
    second = CsvConnector(tx).fetch().transactions
    assert len({t.id for t in first}) == 2
    assert [t.id for t in first] == [t.id for t in second]


@pytest.mark.parametrize(
    "content",
    [
        "date,description\n2026-03-01,x\n",  # missing columns
        TX_HEADER + "not-a-date,x,-1,,Chk\n",
        TX_HEADER + "2026-03-01,x,abc,,Chk\n",
    ],
)
def test_csv_bad_input_raises_connector_error(tmp_path: Path, content: str) -> None:
    tx = tmp_path / "t.csv"
    tx.write_text(content)
    with pytest.raises(ConnectorError):
        CsvConnector(tx).fetch()


def test_csv_missing_file_raises_connector_error(tmp_path: Path) -> None:
    with pytest.raises(ConnectorError):
        CsvConnector(tmp_path / "nope.csv").fetch()

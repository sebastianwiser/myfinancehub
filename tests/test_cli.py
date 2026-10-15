from pathlib import Path

import pytest
from typer.testing import CliRunner

from mainhub.cli.app import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_mock_sync_then_networth_and_spending(tmp_path: Path) -> None:
    db = str(tmp_path / "hub.sqlite")
    sync = runner.invoke(app, ["sync", "--source", "mock", "--db", db])
    assert sync.exit_code == 0
    assert runner.invoke(app, ["sync", "--source", "mock", "--db", db]).exit_code == 0

    nw = runner.invoke(app, ["networth", "--db", db])
    assert nw.exit_code == 0
    assert "Net worth" in nw.stdout
    assert "$16,490.75" in nw.stdout  # 4,823.15 + 12,500.00 - 832.40

    sp = runner.invoke(app, ["spending", "--days", "30", "--db", db])
    assert sp.exit_code == 0
    assert "Groceries" in sp.stdout
    assert "Transfer" not in sp.stdout


def test_csv_sync_reports_bad_file(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "sync",
            "--source",
            "csv",
            "--transactions",
            str(tmp_path / "x.csv"),
            "--db",
            str(tmp_path / "d"),
        ],
    )
    assert result.exit_code == 1


def test_plaid_sync_requires_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(app, ["sync", "--source", "plaid", "--db", str(tmp_path / "d")])
    assert result.exit_code == 1
    assert "PLAID_CLIENT_ID" in result.output

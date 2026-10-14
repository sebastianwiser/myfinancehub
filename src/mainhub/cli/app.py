from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer

from mainhub.connectors.base import Connector
from mainhub.connectors.csv import CsvConnector
from mainhub.connectors.mock import MockConnector
from mainhub.core.money import format_cents
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth
from mainhub.errors import MainHubError

app = typer.Typer(help="MainHub: personal finance platform.", no_args_is_help=True)

DbOption = Annotated[
    Path,
    typer.Option("--db", envvar="MAINHUB_DB_PATH", help="SQLite database path."),
]


def _today() -> date:
    return datetime.now(UTC).date()


@app.callback()
def main() -> None:
    """MainHub: personal finance platform."""


@app.command()
def version() -> None:
    """Print the MainHub version."""
    typer.echo(pkg_version("mainhub"))


@app.command()
def sync(
    source: Annotated[str, typer.Option(help="mock or csv")] = "mock",
    transactions: Annotated[
        Path | None, typer.Option(help="Transactions CSV (csv source).")
    ] = None,
    balances: Annotated[Path | None, typer.Option(help="Balances CSV (csv source).")] = None,
    db: DbOption = Path("data/mainhub.sqlite"),
) -> None:
    """Pull data from a source into the local store (idempotent)."""
    connector: Connector
    if source == "mock":
        connector = MockConnector(today=_today())
    elif source == "csv":
        if transactions is None:
            raise typer.BadParameter("--transactions is required for the csv source")
        connector = CsvConnector(transactions, balances)
    else:
        raise typer.BadParameter("source must be 'mock' or 'csv'")
    store = SqliteStore(db)
    try:
        data = connector.fetch()
        store.upsert_accounts(data.accounts)
        store.upsert_balances(data.balances)
        store.upsert_transactions(data.transactions)
    except MainHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        store.close()
    typer.echo(
        f"synced {len(data.accounts)} accounts, {len(data.balances)} balances, "
        f"{len(data.transactions)} transactions"
    )


@app.command()
def networth(db: DbOption = Path("data/mainhub.sqlite")) -> None:
    """Show current net worth by account."""
    store = SqliteStore(db)
    try:
        result = net_worth(store, _today())
    finally:
        store.close()
    for row in result.by_account:
        typer.echo(f"{row.name:<28}{format_cents(row.balance_cents):>14}")
    typer.echo("-" * 42)
    typer.echo(f"{'Assets':<28}{format_cents(result.assets_cents):>14}")
    typer.echo(f"{'Liabilities':<28}{format_cents(result.liabilities_cents):>14}")
    typer.echo(f"{'Net worth':<28}{format_cents(result.total_cents):>14}")


@app.command()
def spending(
    days: Annotated[int, typer.Option(min=1, help="Look back this many days.")] = 30,
    db: DbOption = Path("data/mainhub.sqlite"),
) -> None:
    """Show spending by category over the last N days."""
    end = _today()
    store = SqliteStore(db)
    try:
        result = get_spending(store, end - timedelta(days=days - 1), end)
    finally:
        store.close()
    for row in result.by_category:
        typer.echo(f"{row.category:<28}{format_cents(row.spent_cents):>14}")
    typer.echo("-" * 42)
    typer.echo(f"{'Total':<28}{format_cents(result.total_cents):>14}")

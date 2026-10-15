import os
import secrets
import time
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer

from mainhub.agents.vision import DEFAULT_HOST, DEFAULT_MODEL, OllamaVisionProvider
from mainhub.channels.telegram import TelegramClient, run_bot
from mainhub.connectors.base import Connector
from mainhub.connectors.csv import CsvConnector
from mainhub.connectors.json_import import apply_snapshot, import_dir, parse_snapshot, utc_now
from mainhub.connectors.mock import MockConnector
from mainhub.core.categorize import load_rules
from mainhub.core.money import format_cents
from mainhub.core.receipts import reconcile_all
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth
from mainhub.errors import MainHubError
from mainhub.settings import env_file, load_env_file, set_var

app = typer.Typer(help="MainHub: personal finance platform.", no_args_is_help=True)

DbOption = Annotated[
    Path,
    typer.Option("--db", envvar="MAINHUB_DB_PATH", help="SQLite database path."),
]


def _rules() -> tuple[tuple[str, str], ...]:
    """Personal categorization rules (categories.toml), then the built-in ones."""
    return load_rules(Path(os.environ.get("MAINHUB_CATEGORY_RULES", "categories.toml")))


def _today() -> date:
    return datetime.now(UTC).date()


@app.command("import")
def import_file(
    path: Annotated[Path, typer.Argument(help="Snapshot JSON written by the automation.")],
    db: DbOption = Path("data/mainhub.sqlite"),
) -> None:
    """Validate and import one snapshot file (see docs/codex-automation.md)."""
    store = SqliteStore(db)
    try:
        result = parse_snapshot(path, utc_now(), _rules())
        warnings = apply_snapshot(store, result)
        newly_matched = reconcile_all(store, _today())
    except MainHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        store.close()
    for warning in warnings:
        typer.echo(f"warning: {warning}", err=True)
    typer.echo(
        f"imported {len(result.data.accounts)} accounts, "
        f"{len(result.data.transactions)} transactions"
    )
    if newly_matched:
        typer.echo(f"matched {newly_matched} receipt(s) to bank transactions")


@app.command("import-dir")
def import_directory(
    folder: Annotated[Path, typer.Argument(help="Folder the automation writes snapshots to.")],
    watch: Annotated[int, typer.Option(min=0, help="Re-check every N seconds (0 = once).")] = 0,
    db: DbOption = Path("data/mainhub.sqlite"),
) -> None:
    """Import all new snapshots in a (synced) folder; each file is processed once."""
    store = SqliteStore(db)
    try:
        while True:
            try:
                lines = import_dir(store, folder, utc_now(), _rules())
                if lines:
                    reconcile_all(store, _today())
            except MainHubError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(1) from exc
            for line in lines:
                typer.echo(line, err=line.startswith(("warning", "rejected")))
            if not watch:
                return
            time.sleep(watch)
    except KeyboardInterrupt:
        typer.echo("stopped")
    finally:
        store.close()


@app.callback()
def main() -> None:
    """MainHub: personal finance platform."""
    load_env_file(env_file())


@app.command()
def version() -> None:
    """Print the MainHub version."""
    typer.echo(pkg_version("mainhub"))


@app.command()
def sync(
    source: Annotated[
        str, typer.Option(help="mock or csv (bank data normally arrives via import-dir)")
    ] = "mock",
    transactions: Annotated[
        Path | None, typer.Option(help="Transactions CSV (csv source).")
    ] = None,
    balances: Annotated[Path | None, typer.Option(help="Balances CSV (csv source).")] = None,
    db: DbOption = Path("data/mainhub.sqlite"),
) -> None:
    """Pull data from a source into the local store (idempotent)."""
    store = SqliteStore(db)
    connector: Connector
    if source == "mock":
        connector = MockConnector(today=_today())
    elif source == "csv":
        if transactions is None:
            raise typer.BadParameter("--transactions is required for the csv source")
        connector = CsvConnector(transactions, balances)
    else:
        raise typer.BadParameter("source must be 'mock' or 'csv'")
    try:
        data = connector.fetch()
        store.upsert_accounts(data.accounts)
        store.upsert_balances(data.balances)
        store.upsert_transactions(data.transactions)
        store.delete_transactions(data.removed_transaction_ids)
        # Cursors advance only after the data is safely stored, so a failed sync is retried.
        for key, cursor in data.cursors.items():
            store.set_cursor(key, cursor)
        newly_matched = reconcile_all(store, _today())
    except MainHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        store.close()
    typer.echo(
        f"synced {len(data.accounts)} accounts, {len(data.balances)} balances, "
        f"{len(data.transactions)} transactions"
    )
    for warning in data.warnings:
        typer.echo(f"warning: {warning}", err=True)
    if newly_matched:
        typer.echo(f"matched {newly_matched} receipt(s) to bank transactions")


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


def _telegram_client() -> TelegramClient:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        typer.echo("error: set TELEGRAM_BOT_TOKEN in .env first", err=True)
        raise typer.Exit(1)
    return TelegramClient(token)


@app.command("telegram-pair")
def telegram_pair() -> None:
    """Find your chat ID: message the bot the shown code and it is saved to .env."""
    telegram = _telegram_client()
    code = secrets.token_hex(3)
    typer.echo(f"Open your bot in Telegram and send exactly:  /pair {code}")
    offset: int | None = None
    try:
        while True:
            for update in telegram.get_updates(offset, 30):
                offset = int(update["update_id"]) + 1
                message = update.get("message") or {}
                if message.get("text", "").strip() == f"/pair {code}":
                    chat_id = int(message["chat"]["id"])
                    set_var(env_file(), "TELEGRAM_ALLOWED_CHAT_ID", str(chat_id))
                    telegram.send_message(chat_id, "Paired. Now send me receipt photos.")
                    telegram.get_updates(offset, 0)  # acknowledge so the bot won't see it again
                    typer.echo(f"paired; saved TELEGRAM_ALLOWED_CHAT_ID to {env_file()}")
                    return
    except MainHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("telegram-bot")
def telegram_bot(
    db: DbOption = Path("data/mainhub.sqlite"),
    receipts_dir: Annotated[
        Path, typer.Option(envvar="MAINHUB_RECEIPTS_DIR", help="Where receipt photos are kept.")
    ] = Path("data/receipts"),
) -> None:
    """Run the receipt inbox: photos sent to your paired chat are read by a local model."""
    telegram = _telegram_client()
    chat = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "")
    if not chat.lstrip("-").isdigit():
        typer.echo("error: run `mainhub telegram-pair` first (TELEGRAM_ALLOWED_CHAT_ID)", err=True)
        raise typer.Exit(1)
    vision = OllamaVisionProvider(
        os.environ.get("OLLAMA_VISION_MODEL", DEFAULT_MODEL),
        os.environ.get("OLLAMA_HOST", DEFAULT_HOST),
    )
    store = SqliteStore(db)
    typer.echo("receipt inbox running; press Ctrl+C to stop")
    try:
        run_bot(
            store=store,
            telegram=telegram,
            vision=vision,
            allowed_chat_id=int(chat),
            receipts_dir=receipts_dir,
            today=_today,
        )
    except KeyboardInterrupt:
        typer.echo("stopped")
    finally:
        store.close()


@app.command()
def receipts(db: DbOption = Path("data/mainhub.sqlite")) -> None:
    """List stored receipts and whether they match a bank transaction."""
    store = SqliteStore(db)
    try:
        for r in store.list_receipts():
            status = "matched" if r.transaction_id else "unmatched"
            typer.echo(
                f"{r.purchased_on or '????-??-??'}  {r.merchant:<24}"
                f"{format_cents(r.total_cents):>12}  {status}"
            )
            for item in store.list_expense_items(r.id):
                typer.echo(f"    {item.description:<30}{format_cents(item.amount_cents):>10}")
    finally:
        store.close()

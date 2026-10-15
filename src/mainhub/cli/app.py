import os
import secrets
import webbrowser
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer

from mainhub.agents.vision import DEFAULT_HOST, DEFAULT_MODEL, OllamaVisionProvider
from mainhub.channels.telegram import TelegramClient, run_bot
from mainhub.connectors.base import Connector
from mainhub.connectors.csv import CsvConnector
from mainhub.connectors.mock import MockConnector
from mainhub.connectors.plaid import HttpPlaidClient, PlaidConnector
from mainhub.core.money import format_cents
from mainhub.core.receipts import reconcile_all
from mainhub.core.sqlite_store import SqliteStore
from mainhub.core.tools import get_spending, net_worth
from mainhub.errors import MainHubError
from mainhub.link_server import LinkServer, serve_until_linked
from mainhub.settings import append_to_list_var, env_file, load_env_file, set_var

app = typer.Typer(help="MainHub: personal finance platform.", no_args_is_help=True)

DbOption = Annotated[
    Path,
    typer.Option("--db", envvar="MAINHUB_DB_PATH", help="SQLite database path."),
]


def _today() -> date:
    return datetime.now(UTC).date()


def _plaid_connector(store: SqliteStore) -> PlaidConnector:
    """Build a Plaid connector from environment variables (never from CLI arguments)."""
    client_id = os.environ.get("PLAID_CLIENT_ID")
    secret = os.environ.get("PLAID_SECRET")
    tokens = [t.strip() for t in os.environ.get("PLAID_ACCESS_TOKENS", "").split(",") if t.strip()]
    if not (client_id and secret and tokens):
        store.close()
        typer.echo(
            "error: set PLAID_CLIENT_ID, PLAID_SECRET and PLAID_ACCESS_TOKENS (see .env.example)",
            err=True,
        )
        raise typer.Exit(1)
    try:
        client = HttpPlaidClient(client_id, secret, os.environ.get("PLAID_ENV", "sandbox"))
    except MainHubError as exc:
        store.close()
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    return PlaidConnector(client, tokens, store.get_cursor, _today())


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
    source: Annotated[str, typer.Option(help="mock, csv or plaid")] = "mock",
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
    elif source == "plaid":
        connector = _plaid_connector(store)
    else:
        raise typer.BadParameter("source must be 'mock', 'csv' or 'plaid'")
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
    if newly_matched:
        typer.echo(f"matched {newly_matched} receipt(s) to bank transactions")


@app.command()
def link() -> None:
    """Connect a real bank: opens Plaid Link in your browser and saves the access token to .env."""
    client_id = os.environ.get("PLAID_CLIENT_ID")
    secret = os.environ.get("PLAID_SECRET")
    if not (client_id and secret):
        typer.echo("error: set PLAID_CLIENT_ID and PLAID_SECRET in .env first", err=True)
        raise typer.Exit(1)
    try:
        client = HttpPlaidClient(client_id, secret, os.environ.get("PLAID_ENV", "sandbox"))
    except MainHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    server = LinkServer(
        client, lambda token: append_to_list_var(env_file(), "PLAID_ACCESS_TOKENS", token)
    )
    typer.echo(f"Opening {server.url}  (Ctrl+C to cancel)")
    webbrowser.open(server.url)
    try:
        serve_until_linked(server)
    except KeyboardInterrupt:
        typer.echo("cancelled")
        raise typer.Exit(1) from None
    finally:
        server.server_close()
    if server.error:
        typer.echo(f"error: {server.error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"connected; saved the access token to {env_file()}")


@app.command("link-sandbox")
def link_sandbox() -> None:
    """Create a Plaid sandbox login and save its access token to .env (token is not printed)."""
    client_id = os.environ.get("PLAID_CLIENT_ID")
    secret = os.environ.get("PLAID_SECRET")
    if not (client_id and secret):
        typer.echo("error: set PLAID_CLIENT_ID and PLAID_SECRET in .env first", err=True)
        raise typer.Exit(1)
    if os.environ.get("PLAID_ENV", "sandbox") != "sandbox":
        typer.echo("error: link-sandbox only works with PLAID_ENV=sandbox", err=True)
        raise typer.Exit(1)
    try:
        token = HttpPlaidClient(client_id, secret, "sandbox").create_sandbox_access_token()
    except MainHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    append_to_list_var(env_file(), "PLAID_ACCESS_TOKENS", token)
    typer.echo(f"saved a sandbox access token to {env_file()}")


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

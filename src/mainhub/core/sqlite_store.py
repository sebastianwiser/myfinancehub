import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

from mainhub.core.models import (
    Account,
    AccountId,
    AccountType,
    Balance,
    ExpenseItem,
    Receipt,
    Transaction,
    TransactionId,
)

# Numbered migrations; the index + 1 is stored in PRAGMA user_version.
_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE accounts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL
    );
    CREATE TABLE balances (
        account_id TEXT NOT NULL REFERENCES accounts(id),
        as_of TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        PRIMARY KEY (account_id, as_of)
    );
    CREATE TABLE transactions (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        posted_on TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        description TEXT NOT NULL,
        category TEXT
    );
    CREATE INDEX idx_transactions_posted_on ON transactions(posted_on);
    """,
    """
    CREATE TABLE sync_state (
        key TEXT PRIMARY KEY,
        cursor TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE receipts (
        id TEXT PRIMARY KEY,
        merchant TEXT NOT NULL,
        purchased_on TEXT,
        tax_cents INTEGER NOT NULL,
        total_cents INTEGER NOT NULL,
        image_path TEXT NOT NULL,
        transaction_id TEXT REFERENCES transactions(id) ON DELETE SET NULL
    );
    CREATE TABLE expense_items (
        receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        description TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        PRIMARY KEY (receipt_id, position)
    );
    """,
)


class SqliteStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(_MIGRATIONS[current:], start=current + 1):
            with self._conn:
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {int(version)}")

    def upsert_accounts(self, accounts: Iterable[Account]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO accounts (id, name, type) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, type = excluded.type",
                [(a.id, a.name, a.type.value) for a in accounts],
            )

    def upsert_balances(self, balances: Iterable[Balance]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO balances (account_id, as_of, amount_cents) VALUES (?, ?, ?) "
                "ON CONFLICT(account_id, as_of) DO UPDATE SET amount_cents = excluded.amount_cents",
                [(b.account_id, b.as_of.isoformat(), b.amount_cents) for b in balances],
            )

    def upsert_transactions(self, transactions: Iterable[Transaction]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO transactions "
                "(id, account_id, posted_on, amount_cents, description, category) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET account_id = excluded.account_id, "
                "posted_on = excluded.posted_on, amount_cents = excluded.amount_cents, "
                "description = excluded.description, category = excluded.category",
                [
                    (
                        t.id,
                        t.account_id,
                        t.posted_on.isoformat(),
                        t.amount_cents,
                        t.description,
                        t.category,
                    )
                    for t in transactions
                ],
            )

    def delete_transactions(self, ids: Iterable[TransactionId]) -> None:
        with self._conn:
            self._conn.executemany("DELETE FROM transactions WHERE id = ?", [(i,) for i in ids])

    def get_cursor(self, key: str) -> str | None:
        row = self._conn.execute("SELECT cursor FROM sync_state WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_cursor(self, key: str, cursor: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO sync_state (key, cursor) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET cursor = excluded.cursor",
                (key, cursor),
            )

    def list_accounts(self) -> list[Account]:
        rows = self._conn.execute("SELECT id, name, type FROM accounts ORDER BY name").fetchall()
        return [Account(AccountId(r[0]), r[1], AccountType(r[2])) for r in rows]

    def latest_balances(self, as_of: date) -> list[Balance]:
        rows = self._conn.execute(
            "SELECT b.account_id, b.as_of, b.amount_cents FROM balances b "
            "WHERE b.as_of = ("
            "  SELECT MAX(as_of) FROM balances WHERE account_id = b.account_id AND as_of <= ?"
            ") ORDER BY b.account_id",
            (as_of.isoformat(),),
        ).fetchall()
        return [Balance(AccountId(r[0]), date.fromisoformat(r[1]), r[2]) for r in rows]

    def list_transactions(self, start: date, end: date) -> list[Transaction]:
        rows = self._conn.execute(
            "SELECT id, account_id, posted_on, amount_cents, description, category "
            "FROM transactions WHERE posted_on BETWEEN ? AND ? ORDER BY posted_on, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [
            Transaction(
                TransactionId(r[0]), AccountId(r[1]), date.fromisoformat(r[2]), r[3], r[4], r[5]
            )
            for r in rows
        ]

    def add_receipt(self, receipt: Receipt, items: Sequence[ExpenseItem]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO receipts (id, merchant, purchased_on, tax_cents, total_cents, "
                "image_path, transaction_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.id,
                    receipt.merchant,
                    receipt.purchased_on.isoformat() if receipt.purchased_on else None,
                    receipt.tax_cents,
                    receipt.total_cents,
                    receipt.image_path,
                    receipt.transaction_id,
                ),
            )
            self._conn.executemany(
                "INSERT INTO expense_items (receipt_id, position, description, amount_cents) "
                "VALUES (?, ?, ?, ?)",
                [(i.receipt_id, i.position, i.description, i.amount_cents) for i in items],
            )

    def _receipts(self, unmatched_only: bool) -> list[Receipt]:
        query = (
            "SELECT id, merchant, purchased_on, tax_cents, total_cents, image_path, "
            "transaction_id FROM receipts"
        )
        if unmatched_only:
            query += " WHERE transaction_id IS NULL"
        rows = self._conn.execute(query + " ORDER BY purchased_on, id").fetchall()
        return [
            Receipt(
                r[0],
                r[1],
                date.fromisoformat(r[2]) if r[2] else None,
                r[3],
                r[4],
                r[5],
                TransactionId(r[6]) if r[6] else None,
            )
            for r in rows
        ]

    def get_receipt(self, receipt_id: str) -> Receipt | None:
        found = [r for r in self._receipts(False) if r.id == receipt_id]
        return found[0] if found else None

    def list_receipts(self, unmatched_only: bool = False) -> list[Receipt]:
        return self._receipts(unmatched_only)

    def list_expense_items(self, receipt_id: str) -> list[ExpenseItem]:
        rows = self._conn.execute(
            "SELECT receipt_id, position, description, amount_cents FROM expense_items "
            "WHERE receipt_id = ? ORDER BY position",
            (receipt_id,),
        ).fetchall()
        return [ExpenseItem(r[0], r[1], r[2], r[3]) for r in rows]

    def link_receipt(self, receipt_id: str, transaction_id: TransactionId) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE receipts SET transaction_id = ? WHERE id = ?", (transaction_id, receipt_id)
            )

    def linked_transaction_ids(self) -> set[TransactionId]:
        rows = self._conn.execute(
            "SELECT transaction_id FROM receipts WHERE transaction_id IS NOT NULL"
        ).fetchall()
        return {TransactionId(r[0]) for r in rows}

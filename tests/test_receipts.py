import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import TODAY

from mainhub.agents.expense_tracker import handle_receipt_image
from mainhub.agents.vision import OllamaVisionProvider, parse_extraction
from mainhub.channels.telegram import OFFSET_KEY, TelegramClient, run_bot
from mainhub.core.models import Account, AccountId, AccountType, Transaction, TransactionId
from mainhub.core.receipts import (
    ExtractedItem,
    ExtractedReceipt,
    reconcile_all,
    validate_receipt,
)
from mainhub.core.sqlite_store import SqliteStore
from mainhub.errors import ExtractionError, ReceiptMismatchError

GOOD = '{"merchant":"Corner Grocery","date":"2026-03-14","items":[{"description":"Milk","amount":"3.50"},{"description":"Bread","amount":"4.00"}],"tax":"0.60","total":"8.10"}'  # noqa: E501


def _receipt(total: int = 810, tax: int = 60) -> ExtractedReceipt:
    return ExtractedReceipt(
        "Corner Grocery",
        date(2026, 3, 14),
        (ExtractedItem("Milk", 350), ExtractedItem("Bread", 400)),
        tax,
        total,
    )


class FakeVision:
    def __init__(self, receipt: ExtractedReceipt) -> None:
        self.receipt = receipt
        self.calls = 0

    def extract(self, image: bytes, media_type: str) -> ExtractedReceipt:
        self.calls += 1
        return self.receipt


def _seed_tx(store: SqliteStore, cents: int, day: date, tx_id: str = "t1") -> None:
    store.upsert_accounts([Account(AccountId("a"), "Chk", AccountType.CHECKING)])
    store.upsert_transactions(
        [Transaction(TransactionId(tx_id), AccountId("a"), day, cents, "Corner Grocery", "Food")]
    )


def test_validate_receipt() -> None:
    validate_receipt(_receipt())
    with pytest.raises(ReceiptMismatchError):
        validate_receipt(_receipt(total=999))
    with pytest.raises(ReceiptMismatchError):
        validate_receipt(ExtractedReceipt("x", None, (), 0, 100))


def test_parse_extraction_ok_and_bad() -> None:
    r = parse_extraction("Sure! ```json\n" + GOOD + "\n```")
    assert (r.total_cents, r.tax_cents, len(r.items)) == (810, 60, 2)
    for bad in ["no json", '{"error": "blurry"}', '{"merchant": "x"}', '{"items": 5}']:
        with pytest.raises(ExtractionError):
            parse_extraction(bad)


def test_handle_image_saves_items_and_matches_transaction(
    store: SqliteStore, tmp_path: Path
) -> None:
    _seed_tx(store, -810, date(2026, 3, 15))
    reply = handle_receipt_image(
        store, FakeVision(_receipt()), b"img", "image/jpeg", tmp_path, TODAY
    )
    assert "matched to your bank transaction" in reply
    [receipt] = store.list_receipts()
    assert receipt.transaction_id == TransactionId("t1")
    assert [i.amount_cents for i in store.list_expense_items(receipt.id)] == [350, 400]
    assert Path(receipt.image_path).read_bytes() == b"img"


def test_unmatched_receipt_is_matched_by_later_reconcile(
    store: SqliteStore, tmp_path: Path
) -> None:
    reply = handle_receipt_image(
        store, FakeVision(_receipt()), b"img", "image/jpeg", tmp_path, TODAY
    )
    assert "No matching bank transaction yet" in reply
    _seed_tx(store, -810, date(2026, 3, 16))  # bank posts later
    assert reconcile_all(store, TODAY) == 1
    assert reconcile_all(store, TODAY) == 0


def test_transaction_is_claimed_by_only_one_receipt(store: SqliteStore, tmp_path: Path) -> None:
    _seed_tx(store, -810, date(2026, 3, 15))
    v = FakeVision(_receipt())
    handle_receipt_image(store, v, b"one", "image/jpeg", tmp_path, TODAY)
    reply = handle_receipt_image(store, v, b"two", "image/jpeg", tmp_path, TODAY)
    assert "No matching bank transaction yet" in reply


def test_mismatch_saves_nothing_and_duplicate_photo_skips_model(
    store: SqliteStore, tmp_path: Path
) -> None:
    bad = FakeVision(_receipt(total=999))
    reply = handle_receipt_image(store, bad, b"img", "image/jpeg", tmp_path, TODAY)
    assert "don't add up" in reply
    assert store.list_receipts() == []
    assert not list(tmp_path.iterdir())

    good = FakeVision(_receipt())
    handle_receipt_image(store, good, b"img", "image/jpeg", tmp_path, TODAY)
    assert handle_receipt_image(store, good, b"img", "image/jpeg", tmp_path, TODAY) == (
        "I already have this receipt."
    )
    assert good.calls == 1


def test_ollama_provider_request_and_errors() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": GOOD}})

    provider = OllamaVisionProvider("m", transport=httpx.MockTransport(handler))
    assert provider.extract(b"abc", "image/jpeg").total_cents == 810
    assert seen[0]["model"] == "m"
    assert seen[0]["messages"][0]["images"]

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ExtractionError, match="ollama serve"):
        OllamaVisionProvider("m", transport=httpx.MockTransport(down)).extract(b"x", "image/png")

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(ExtractionError, match="pulled"):
        OllamaVisionProvider("m", transport=httpx.MockTransport(missing)).extract(b"x", "image/png")


class FakeTelegram:
    def __init__(self, batches: list[list[Mapping[str, Any]]]) -> None:
        self.batches = batches
        self.sent: list[tuple[int, str]] = []
        self.offsets: list[int | None] = []

    def get_updates(self, offset: int | None, timeout: int) -> list[Mapping[str, Any]]:
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else []

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def download_photo(self, file_id: str) -> tuple[bytes, str]:
        return file_id.encode(), "image/jpeg"


def _photo(update_id: int, chat: int, file_id: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat}, "photo": [{"file_id": "small"}, {"file_id": file_id}]},
    }


def test_bot_serves_only_paired_chat_and_saves_offset(store: SqliteStore, tmp_path: Path) -> None:
    tg = FakeTelegram(
        [
            [
                _photo(10, 999, "stranger"),  # not the owner: ignored silently
                _photo(11, 1, "mine"),
                {"update_id": 12, "message": {"chat": {"id": 1}, "text": "hi"}},
            ],
            [],
        ]
    )
    run_bot(
        store=store,
        telegram=tg,
        vision=FakeVision(_receipt()),
        allowed_chat_id=1,
        receipts_dir=tmp_path,
        today=lambda: TODAY,
        max_polls=2,
    )
    assert [chat for chat, _ in tg.sent] == [1, 1]
    assert tg.sent[0][1].startswith("Saved 2 items")
    assert "photo of a receipt" in tg.sent[1][1]
    assert store.get_cursor(OFFSET_KEY) == "13"
    assert tg.offsets == [None, 13]


def test_bot_reports_extraction_failure_to_user(store: SqliteStore, tmp_path: Path) -> None:
    class Broken:
        def extract(self, image: bytes, media_type: str) -> ExtractedReceipt:
            raise ExtractionError("model offline")

    tg = FakeTelegram([[_photo(1, 1, "x")]])
    run_bot(
        store=store,
        telegram=tg,
        vision=Broken(),
        allowed_chat_id=1,
        receipts_dir=tmp_path,
        today=lambda: TODAY,
        max_polls=1,
    )
    assert "model offline" in tg.sent[0][1]


def test_telegram_client_hides_token_in_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient("SECRET:TOKEN", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception, match="getUpdates failed") as err:
        client.get_updates(None, 1)
    assert "SECRET" not in str(err.value)

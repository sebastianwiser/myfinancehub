"""Telegram channel over the Bot HTTP API (long polling). Only the paired chat is served."""

import time
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx

from mainhub.agents.expense_tracker import handle_receipt_image
from mainhub.agents.vision import VisionProvider
from mainhub.core.store import FinanceStore
from mainhub.errors import ConnectorError, MainHubError

OFFSET_KEY = "telegram:offset"
HELP_TEXT = "Send me a photo of a receipt and I'll record the items."


class TelegramApi(Protocol):
    def get_updates(self, offset: int | None, timeout: int) -> list[Mapping[str, Any]]: ...

    def send_message(self, chat_id: int, text: str) -> None: ...

    def download_photo(self, file_id: str) -> tuple[bytes, str]: ...


class TelegramClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        # The token is part of the URL, so errors below never include URLs or exception text.
        self._api = f"https://api.telegram.org/bot{token}"
        self._files = f"https://api.telegram.org/file/bot{token}"
        self._http = httpx.Client(timeout=60, transport=transport)

    def _call(self, method: str, payload: dict[str, Any], timeout: float = 60) -> Any:
        try:
            response = self._http.post(f"{self._api}/{method}", json=payload, timeout=timeout)
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorError(f"Telegram {method} failed: {type(exc).__name__}") from exc
        if response.status_code != 200 or not body.get("ok"):
            raise ConnectorError(f"Telegram {method} returned HTTP {response.status_code}")
        return body["result"]

    def get_updates(self, offset: int | None, timeout: int) -> list[Mapping[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result: list[Mapping[str, Any]] = self._call("getUpdates", payload, timeout + 10)
        return result

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def download_photo(self, file_id: str) -> tuple[bytes, str]:
        file_path = str(self._call("getFile", {"file_id": file_id})["file_path"])
        try:
            response = self._http.get(f"{self._files}/{file_path}")
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Telegram download failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ConnectorError(f"Telegram download returned HTTP {response.status_code}")
        media_type = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
        return response.content, media_type


def process_update(
    update: Mapping[str, Any],
    *,
    store: FinanceStore,
    telegram: TelegramApi,
    vision: VisionProvider,
    allowed_chat_id: int,
    receipts_dir: Path,
    today: Callable[[], date],
) -> None:
    message = update.get("message")
    if not message or message["chat"]["id"] != allowed_chat_id:
        return  # ignore everyone but the paired owner, without replying
    photos = message.get("photo")
    if not photos:
        telegram.send_message(allowed_chat_id, HELP_TEXT)
        return
    try:
        image, media_type = telegram.download_photo(photos[-1]["file_id"])
        reply = handle_receipt_image(store, vision, image, media_type, receipts_dir, today())
    except MainHubError as exc:
        reply = f"Sorry, I couldn't process that receipt: {exc}"
    telegram.send_message(allowed_chat_id, reply)


def run_bot(
    *,
    store: FinanceStore,
    telegram: TelegramApi,
    vision: VisionProvider,
    allowed_chat_id: int,
    receipts_dir: Path,
    today: Callable[[], date],
    max_polls: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll forever (or `max_polls` times). The offset is saved after each update is handled,
    so a restart neither replays handled receipts nor skips unhandled ones."""
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        saved = store.get_cursor(OFFSET_KEY)
        try:
            updates = telegram.get_updates(int(saved) if saved else None, 30)
        except ConnectorError:
            sleep(5)
            continue
        for update in updates:
            process_update(
                update,
                store=store,
                telegram=telegram,
                vision=vision,
                allowed_chat_id=allowed_chat_id,
                receipts_dir=receipts_dir,
                today=today,
            )
            store.set_cursor(OFFSET_KEY, str(int(update["update_id"]) + 1))

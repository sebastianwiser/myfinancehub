"""Expense tracker agent: receipt photo -> validated line items -> matched bank transaction.

Channel-agnostic: takes image bytes, returns the reply text. Telegram is just one caller.
"""

import hashlib
from datetime import date
from pathlib import Path

from mainhub.agents.vision import VisionProvider
from mainhub.core.models import ExpenseItem, Receipt
from mainhub.core.money import format_cents
from mainhub.core.receipts import reconcile_receipt, validate_receipt
from mainhub.core.store import FinanceStore
from mainhub.errors import ReceiptMismatchError

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def handle_receipt_image(
    store: FinanceStore,
    vision: VisionProvider,
    image: bytes,
    media_type: str,
    receipts_dir: Path,
    today: date,
) -> str:
    receipt_id = hashlib.sha256(image).hexdigest()[:16]
    if store.get_receipt(receipt_id) is not None:
        return "I already have this receipt."

    extracted = vision.extract(image, media_type)
    try:
        validate_receipt(extracted)
    except ReceiptMismatchError as exc:
        return (
            f"I read this receipt but the numbers don't add up ({exc}). "
            "I saved nothing; please send a clearer photo."
        )

    receipts_dir.mkdir(parents=True, exist_ok=True)
    path = receipts_dir / f"{receipt_id}{_EXTENSIONS.get(media_type, '.jpg')}"
    path.write_bytes(image)
    path.chmod(0o600)

    receipt = Receipt(
        receipt_id,
        extracted.merchant,
        extracted.purchased_on,
        extracted.tax_cents,
        extracted.total_cents,
        str(path),
    )
    items = [
        ExpenseItem(receipt_id, n, i.description, i.amount_cents)
        for n, i in enumerate(extracted.items)
    ]
    store.add_receipt(receipt, items)
    matched = reconcile_receipt(store, receipt, today)

    summary = (
        f"Saved {len(items)} items from {receipt.merchant}, {format_cents(receipt.total_cents)}"
    )
    if matched.transaction_id is not None:
        return f"{summary}, matched to your bank transaction."
    return f"{summary}. No matching bank transaction yet; I'll match it after your next sync."

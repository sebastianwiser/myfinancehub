"""Vision adapter: receipt photo -> validated ExtractedReceipt, using a local Ollama model.

Privacy: the image goes only to your own Ollama server (default localhost). Nothing else
(no transactions or balances) is ever included in the request.
"""

import base64
import json
from datetime import date
from typing import Any, Protocol

import httpx

from mainhub.core.money import parse_cents
from mainhub.core.receipts import ExtractedItem, ExtractedReceipt
from mainhub.errors import ExtractionError, MoneyParseError

DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_HOST = "http://localhost:11434"

_PROMPT = """Extract this shopping receipt as JSON only, no prose, in exactly this shape:
{"merchant": "name", "date": "YYYY-MM-DD or null",
 "items": [{"description": "text", "amount": "12.34"}],
 "tax": "0.00", "total": "12.34"}
Rules: amounts are USD decimal strings copied exactly from the receipt; discounts are negative
items; include every line item; tax is the sales-tax amount (0.00 if none); total is what was
charged. If the image is not a legible receipt, reply {"error": "short reason"}."""


class VisionProvider(Protocol):
    def extract(self, image: bytes, media_type: str) -> ExtractedReceipt: ...


def parse_extraction(text: str) -> ExtractedReceipt:
    """Turn the model's reply into a typed receipt; the reply is untrusted input."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ExtractionError("the model did not return JSON")
    try:
        data = json.loads(text[start : end + 1])
        if "error" in data:
            raise ExtractionError("could not read the receipt: " + str(data["error"])[:100])
        items = tuple(
            ExtractedItem(str(i["description"])[:200], parse_cents(str(i["amount"])))
            for i in data["items"]
        )
        raw_date = data.get("date")
        return ExtractedReceipt(
            merchant=str(data["merchant"])[:100],
            purchased_on=date.fromisoformat(raw_date) if raw_date else None,
            items=items,
            tax_cents=parse_cents(str(data.get("tax") or "0")),
            total_cents=parse_cents(str(data["total"])),
        )
    except (ValueError, KeyError, TypeError, MoneyParseError) as exc:
        if isinstance(exc, ExtractionError):
            raise
        raise ExtractionError("the model returned an unexpected receipt format") from exc


class OllamaVisionProvider:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        # Local models can be slow on the first call while they load into memory.
        self._http = httpx.Client(base_url=host, timeout=300, transport=transport)

    def extract(self, image: bytes, media_type: str) -> ExtractedReceipt:
        body: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "user",
                    "content": _PROMPT,
                    "images": [base64.b64encode(image).decode()],
                }
            ],
        }
        try:
            response = self._http.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise ExtractionError(
                f"cannot reach Ollama ({type(exc).__name__}); is `ollama serve` running?"
            ) from exc
        if response.status_code != 200:
            raise ExtractionError(
                f"Ollama returned HTTP {response.status_code}; is {self._model} pulled "
                "and vision-capable?"
            )
        try:
            text = str(response.json()["message"]["content"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ExtractionError("unexpected response from Ollama") from exc
        return parse_extraction(text)

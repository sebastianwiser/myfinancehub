# Hourly snapshot automation (ChatGPT / Codex)

An hourly automation that can see your Plaid-linked accounts writes one JSON file to a synced
folder. MainHub validates and imports it: `mainhub import-dir <folder> --watch 300`.

The file is produced by an AI, so MainHub treats it as untrusted: it is validated completely,
rejected whole if anything is wrong, and flagged if it looks implausible.

## Format (schema_version 1)

```json
{
  "schema_version": 1,
  "amount_convention": "negative_is_outflow",
  "generated_at": "2026-03-15T14:00:00Z",
  "accounts": [
    {"id": "<plaid account_id>", "name": "Everyday Checking", "type": "checking",
     "balance": "1234.56", "balance_as_of": "2026-03-15"}
  ],
  "transactions": [
    {"id": "<plaid transaction_id>", "account_id": "<plaid account_id>", "date": "2026-03-14",
     "amount": "-4.75", "description": "BLUE BOTTLE COFFEE", "category": "Dining",
     "pending": false}
  ]
}
```

- `type`: one of `checking`, `savings`, `credit`, `investment`, `loan`.
- `balance`: the current balance as the bank shows it; for `credit` and `loan` MainHub stores the
  amount owed as negative regardless of sign.
- `amount`: USD decimal string, **negative = money out (spent), positive = money in**. The
  `amount_convention` field must be present as shown; it guards against a flipped sign.
- `category` is optional; when missing, MainHub applies its rules (`categories.toml`, see
  `demo/categories.example.toml`).
- `id` on transactions: copy the Plaid `transaction_id` whenever available. Without it, MainHub
  merges on account + date + amount, which also works but cannot tell apart a changed amount.
- Skip nothing: include the last 14 days of transactions every run (overlap is safe), and every
  account in every run (a missing account raises a warning).

## Prompt to give the automation

> Every hour, read my linked accounts and write a file `snapshot-<UTC timestamp>.json` to
> `<synced folder>` in exactly the schema in this document. Copy numbers exactly as shown by the
> source; never estimate, round, or infer. Amounts: negative for money out, positive for money
> in. Include all accounts and the last 14 days of transactions with Plaid transaction IDs.
> If any data is unavailable, write nothing rather than a partial file.

## What MainHub checks

Rejected (whole file): wrong schema_version or missing `amount_convention`, invalid or non-USD
amounts, unknown account references, duplicate IDs, future dates, bad JSON.

Warned (still imported): file older than 6 hours, no transactions, a transaction of $50,000 or
more, a balance that jumped by over $500 and 50%, an account missing from the snapshot.

Each file is processed once (by content hash), so leaving old files in the folder is fine.

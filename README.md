# MainHub

**A self-hosted personal finance hub: net worth, spending, and itemized receipts, with a deterministic core and AI only at the edges.**

![python](https://img.shields.io/badge/python-3.12%2B-blue) ![types](https://img.shields.io/badge/mypy-strict-success) ![lint](https://img.shields.io/badge/lint-ruff-informational) ![license](https://img.shields.io/badge/license-MIT-green)

MainHub pulls account data from an hourly automation, tracks net worth and spending, and turns
receipt photos sent to a Telegram bot into itemized, bank-matched expenses. The receipt reader is
a local vision model, so receipt images never leave your machine. All financial logic lives in
tested, typed Python functions; AI is used to read photos and, later, to explain results, never to
compute them.

## Why it's built this way

| Principle | What it means in the code |
|---|---|
| **Deterministic core, AI at the edges** | Money math, merging, and matching are plain tested functions. Models only read images; their output is parsed and validated like any untrusted input. |
| **Untrusted input is validated whole** | An hourly snapshot written by an AI is rejected entirely on any schema or amount error, and flagged (not silently accepted) when it merely looks implausible. |
| **Exact money** | Integer cents, USD only ([ADR 0001](docs/decisions/0001-money-representation.md)). Strict amount parsing rejects malformed values such as `1,2,3`. |
| **Idempotent everywhere** | Every import and sync is an upsert on stable IDs, so re-running changes nothing. Transactions without IDs merge on account + date + amount. |
| **Capture is real-time, reconcile is eventual** | A receipt can arrive before the bank posts the charge; it is matched automatically on a later import. |
| **Pluggable edges** | Store, connectors, channels, and vision providers sit behind `Protocol` interfaces with in-memory fakes for tests. |
| **Public-repo safe** | No real data in the repo, secrets scanning in pre-commit and CI, `.env`, databases, and receipts gitignored. |

## Architecture

```
 Hourly automation ──▶ JSON snapshot ──┐
 (synced folder)                       │   validate whole   ┌───────────────┐
 CSV / mock ───────────────────────────┼──▶ + sanity checks ─▶│ FinanceStore  │──▶ Tool layer ──▶ CLI
                                       │                     │ (SQLite)      │    net_worth()
 Telegram photo ─▶ local Ollama vision ─┘                     └───────┬───────┘    get_spending()
        │              │ items + tax must equal total                 │
        └── reply ◀────┴──── receipts matched to bank transactions ◀──┘
```

## Features

- **Net worth and spending** from the CLI, by account and by category. Transfers and income are
  excluded from spending, and refunds net against their category.
- **Hourly snapshot import** with a strict, versioned JSON schema, an explicit sign-convention
  guard, per-file content hashing (each file processed once), and warnings for stale files,
  large transactions, balance jumps, and missing accounts. See
  [docs/codex-automation.md](docs/codex-automation.md).
- **Receipt inbox**: send a photo to a Telegram bot, a local vision model extracts the line items,
  the app checks that items plus tax equals the total (otherwise it asks for a clearer photo and
  stores nothing), and the receipt is linked to the one matching bank transaction.
- **Editable categorizer**: rule-based, with personal rules in `categories.toml` taking priority.
- **Owner-only bot**: paired by a one-time code, and messages from any other chat are ignored.
  The bot token never appears in logs or error messages.

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync

# try it with fake data
uv run mainhub sync --source mock --db data/demo.sqlite
uv run mainhub networth --db data/demo.sqlite
uv run mainhub spending --days 30 --db data/demo.sqlite

# import an automation snapshot (or a whole synced folder, re-checked every 5 minutes)
uv run mainhub import demo/snapshot.json
uv run mainhub import-dir ~/Sync/mainhub --watch 300
```

Receipt inbox (needs [Ollama](https://ollama.com) and a vision model):

```bash
ollama pull qwen2.5vl:7b
cp .env.example .env                  # add TELEGRAM_BOT_TOKEN from @BotFather
uv run mainhub telegram-pair          # send the printed /pair code to your bot
uv run mainhub telegram-bot           # then send it receipt photos
uv run mainhub receipts
```

The database defaults to `data/mainhub.sqlite` (gitignored); override with `--db` or `MAINHUB_DB_PATH`.

## Project layout

```
src/mainhub/
├── core/         models, FinanceStore protocol + SQLite store, money, receipts, tool layer
├── connectors/   json_import (hourly snapshots), csv, mock
├── agents/       vision adapter (Ollama), expense tracker
├── channels/     Telegram (long polling)
└── cli/          Typer commands
tests/            unit tests with fakes; no network, no real data
docs/             architecture notes and decision records
```

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

73 tests; `mypy --strict`; pre-commit runs ruff and gitleaks; CI runs the same checks.

## Roadmap

- [x] Store, mock and CSV sources, CLI, money handling
- [x] Validated hourly snapshot import with sanity checks
- [x] Telegram receipt inbox with local vision model and bank matching
- [ ] Live purchase alerts from bank emails, merged into the next snapshot
- [ ] Weekly savings review agent (deterministic facts first, then grounded suggestions)
- [ ] Web dashboard
- [ ] Trade journal and student-discount agents

## Status and limits

Early-stage personal project. The importer, store, and receipt logic are covered by tests using
fakes; the receipt reader depends on the accuracy of the local model you choose, and the
items-plus-tax check exists to catch its misreads. Not financial advice, and not a replacement
for your bank's own records.

## License

MIT

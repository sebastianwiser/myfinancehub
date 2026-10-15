# MainHub

An always-on personal finance platform: net worth and spending tracking, itemized purchase capture, and read-only agents that suggest optimizations. Deterministic core, LLM at the edges. See `docs/` for decisions.

**Status:** Phase 3. Bank data arrives as validated JSON snapshots from an hourly automation (`docs/codex-automation.md`); receipts via Telegram + a local Ollama vision model.

## Quickstart

```
uv sync
uv run mainhub sync --source mock          # or: --source csv --transactions demo/transactions.csv --balances demo/balances.csv
uv run mainhub import demo/snapshot.json   # one automation snapshot; or: import-dir <folder> --watch 300
uv run mainhub networth
uv run mainhub spending --days 30
```

The database defaults to `data/mainhub.sqlite` (gitignored); override with `--db` or `MAINHUB_DB_PATH`.

## Development

```
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Licensed under MIT.

# 0001: Money is integer cents, USD only

Status: accepted

## Decision
- All amounts are stored and computed as `int` cents (e.g. $12.34 -> `1234`).
- The project is USD-only; there is no currency column. Multi-currency would need a new ADR and a migration.
- Sign convention: negative = money out (spending), positive = money in. Connectors normalize to this (Plaid reports outflows as positive, so its connector flips the sign).
- Rounding happens only when parsing decimal strings or computing percentages, using round-half-even, in one helper.

## Why
Integers make equality, sums, and SQL aggregates exact and fast, and avoid float and Decimal-in-SQLite pitfalls. USD-only removes currency plumbing until it's actually needed.

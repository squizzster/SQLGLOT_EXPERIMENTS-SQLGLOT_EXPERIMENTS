# SQLGlot Experiments

An experimental Python workspace for learning and evaluating
[SQLGlot](https://github.com/tobymao/sqlglot) against representative SQL
parsing and transformation cases.

## Development mode

`EXP` — run a representative case, observe real behaviour, make the smallest
useful adaptation, and repeat.

## Current state

The Python 3.12 project baseline, SQLGlot, and python-dateutil dependencies are
installed and locked. The first experiment exercises SQLGlot's native parser,
lineage, and transpiler APIs directly against the verified SQLite source query.
The retained statement-fingerprinting experiment now compares independent
dialect-known and engine-unknown SHA-256 views of prepared `SELECT`, `INSERT`,
and `UPDATE` shapes. It tries the standard Python DB-API placeholder styles,
common PostgreSQL/SQLite spellings, qualified names, and external-value
exclusion without claiming a universal driver adapter.

## Design notes

- [AST source and target](docs/AST_SOURCE_TARGET.md)

## Run

```bash
uv sync
uv run python experiments/native_api_showcase.py
uv run python -m experiments.sql_statement_fingerprinting.showcase
uv run python -m unittest experiments.sql_statement_fingerprinting.test_fingerprint
```

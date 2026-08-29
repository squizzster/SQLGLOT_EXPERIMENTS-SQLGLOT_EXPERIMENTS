# SQLGlot Experiments

A Python prototype for turning SQL with field-associated hardcoded values into
a compact, parameterized execution package using
[SQLGlot](https://github.com/tobymao/sqlglot).

## Development mode

`PROTO` — strengthen the useful path with a small public contract, automated
checks, and explicit limitations while breaking changes remain expected.

## Current state

`prepare_statement` accepts exactly one `SELECT`, `INSERT`, `UPDATE`, or
`DELETE`, optional source-native sequence or mapping bindings, and explicit
source and target dialects. It resolves logical source parameter slots,
preserves supplied values, lifts direct field-associated literals, and returns
one execution-complete package:

```python
{
    "dialect": "sqlite,sqlite",
    "statement_type": "SELECT",
    "sql": "SELECT * FROM orders WHERE category = ?",
    "bindings": ["sales"],
    "analysis": {
        "hardcoded_value_count": 1,
        "hardcoded_field_count": 1,
    },
}
```

SQLite execution is verified for all four statement types, the retained complex
fixture, and 590 torture cases with no genuine failures. Hardcoded and
already-parameterized inputs converge to the same package shape. The demo
consumer deliberately owns database execution; the library does not connect to
a database.

## Design notes

- [AST source and target](docs/AST_SOURCE_TARGET.md)
- [Statement API contract](docs/STATEMENT_API.md)

## Run

```bash
uv sync
uv run python demo/sqlite_consumer.py
uv run python demo/sqlite_torture_consumer.py
uv run python -m unittest discover -v
uvx ruff check src/sqlglot_experiments demo tests
uv run --with pyright pyright src/sqlglot_experiments demo tests

# Retained experiments
uv run python experiments/native_api_showcase.py
uv run python -m experiments.sql_statement_fingerprinting.showcase
uv run python -m unittest experiments.sql_statement_fingerprinting.test_fingerprint
```

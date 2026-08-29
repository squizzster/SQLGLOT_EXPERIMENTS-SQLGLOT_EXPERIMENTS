# SQLGlot Experiments

A Python prototype for turning SQL with field-associated hardcoded values into
a compact, parameterized execution package using
[SQLGlot](https://github.com/tobymao/sqlglot).

## Development mode

`PROTO` — strengthen the useful path with a small public contract, automated
checks, and explicit limitations while breaking changes remain expected.

## Current state

`prepare_statement` is the library's first public API command. It accepts
exactly one `SELECT`, `INSERT`, `UPDATE`, or `DELETE`, optional source-native
sequence or mapping bindings, and explicit source and target dialects. Every
recognised outcome carries the fixed `success`, `warnings`, and `msg` envelope.
A successful hardcoded-value replacement returns:

```python
{
    "success": True,
    "warnings": True,
    "msg": "warnings: replaced 1 hardcoded value with placeholder",
    "sql_fingerprint": "<64-character SHA-256>",
    "dialect": ["sqlite", "sqlite"],
    "statement_type": "SELECT",
    "sql": "SELECT * FROM orders WHERE category = ?",
    "bindings": ["sales"],
    "where_fields": [],
    "analysis": {
        "hardcoded_value_count": 1,
        "hardcoded_field_count": 1,
    },
}
```

Failures contain only the standard envelope; they never contain fake executable
SQL or bindings. Messages are owned by this library, single-line, and limited to
240 characters. Missing, invalid, or malformed public arguments return specific
failure messages instead of Python argument-binding exceptions. Unexpected
internal defects remain exceptions.

SQLite execution is verified for all four statement types, the retained complex
fixture, and 590 torture cases with no genuine failures. Hardcoded and
already-parameterized inputs converge to the same package shape. The demo
consumer deliberately owns database execution; the library does not connect to
a database.

Internal statement fingerprinting produces the public success-payload field
`sql_fingerprint`: a value-independent SHA-256 for `SELECT`, `INSERT`, `UPDATE`,
and `DELETE`. The fingerprint function itself remains private, and the
source-to-target dialect route remains part of the fingerprint identity.

Successful packages also include `where_fields`, the deduplicated qualified
fields beneath `WHERE` nodes that the source AST resolves directly to physical
tables. Every returned item has a table; its database is `None` when SQL does
not name one. Unqualified, ambiguous, CTE, and derived-table references are
omitted rather than guessed.

## Design notes

- [Public API envelope](docs/API_ENVELOPE.md)
- [AST source and target](docs/AST_SOURCE_TARGET.md)
- [Statement API contract](docs/STATEMENT_API.md)
- [Statement fingerprinting](docs/STATEMENT_FINGERPRINTING.md)

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

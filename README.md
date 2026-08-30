# SQLGlot Experiments

A Python prototype for turning SQL with field-associated hardcoded values into
a compact, parameterized execution package using
[SQLGlot](https://github.com/tobymao/sqlglot).

## Development mode

`PROTO` — strengthen the useful path with a small public contract, automated
checks, and explicit limitations while breaking changes remain expected.

## Current state

The library exposes two public API commands. `prepare_statement` accepts
exactly one SQLGlot-parsable source statement plus explicit source and target
dialects. `SELECT`, `INSERT`, `UPDATE`, and `DELETE` use the extended preparation
pipeline; every other parsed AST receives a compact generic success envelope
and fingerprint without transformation. Every recognised outcome carries the
fixed `success`, `warnings`, and `msg` envelope. `set_lru_cache_size` configures
the library's prepared-DML structure cache in the calling Python process and
returns the same fixed envelope.
A successful hardcoded-value replacement returns:

```python
{
    "success": True,
    "warnings": True,
    "msg": "warnings: replaced 1 hardcoded value with placeholder",
    "envelope_type": "prepared",
    "sql_fingerprint": "<64-character SHA-256>",
    "dialect": ["sqlite", "sqlite"],
    "statement_type": "SELECT",
    "sql": "SELECT * FROM orders WHERE category = ?",
    "bindings": ["sales"],
    "where_fields": ["orders.category"],
    "analysis": {
        "hardcoded_value_count": 1,
        "hardcoded_field_count": 1,
    },
}
```

A parsed statement outside the extended DML route returns only:

```python
{
    "success": True,
    "warnings": False,
    "msg": "success: ok",
    "envelope_type": "accepted",
    "sql_fingerprint": "<64-character SHA-256>",
}
```

This generic result confirms one source AST was accepted. It does not generate
target SQL, process bindings, transform literals, or claim engine readiness.
Malformed SQL such as `srerlct woof where` still returns the fixed failure
envelope with `envelope_type: "failure"`.

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

Successful packages also include `where_fields`, every distinct field beneath
`WHERE` nodes as SQL-shaped strings. The fixed forms are `field`,
`table.field`, and `database.table.field`, depending on how much physical
ownership the source AST proves. The field itself is never omitted. Across 587
successful adversarial packages, 559 WHERE-field entries were returned: 494
qualified to a physical table and 65 retained as bare fields.

Prepared SQL structures use a built-in LRU with a default limit of 128 entries
per Python process. Consumers may replace that limit with
`set_lru_cache_size(size)`; a successful call empties the current process cache.
Normalized dialects, exact SQL, and binding names identify the structure;
caller binding values remain outside the cache. Hits resolve the cached binding
route with current values and return a fresh public envelope. Separate Python
processes therefore have separate library caches.

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

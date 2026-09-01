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
dialects. `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, and `REPLACE` use one
extended preparation pipeline; every other parsed AST receives a compact
generic success envelope and fingerprint without transformation. `WITH` is a
clause on the effective operation, never a synthetic statement type. Every
recognised outcome carries the fixed `success`, `warnings`, and `msg` envelope.
`set_lru_cache_size` configures the library's prepared-statement structure
cache in the calling Python process and returns the same fixed envelope.
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
        "returns_rows": True,
        "contains_unresolved_function_calls": False,
        "insert": None,
        "existing_row_mutations": {
            "effects": [],
            "evidence_complete": True,
        },
    },
}
```

A parsed statement outside the extended preparation route returns only:

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

SQLite execution is verified for the original four operations, ten REPLACE
scenarios, the retained complex fixture, and 590 torture cases with no genuine
failures. Seven retained MERGE scenarios are compared with direct execution on
DuckDB. Hardcoded and already-parameterized inputs converge to the same package
shape. The demo consumer deliberately owns database execution; the library
does not connect to a database.

Internal statement fingerprinting produces the public success-payload field
`sql_fingerprint`: a value-independent SHA-256 for all six prepared operation
types. The fingerprint function itself remains private, and the source-to-target
dialect route remains part of the fingerprint identity.

Successful packages also include `where_fields`, every distinct field beneath
`WHERE` nodes as SQL-shaped strings. The fixed forms are `field`,
`table.field`, and `database.table.field`, depending on how much physical
ownership the source AST proves. The field itself is never omitted. Across 587
successful adversarial packages, 559 WHERE-field entries were returned: 494
qualified to a physical table and 65 retained as bare fields.

Every prepared package also carries `analysis.insert`. It is `None` for every
non-INSERT family. For INSERT it contains the target's separate catalog, schema,
and table components, the exact explicit supplied-column sequence, and an
optional row-to-binding-index map for an unmodified source `INSERT ... VALUES`.
The map identifies only direct value cells in the authoritative returned binding
order; computed cells and INSERT modifiers receive `None`. Quoted dots remain
identifier content, qualification is not flattened, duplicates remain visible,
and an absent column list stays empty. The library does not inspect database
schema or claim auto-increment/unique eligibility; its consumer combines these
static SQL facts with its own schema evidence.

`analysis.returns_rows` is authoritative target-AST evidence. It is `True` for
queries and for write statements with an explicit result projection such as
`RETURNING`; it is `False` for writes without one. Consumers use this fact to
distinguish authored row results from incidental driver metadata.

`analysis.contains_unresolved_function_calls` is conservative target-AST
evidence that at least one function call remains an anonymous, dialect-specific
call rather than a SQLGlot-recognized function node. A schema-owning execution
consumer can use it to reject calls whose indirect database effects it cannot
prove. It does not claim that the call exists on a server or that it has side
effects. MySQL's legacy `VALUES(column)` conflict-update form is the one
explicit safe anonymous-call exception.

`analysis.existing_row_mutations` reports direct, target-AST-visible effects on
existing rows across UPDATE, DELETE, INSERT conflict updates, REPLACE, MERGE,
and nested data-modifying statements. Each effect keeps its catalog, schema,
and table structured, lists assignment-target columns, and states whether rows
may be deleted. `evidence_complete: False` tells a schema-owning consumer that
the exact direct effect could not be resolved safely. WHERE fields are not
assignment targets. Triggers, cascades, stored routines, and other engine-side
indirect effects remain outside this schema-free AST contract.

Prepared SQL structures use a built-in LRU with a default limit of 128 entries
per Python process. Consumers may replace that limit with
`set_lru_cache_size(size)`; a successful call empties the current process cache.
Normalized dialects, exact SQL, and binding names identify the structure;
caller binding values remain outside the cache. Hits resolve the cached binding
route with current values and return a fresh public envelope. Separate Python
processes therefore have separate library caches.

REPLACE is configured for SQLite, MySQL, and DuckDB. MERGE is configured for
PostgreSQL, DuckDB, Snowflake, T-SQL, BigQuery, Databricks, and Oracle. Common
forms may cross configured dialects; dialect-only clauses must remain
representable at the target. Narrow adapters preserve MySQL REPLACE forms,
DuckDB `INSERT BY NAME`, Snowflake `ALL BY NAME`, and T-SQL's required MERGE
terminator. Structural validation and a target-AST semantic signature prevent
SQLGlot's permissive parser or generator from silently dropping these forms.
Configured support covers the tested common structural route, not every vendor
extension; unadapted syntax returns the fixed failure envelope rather than a
lossy package.

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
uvx ruff check src/sqlglot_experiments demo tests \
  experiments/replace_statement_support \
  experiments/merge_with_statement_support
uv run --with pyright pyright src/sqlglot_experiments demo tests \
  experiments/replace_statement_support \
  experiments/merge_with_statement_support

# Retained experiments
uv run python experiments/native_api_showcase.py
uv run python -m experiments.sql_statement_fingerprinting.showcase
uv run python -m unittest experiments.sql_statement_fingerprinting.test_fingerprint
uv run python -m experiments.replace_statement_support.run_experiment
uv run --with duckdb python -m experiments.merge_with_statement_support.run_experiment
uv run --with duckdb python -m unittest -v \
  experiments.replace_statement_support.test_experiment \
  experiments.merge_with_statement_support.test_experiment
```

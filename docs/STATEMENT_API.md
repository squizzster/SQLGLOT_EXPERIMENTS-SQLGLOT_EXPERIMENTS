# Statement API contract

```python
prepare_statement(
    sql: str | None = None,
    *,
    bindings: Sequence[object] | Mapping[str, object] | None = None,
    source_dialect: str | None = None,
    target_dialect: str | None = None,
) -> PreparationResult
```

SQL and both dialects are semantically required. Their `None` defaults allow
omission to enter the public boundary and return a controlled failure envelope
instead of raising Python's argument-binding `TypeError`. `bindings` is omitted
when the input has no placeholders. Otherwise it resolves values through the
declared source dialect's logical parameter slots. Every recognised outcome
follows the [public API envelope](API_ENVELOPE.md). A successful replacement
returns one compact, execution-complete package:

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

SQL requiring no replacement returns `success: True`, `warnings: False`, and
`msg: "success: ok"`. Failure returns only:

```python
{
    "success": False,
    "warnings": False,
    "msg": "failure: <compact library-owned reason>",
}
```

The fixed envelope does not gain error-specific fields. Known public-call
failures have library-owned messages, including:

| Situation | `msg` |
|---|---|
| Missing or blank SQL | `failure: sql is required` |
| Non-string SQL | `failure: sql must be a string` |
| Missing or blank source dialect | `failure: source dialect is required` |
| Non-string source dialect | `failure: source dialect must be a string` |
| Unsupported source dialect | `failure: unsupported source dialect: <name>` |
| Missing or blank target dialect | `failure: target dialect is required` |
| Non-string target dialect | `failure: target dialect must be a string` |
| Unsupported target dialect | `failure: unsupported target dialect: <name>` |
| Extra positional argument | `failure: only sql may be passed positionally` |
| SQL passed twice | `failure: sql was provided more than once` |
| Unknown keyword | `failure: unexpected argument: <name>` |
| Invalid or mismatched bindings | `failure: bindings: <compact reason>` |

Empty or multiple statements, unsupported statement types, malformed SQL,
unsupported placeholder forms, and target-rendering failures also return this
same fixed failure envelope. An unexpected internal defect raises an exception;
it is not misreported as a recognised caller failure.

Returned bindings are native Python values in generated-target-placeholder
order. This may differ from source order when SQLGlot rewrites a construct; for
example, SQLite `LIMIT ?, ?` becomes `LIMIT ? OFFSET ?`, and the corresponding
values are reordered with it.

`dialect` is always the ordered `[source, target]` pair.

`where_fields` is always an array. It contains distinct qualified fields found
beneath `WHERE` nodes when SQLGlot can resolve the qualifier directly to a
physical table in the source AST:

```python
prepare_statement(
    "SELECT * FROM main.people AS p WHERE p.id = 1 AND p.status = 'active'",
    source_dialect="sqlite",
    target_dialect="sqlite",
)["where_fields"]

# [
#     {"database": "main", "table": "people", "field": "id"},
#     {"database": "main", "table": "people", "field": "status"},
# ]
```

The table is always known for every returned item. The database is `None` when
the SQL does not name it; no database is guessed. Unqualified fields, ambiguous
references, and fields qualified by a CTE or derived-table alias are omitted
because the source AST alone does not prove a physical table for them. Thus an
empty array means "no reliably resolved qualified WHERE fields", not
necessarily "no WHERE clause". Repeated references to the same physical
database/table/field are returned once, in first-occurrence order.

`hardcoded_value_count` counts replaced occurrences and
`hardcoded_field_count` counts their distinct AST-associated fields. These
counts are the machine-readable warning that Brick 1 replaced hardcoded values;
both remain `0` for already-parameterized input. An `INSERT` without a column
list can still lift its values, but its field count is `0` because the SQL does
not name the fields.

Missing or extra caller bindings return a failure envelope. A successful return
therefore always contains every binding needed by its generated SQL. Repeated
source slots reuse one input value and expand into generated-target-placeholder
order when necessary.

SQLite accepts sequences for native slot numbering and mappings for named
parameters. This covers `?`, `?NNN`, `:name`, `@name`, `$name`, repeated names,
and numbered gaps. PostgreSQL `$N` parameters use numeric slots and ordered
sequence bindings; mappings are not inferred.

## Prototype scope

The AST transform currently lifts direct constants from:

- field comparisons, including `LIKE`;
- `IN` lists and `BETWEEN` bounds;
- `UPDATE SET` assignments;
- `INSERT ... VALUES` rows;
- the same constructs inside nested queries, joins, and qualified table names.

Projection constants, `LIMIT`, JSON paths, function configuration arguments,
typed or wrapped constants, computed assignments, and `IS NULL` remain in the
SQL. Driver-only template markers outside the declared SQLGlot dialect contract
are not adapted here. Compact PostgreSQL `$N` parameters without token
separation and SQLite's extended Tcl-style parameter names remain unsupported.

The SQLite target is execution-tested with Python's `sqlite3` driver, including
same-dialect cast affinity and partial-index predicates. Other targets use
SQLGlot's target rendering, including its placeholder spelling; that is not
target-engine validation. Brick 1 owns analysis and preparation only; a later
pipeline brick owns database execution.

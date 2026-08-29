# Statement API contract

```python
prepare_statement(
    sql: str,
    *,
    bindings: Sequence[object] | None = None,
    source_dialect: str,
    target_dialect: str,
) -> PreparedStatement
```

Both dialects are required. `bindings` is omitted when the input has no
placeholders; otherwise it contains one value for each placeholder occurrence in
source SQL order. The function returns one compact, execution-complete package:

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

Returned bindings are native Python values in generated-target-placeholder
order. This may differ from source order when SQLGlot rewrites a construct; for
example, SQLite `LIMIT ?, ?` becomes `LIMIT ? OFFSET ?`, and the corresponding
values are reordered with it.

`hardcoded_value_count` counts replaced occurrences and
`hardcoded_field_count` counts their distinct AST-associated fields. These
counts are the machine-readable warning that Brick 1 replaced hardcoded values;
both remain `0` for already-parameterized input. An `INSERT` without a column
list can still lift its values, but its field count is `0` because the SQL does
not name the fields.

Missing or extra caller bindings raise `BindingCountError`; a successful return
therefore always contains every binding needed by its generated SQL. Repeated
named placeholders still take one input value per occurrence because Brick 1's
input contract is ordered rather than driver-specific.

## Prototype scope

The AST transform currently lifts direct constants from:

- field comparisons, including `LIKE`;
- `IN` lists and `BETWEEN` bounds;
- `UPDATE SET` assignments;
- `INSERT ... VALUES` rows;
- the same constructs inside nested queries, joins, and qualified table names.

Projection constants, `LIMIT`, JSON paths, function configuration arguments,
typed or wrapped constants, computed assignments, and `IS NULL` remain in the
SQL. SQLite's `?`, `?NNN`, `:name`, `@name`, `$name`, and `$1` forms and
PostgreSQL numeric parameters are accepted and normalized. Driver-only template
markers outside the SQLGlot dialect contract are not adapted here.

The SQLite target is execution-tested with Python's `sqlite3` driver. Other
targets use SQLGlot's target rendering, including its placeholder spelling; that
is not target-engine validation. Brick 1 owns analysis and preparation only; a
later pipeline brick owns database execution.

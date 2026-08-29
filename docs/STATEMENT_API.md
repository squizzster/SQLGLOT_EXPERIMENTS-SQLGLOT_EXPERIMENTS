# Statement API contract

```python
prepare_statement(
    sql: str, *, source_dialect: str, target_dialect: str
) -> PreparedStatement
```

Both dialects are required. The function returns one compact execution package:

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

The bindings are native Python values in generated-placeholder order.
`hardcoded_value_count` counts occurrences; `hardcoded_field_count` counts
distinct fields associated by the AST. An `INSERT` without a column list can
still lift its values, but its field count is `0` because the SQL does not name
the fields.

## Prototype scope

The AST transform currently lifts direct constants from:

- field comparisons, including `LIKE`;
- `IN` lists and `BETWEEN` bounds;
- `UPDATE SET` assignments;
- `INSERT ... VALUES` rows;
- the same constructs inside nested queries, joins, and qualified table names.

Projection constants, `LIMIT`, JSON paths, function configuration arguments,
typed or wrapped constants, computed assignments, and `IS NULL` remain in the
SQL. Placeholders recognized by the source dialect are rejected because SQL text
alone does not contain their binding values, so the API cannot return a complete
ordered binding list. SQLite's `?`, `:name`, `@name`, `$name`, and `$1` forms are
covered; driver-only template markers outside a SQLGlot dialect are not.

The SQLite target is execution-tested with Python's `sqlite3` driver. Other
targets use SQLGlot's target rendering, including its placeholder spelling; that
is not a universal database-driver adapter or target-engine validation.

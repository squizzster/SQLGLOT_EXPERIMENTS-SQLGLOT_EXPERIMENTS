# Public API contract

The package exposes exactly two public operations:

```python
prepare_statement(
    sql: str | None = None,
    *,
    bindings: Sequence[object] | Mapping[str, object] | None = None,
    source_dialect: str | None = None,
    target_dialect: str | None = None,
) -> PreparationResult

set_lru_cache_size(size: int | None = None) -> ApiEnvelope
```

SQL and both dialects are semantically required. Their `None` defaults allow
omission to enter the public boundary and return a controlled failure envelope
instead of raising Python's argument-binding `TypeError`. `bindings` is omitted
when prepared input has no placeholders. Otherwise the extended route resolves
values through the declared source dialect's logical parameter slots. Every
recognised outcome follows the [public API envelope](API_ENVELOPE.md).

`prepare_statement` first requires SQLGlot to parse exactly one source-dialect
AST. It then has two fixed success routes:

| Source AST | Result |
|---|---|
| `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, or `REPLACE` | Complete extended preparation package |
| Any other AST accepted by SQLGlot | Generic acceptance envelope and source fingerprint |

`WITH` remains a clause on the AST's effective operation. A CTE-prefixed
statement therefore reports `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, or
`REPLACE`; it never reports the less informative synthetic type `WITH`.

A successful extended replacement returns:

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
        "insert": None,
    },
}
```

A parsed statement outside the prepared route returns exactly:

```python
{
    "success": True,
    "warnings": False,
    "msg": "success: ok",
    "envelope_type": "accepted",
    "sql_fingerprint": "<64-character SHA-256>",
}
```

The generic route stops after source parsing and identification. It does not
resolve binding values, lift literals, generate target SQL, reparse a target
AST, extract `where_fields`, or enter the prepared-structure LRU. Its
`sql_fingerprint` covers the exact source SQL and normalized source/target
dialect route. It is therefore a stable identity for that accepted input, not
the value-independent prepared-statement fingerprint described below. A sequence or mapping
passed as `bindings` is accepted but not interpreted on this route.

SQL requiring no replacement returns `success: True`, `warnings: False`, and
`msg: "success: ok"`. Failure returns only:

```python
{
    "success": False,
    "warnings": False,
    "msg": "failure: <compact library-owned reason>",
    "envelope_type": "failure",
}
```

`envelope_type` is always present and has exactly one of three values:
`prepared`, `accepted`, or `failure`. The fixed failure envelope does not gain
error-specific fields. Known public-call failures have library-owned messages,
including:

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

Empty or multiple statements and malformed SQL return this same fixed failure
envelope. Unsupported placeholder forms, binding failures, and target-rendering
failures apply to the extended route. An unexpected internal defect raises
an exception; it is not misreported as a recognised caller failure.

Generic success means SQLGlot accepted one AST using the declared source
dialect. It does not prove that every SQL implementation would consider the
text valid, nor can it accept syntax that the installed SQLGlot version cannot
parse. SQLGlot fallback `Command` ASTs are accepted because this route does not
transform or render them.

### Configured extended dialects

REPLACE uses INSERT-family mechanics while preserving public
`statement_type: "REPLACE"` and native target syntax:

- SQLite, MySQL, and DuckDB are configured REPLACE dialects.
- Common value forms can cross those targets.
- MySQL-only modifiers, `SET`, `PARTITION`, and `TABLE` forms remain
  same-dialect because there is no proven schema-free equivalent everywhere.
- MySQL `VALUE`, `VALUES ROW(...)`, source `TABLE`, and the valid
  `REPLACE ... WITH ... SELECT` CTE placement are adapted explicitly.
- `REPLACE ... SET` is rendered back as SET so MySQL's special right-hand
  column semantics are not changed to VALUES. The specific `DEFAULT(column)`
  shape currently lost by SQLGlot returns a controlled failure rather than a
  false package.
- MySQL optimizer hints immediately after `REPLACE` return a controlled failure
  because SQLGlot does not preserve their required position. The library does
  not interpret or execute optimizer behavior.

MERGE is configured for PostgreSQL, DuckDB, Snowflake, T-SQL, BigQuery,
Databricks, and Oracle. Common action forms may cross configured targets.
Dialect-only actions, result projection, and match modes must be supported by
the target or preparation fails. Narrow adapters preserve DuckDB `INSERT BY
NAME`, Snowflake `ALL BY NAME`, Databricks star actions, BigQuery `INSERT ROW`,
and T-SQL's statement terminator. MERGE action completeness and match/action
compatibility are checked before and after generation.

Configured means the tested common structural route, not every vendor extension.
Currently unadapted forms such as T-SQL `MERGE TOP`, Oracle's combined
`UPDATE ... DELETE WHERE` action, and PostgreSQL MERGE `OVERRIDING` return the
fixed controlled failure envelope rather than a potentially lossy package.

These are parser, transformation, and target-representation guarantees. They
do not inspect schemas, keys, server versions, privileges, or engine state.

Returned bindings are native Python values in generated-target-placeholder
order. This may differ from source order when SQLGlot rewrites a construct; for
example, SQLite `LIMIT ?, ?` becomes `LIMIT ? OFFSET ?`, and the corresponding
values are reordered with it.

`dialect` is always the ordered `[source, target]` pair.

`where_fields` is always a `list[str]` containing every distinct field found
beneath `WHERE` nodes. Each item uses the most specific physical SQL
qualification the source AST proves:

```python
prepare_statement(
    "SELECT * FROM main.people AS p WHERE p.id = 1 AND p.status = 'active'",
    source_dialect="sqlite",
    target_dialect="sqlite",
)["where_fields"]

# ["main.people.id", "main.people.status"]
```

Qualified aliases resolve directly to their physical tables. An unqualified
field resolves when its non-correlatable query scope has exactly one direct
physical source, and for a simple `UPDATE` or `DELETE` when there is exactly one
physical DML source. The three fixed forms are:

```python
"id"                  # physical table unknown
"people.id"           # physical table proven; database unknown
"main.people.id"      # database and physical table proven
```

Ambiguous joins, potentially correlated unqualified fields, and derived/CTE
outputs use the bare-field form rather than inventing physical ownership. No
field is omitted because its source is unknown. Aliases are replaced with their
physical table names when resolved: `p.id` from `people AS p` becomes
`people.id`. Source-dialect identifier identity is respected, and quoting is
preserved when required to distinguish an identifier containing a dot from SQL
qualification dots. Repeated physical identities are returned once, in
deterministic AST order. An empty array means no field reference was found
beneath a `WHERE`; a constant-only `WHERE 1 = 1` is one such case.

A direct query used as a MERGE source is scoped like any other query, so its
WHERE fields can resolve to the source's physical table. Correlated and
ambiguous nested fields retain the conservative bare-field form.

`hardcoded_value_count` counts replaced occurrences and
`hardcoded_field_count` counts their distinct AST-associated fields. These
counts are the machine-readable warning that Brick 1 replaced hardcoded values;
both remain `0` for already-parameterized input. An `INSERT` without a column
list can still lift its values, but its field count is `0` because the SQL does
not name the fields.

`analysis.insert` is present on every prepared envelope. For `SELECT`, `UPDATE`,
`DELETE`, `MERGE`, and `REPLACE` it is `None`. A prepared INSERT returns:

```python
{
    "target": {
        "catalog": str | None,
        "schema": str | None,
        "table": str,
    },
    "supplied_columns": list[str],
}
```

These are target-AST facts because the generated target SQL is the executable
package. Qualification remains structured: a quoted table named
`"inventory.log"` has `table: "inventory.log"` and no schema, while
`inventory.log` has `schema: "inventory"` and `table: "log"`. Quoted dotted
columns likewise remain one supplied identifier. Column order and duplicates are
preserved. PostgreSQL target aliases retain the following INSERT column list.
SQL without an explicit target-column association, including ordinary positional
`VALUES` and `INSERT BY NAME`, receives an empty list; the library does not infer
schema order.

The facts are deliberately static. Multi-row input, INSERT SELECT, conflict
actions, `RETURNING`, and MySQL SET syntax retain the same target/column report.
This library does not connect to a database and therefore cannot decide whether a
target has an auto-increment identity or whether the supplied columns cover a
unique constraint. That final boolean belongs to a schema-owning consumer.

Missing caller bindings and incorrectly sized binding sequences return a
failure envelope. SQLite mappings must contain every required named key;
unrelated mapping keys are ignored. A successful return therefore always
contains every binding needed by its generated SQL. Repeated source slots reuse
one input value and expand into generated-target-placeholder order when
necessary.

## `set_lru_cache_size`

The caller may configure the library's prepared-statement structure LRU in its
current Python process:

```python
set_lru_cache_size(256)

# {
#     "success": True,
#     "warnings": False,
#     "msg": "success: ok",
# }
```

`size` may be passed positionally or by keyword and must be a positive plain
integer. A successful call replaces and empties the process-local cache.
Other Python processes are unaffected. Invalid calls return
only the fixed failure envelope and leave the active cache and its entries
unchanged:

| Situation | `msg` |
|---|---|
| Missing size | `failure: lru cache size is required` |
| Zero, negative, boolean, or non-integer size | `failure: lru cache size must be a positive integer` |
| Extra positional argument | `failure: only size may be passed positionally` |
| Size passed twice | `failure: size was provided more than once` |
| Unknown keyword | `failure: unexpected argument: <name>` |

## Process-local structure cache

Successful extended statement structures use Python's built-in LRU cache, with 128
entries as the default limit. Generic accepted statements do not use this
cache. The cache is local to the Python process: callers in one process share
it, separate Python processes have independent caches, and a process restart
restores the 128-entry default with an empty cache.

The immutable cache key consists of the normalized source dialect, normalized
target dialect, exact input SQL, and ordered source binding names. Anonymous
slots receive structural names such as `#1`. Caller binding values never enter
the cache key or cached structure. A cached entry retains the generated SQL,
fingerprint, statement type, WHERE fields, analysis, status, and binding route.
Each call resolves that route against its current binding values and returns a
fresh envelope with fresh mutable containers.

Malformed SQL and invalid binding calls are not retained as cache entries.
Hardcoded literals remain part of the exact input SQL and its generated binding
route. Cache behavior does not add fields to the public envelope.

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
- REPLACE values and MySQL SET assignments through the same INSERT-family
  association;
- MERGE UPDATE assignments and explicit MERGE INSERT values;
- the same constructs inside nested queries, joins, and qualified table names.

Projection constants, `LIMIT`, JSON paths, function configuration arguments,
typed or wrapped constants, computed assignments, and `IS NULL` remain in the
SQL. Driver-only template markers outside the declared SQLGlot dialect contract
are not adapted here. Compact PostgreSQL `$N` parameters without token
separation and SQLite's extended Tcl-style parameter names remain unsupported.

The SQLite target is execution-tested with Python's `sqlite3` driver, including
same-dialect cast affinity, partial-index predicates, and ten REPLACE scenarios.
Seven MERGE scenarios are equivalence-tested on DuckDB. Other targets use
SQLGlot's target rendering, including its placeholder spelling; that is not
target-engine validation. Preparation does not prove that a particular driver,
schema, or engine will accept or execute the package. Brick 1 owns analysis and
preparation only; a later pipeline brick owns database execution.

# SQL statement fingerprinting — EXP

This small retained experiment asks whether prepared `SELECT`, `INSERT`, and
`UPDATE` statements can receive a repeatable SHA-256 based on their SQL shape,
without including externally bound values such as `Mark` or `Paul`.

## Two versions

```python
# We know the SQL dialect.
fingerprint_sql(sql, read="sqlite", placeholder_profile="qmark")

# We do not know the engine; SQLGlot uses its generic interpretation.
fingerprint_sql(sql, placeholder_profile="qmark")
```

The first SHA is namespaced by SQLGlot dialect. “Known” means dialect-known,
not validated by a live database. The second is only an engine-unknown
structural hypothesis. The two versions run independently and cannot collide
because they use different algorithm namespaces.

## Placeholders tried

The experiment covers the five Python DB-API styles plus the PostgreSQL and
SQLite spellings we observed:

```text
?  :1  :name  %s  %(name)s  $1  ?1  @name  $name
```

SQLGlot's tokenizer locates token spans so marker-looking text in tested string
literals and comments is not replaced. A small adapter changes those observed
markers to `:__sqlfp_p1`, `:__sqlfp_p2`, then SQLGlot owns parsing, statement
classification, qualified names, and canonical rendering. SHA-256 is calculated
from that rendered SQL plus the mode, dialect, statement kind, and SQLGlot
version.

Anonymous markers get a new slot per occurrence. Repeated named/numbered markers
reuse a slot. The original marker sequence is returned as `source_bindings`.

## Honest limit

This is representative EXP evidence, not a universal driver adapter. It has not
been tested against every SQL grammar, driver, quoting rule, operator, or server
configuration. Engine-unknown mode is particularly easy to interpret wrongly
for dialect-specific SQL. Add a real failing example before adding another
rule.

The SHA is suitable for experimenting with statement-shape grouping. It is not
a database validation result, semantic-equivalence proof, execution dedupe key,
authorization decision, or write-idempotency key. Inline literals remain part
of the shape; external bound values never enter the API.

## Run

```bash
uv run python -m experiments.sql_statement_fingerprinting.showcase
uv run python -m unittest experiments.sql_statement_fingerprinting.test_fingerprint
```

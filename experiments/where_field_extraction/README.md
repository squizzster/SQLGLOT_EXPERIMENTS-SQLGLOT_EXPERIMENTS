# WHERE-field extraction — EXP

This isolated experiment asks whether SQLGlot can accurately extract the
catalog/database, table, and field references used beneath every `WHERE` node
without consulting a database schema or driver.

## Accuracy boundary

- A field occurrence is every SQLGlot `Column` node with a `WHERE` ancestor.
- The nearest `WHERE` ancestor owns the occurrence, keeping nested predicates
  separate.
- A second whole-AST traversal audits completeness and detects missing or
  invented occurrences.
- Qualified aliases are resolved through SQLGlot `Scope.sources`.
- An unqualified field with one physical source is marked `inferred`.
- Ambiguous, derived, or absent source ownership remains `unresolved`.
- Database and catalog names are reported only when present on the resolved AST
  table. No default database is invented.

This measures structural references, not semantic-minimal dependencies. For
example, a column projected by a scalar subquery beneath a `WHERE` remains a
structural dependency even when an optimizer could simplify the expression.

## SQLGlot API choice

[SQLGlot's AST primer](https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md)
recommends `Expression.find_all` for simple traversal and `Scope` for semantic
source context. It warns that some unqualified columns require schema metadata
for disambiguation. The installed
[`Scope` API](https://github.com/tobymao/sqlglot/blob/main/sqlglot/optimizer/scope.py)
exposes source and column context but no direct “fields used by `WHERE`” call.
This experiment therefore combines `exp.Where`, `exp.Column`, `traverse_scope`,
`Scope.column_index`, `Scope.selected_sources`, and `Scope.sources` while
refusing schema-dependent guesses.

SQLGlot's scope traversal covers nested queries within DML but does not yield a
root scope for a simple `UPDATE` or `DELETE`. The experiment handles those two
root statements conservatively from their explicit target and any direct
`FROM`/`USING` sources.

## Run

```bash
uv run python -m experiments.where_field_extraction.run_corpus
uv run python -m unittest experiments.where_field_extraction.test_extractor -v
```

## Results

Observed with SQLGlot 30.17.0 over all 590 primary torture cases plus the
retained complex SQLite query:

- 591 inputs and 592 statements parsed; the extra statement is the deliberate
  multi-statement rejection case.
- The existing source-parameter lexer normalized SQLite `?NNN` syntax before
  parsing; no input was excluded.
- 428 `WHERE` nodes contained 818 field occurrences.
- The independent whole-AST audit also found 818 occurrences: zero missing or
  invented fields.
- SQLGlot scope traversal produced zero errors.
- 379 of 818 occurrences (46.3%) mapped observably to a physical table through
  explicit qualification or alias scope.
- Another 365 occurrences (44.6%) produced a single-source table inference.
  These are useful candidates but are not schema-verified facts.
- 74 occurrences (9.0%) deliberately remained unresolved: 73 referenced
  derived/CTE outputs and one referenced a table-valued function. Resolving
  those to physical base fields is lineage analysis, not direct WHERE-field
  extraction.
- Zero database names were reported because none were present on the resolved
  tables in this corpus. The extractor correctly refused to invent SQLite's
  implicit `main` database.

## Conclusion

Field extraction is accurate for the tested structural definition: every
`Column` beneath every `WHERE` was captured across the complete retained corpus.
Physical table extraction is observed where AST source ownership is explicit;
single-source ownership remains visibly inferred, and derived outputs remain
unresolved. Database names can be extracted only when SQL names them.

This is strong corpus evidence, not a proof for every SQL grammar. A production
contract must not claim that every field has a physical database/table
identity.

## Production decision

Every distinct field was promoted to the public `prepare_statement` success
payload as the SQL-shaped `where_fields: list[str]`. Observed physical sources
and the narrow single-direct-source inference render as `table.field` or
`database.table.field`. Ambiguous, correlatable, derived, and unresolved
ownership renders as the bare `field`; those fields are never omitted.

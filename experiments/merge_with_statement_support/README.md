# MERGE and WITH statement-support experiment

## Status

Folded into the main library on the `proto/replace-merge-with-support` change.
The original prototype helpers remain in Git history; the runner and tests now
exercise the integrated public API directly.

## Original question

Can `MERGE` use the extended preparation pipeline, and should `WITH` become a
separate public statement type?

The experiment classified SQLGlot's real `exp.Merge` root as `MERGE` and added
one narrow association for hardcoded values in a MERGE INSERT action. That
boundary is now integrated. Existing parameter planning, literal lifting,
binding ordering, WHERE-field extraction, target generation, fingerprinting,
cache, and public envelopes remain authoritative.

## Run

DuckDB is supplied ephemerally so the project dependency set is unchanged:

```bash
uv run --with duckdb python -m experiments.merge_with_statement_support.run_experiment
uv run --with duckdb python -m unittest experiments.merge_with_statement_support.test_experiment -v
```

## Scope

The structural matrix covers CTE-prefixed SELECT, INSERT, UPDATE, DELETE, and
MERGE statements; recursive, multiple, materialized, data-modifying, nested,
quoted, commented, and parameterized CTEs; and malformed WITH input. MERGE
coverage includes PostgreSQL, DuckDB, T-SQL, BigQuery, Snowflake, Databricks,
and Oracle forms, hardcoded and existing values, all three mutation actions,
conditional and multiple actions, DO NOTHING, DEFAULT, BY SOURCE/TARGET,
BY NAME/star actions, RETURNING, source WHERE clauses, CTE prefixes,
cross-dialect rendering, quoting, malformed SQL, bad bindings, and multiple
statements. Known-valid DuckDB `INSERT BY NAME` and Snowflake `ALL BY NAME`
forms are retained specifically to verify the integrated dialect adapters.

Seven real DuckDB comparisons execute raw and prepared MERGE statements on
independently initialized databases and compare both returned rows and final
table state.

## Implemented boundary

`WITH` is a clause, not a peer root operation in SQLGlot's AST. A parsed
CTE-prefixed statement remains an `exp.Select`, `exp.Insert`, `exp.Update`,
`exp.Delete`, or `exp.Merge` with a `with_` child. The experiment therefore
preserves the effective operation in `statement_type`; it does not manufacture
a `WITH` type that would hide whether the package reads or mutates data.

SQLGlot is a parser/transpiler rather than an engine validator. SQLite and
MySQL MERGE targets are rejected explicitly instead of being retained as
unsafe parse/render observations.

The experiment also extends WHERE scope discovery only for query nodes beneath
a MERGE root. This proves that a source-subquery field such as `tenant_id` can
still be returned as `incoming.tenant_id` under the existing public contract.

## Observations

Observed with SQLGlot 30.17.0 and DuckDB 1.5.5:

- All 33 structural cases produced their expected controlled outcome.
- All 12 valid CTE-prefixed cases entered the extended pipeline under their
  effective `SELECT`, `INSERT`, `UPDATE`, `DELETE`, or `MERGE` root. No package
  had the synthetic statement type `WITH`.
- MERGE reuses the existing extended pipeline after classification, structural
  validation, and a narrow INSERT-action field association. The full hardcoded
  PostgreSQL case lifted six values, including both INSERT-action values.
- Hardcoded and already-parameterized MERGE forms converged to the same
  structural fingerprint.
- All seven DuckDB comparisons returned equivalent rows and final table state
  for raw and prepared SQL.
- A WHERE field inside a MERGE source query resolved to its physical source as
  `incoming.tenant_id` after scope discovery began at the nested query.
- Narrow adapters preserve valid Snowflake `UPDATE ALL BY NAME` / `INSERT ALL
  BY NAME` and DuckDB `INSERT BY NAME` forms.
- Strict MERGE action parsing and structural validation reject the formerly
  unsafe incomplete form.
- Explicit target capability rejects SQLite and MySQL MERGE routes. A semantic
  signature checks action structure again after target rendering.

## Conclusion

`MERGE` is now the sixth extended execution family and reuses the authoritative
pipeline. It is not an INSERT or UPDATE subtype because one
statement may conditionally insert, update, and delete.

`WITH` should be supported everywhere it prefixes a supported primary
operation, but it should not become a seventh `statement_type`. Returning
`WITH` would discard the more important fact that the primary operation is a
SELECT, INSERT, UPDATE, DELETE, MERGE, or REPLACE. The public syntax set can be
described as those six operations, each with optional WITH/CTE clauses.

The integrated implementation includes structural validation, direct
source-query WHERE scope discovery, controlled target capability, and narrow
DuckDB/Snowflake adapters. WITH remains a clause and never becomes a parallel
public pipeline or synthetic statement type.

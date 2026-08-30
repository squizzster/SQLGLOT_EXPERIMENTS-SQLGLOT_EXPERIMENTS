# MERGE and WITH statement-support experiment

## Question

Can `MERGE` use the extended preparation pipeline, and should `WITH` become a
separate public statement type?

This experiment does not modify the main library. It temporarily classifies
SQLGlot's real `exp.Merge` root as `MERGE` and adds one narrow association for
hardcoded values in a MERGE INSERT action. The existing parameter planning,
literal lifting, binding ordering, WHERE-field extraction, target generation,
fingerprinting, cache, and public envelopes remain authoritative.

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
forms are retained specifically to expose SQLGlot parser gaps.

Seven real DuckDB comparisons execute raw and prepared MERGE statements on
independently initialized databases and compare both returned rows and final
table state.

## Boundary being tested

`WITH` is a clause, not a peer root operation in SQLGlot's AST. A parsed
CTE-prefixed statement remains an `exp.Select`, `exp.Insert`, `exp.Update`,
`exp.Delete`, or `exp.Merge` with a `with_` child. The experiment therefore
preserves the effective operation in `statement_type`; it does not manufacture
a `WITH` type that would hide whether the package reads or mutates data.

SQLGlot is a parser/transpiler rather than an engine validator. It can parse
and render a MERGE while declaring SQLite or MySQL even though those engines do
not implement standard MERGE. Those two routes are retained as explicitly
known unsafe observations rather than treated as execution evidence.

The experiment also extends WHERE scope discovery only for query nodes beneath
a MERGE root. This proves that a source-subquery field such as `tenant_id` can
still be returned as `incoming.tenant_id` under the existing public contract.

## Observations

Observed with SQLGlot 30.17.0 and DuckDB 1.5.5:

- All 33 structural cases produced their expected controlled outcome.
- All 12 valid CTE-prefixed cases entered the extended pipeline under their
  effective `SELECT`, `INSERT`, `UPDATE`, `DELETE`, or `MERGE` root. No package
  had the synthetic statement type `WITH`.
- MERGE needed only root classification plus a narrow INSERT-action field
  association to reuse the existing extended pipeline. The full hardcoded
  PostgreSQL case lifted six values, including both INSERT-action values.
- Hardcoded and already-parameterized MERGE forms converged to the same
  structural fingerprint.
- All seven DuckDB comparisons returned equivalent rows and final table state
  for raw and prepared SQL.
- A WHERE field inside a MERGE source query resolved to its physical source as
  `incoming.tenant_id` after scope discovery began at the nested query.
- SQLGlot rejected valid Snowflake `UPDATE ALL BY NAME` / `INSERT ALL BY NAME`.
- SQLGlot misparsed valid DuckDB `INSERT BY NAME` as `INSERT (BY AS NAME)` and
  leniently accepted one incomplete MERGE. Both are unsafe successes that need
  an adapter or structural rejection before public support.
- SQLGlot structurally prepared SQLite and MySQL targets even though those
  engines do not implement standard MERGE. Target capability therefore needs
  an explicit boundary; parse/render round trips alone are insufficient.

## Conclusion

`MERGE` is suitable as a sixth extended execution family and can reuse the
authoritative pipeline. It is not an INSERT or UPDATE subtype because one
statement may conditionally insert, update, and delete.

`WITH` should be supported everywhere it prefixes a supported primary
operation, but it should not become a seventh `statement_type`. Returning
`WITH` would discard the more important fact that the primary operation is a
SELECT, INSERT, UPDATE, DELETE, MERGE, or REPLACE. The public syntax set can be
described as those six operations, each with optional WITH/CTE clauses.

Before MERGE is folded into main, the smallest coherent implementation should
also add MERGE structural validation, nested-query WHERE scope discovery, and
controlled target capability handling. DuckDB `INSERT BY NAME` and Snowflake
`ALL BY NAME` must either receive narrow dialect adapters or controlled failure
until adapted; they must not return a prepared package with changed syntax.

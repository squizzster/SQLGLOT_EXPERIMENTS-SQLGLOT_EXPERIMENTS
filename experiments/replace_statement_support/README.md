# REPLACE statement support experiment

## Status

Folded into the main library on the `proto/replace-merge-with-support` change.
The original prototype helpers remain in Git history; the runner and tests now
exercise the integrated public API directly.

## Original question

Can `REPLACE` become a fifth extended statement family by adding a narrow
SQLGlot dialect adapter and then reusing the existing INSERT preparation
pipeline?

The experiment proposed SQLite/MySQL tokenizers and parsers that produce an
`exp.Insert` with `alternative="REPLACE"`, a MySQL generator that emits native
`REPLACE`, and internal statement classification as `REPLACE`. That boundary is
now integrated. Parameter planning, literal lifting, binding ordering,
WHERE-field extraction, fingerprinting, caching, envelopes, and execution
remain one authoritative library pipeline.

## Run

```bash
uv run python -m experiments.replace_statement_support.run_experiment
uv run python -m unittest experiments.replace_statement_support.test_experiment -v
```

## Scope

The adversarial matrix covers SQLite and MySQL values, existing placeholders,
SQLite named/numbered parameters, multi-row input, missing column lists,
defaults, INSERT/REPLACE SELECT, CTE prefixes, RETURNING, MySQL SET, PARTITION,
TABLE, optional INTO, LOW_PRIORITY, DELAYED, comments, quoting, cross-dialect
SQLite/MySQL rendering, unsupported PostgreSQL rendering, malformed SQL, and
multiple statements. Ten SQLite cases compare raw REPLACE execution with the
prepared package on independently initialized databases, including primary-key
replacement, unique-key replacement, and one input row conflicting with two
existing rows.

## Implemented boundary

`exp.Replace` is not involved; it is the scalar string function. The integrated
adapter removes statement-level REPLACE from SQLGlot's fallback-command token
set and parses it through the INSERT grammar while preserving explicit REPLACE
identity.

PostgreSQL rendering is deliberately rejected because there is no schema-free
equivalent. MySQL's singular `VALUE` and `VALUES ROW(...)` forms now have narrow
parser adapters. MySQL SET syntax is rendered back as SET so its right-hand
column semantics are not silently changed to VALUES.

## Observations

Observed with SQLGlot 30.17.0:

- All 37 matrix cases produced their expected controlled outcome.
- Thirty-one cases returned full `prepared` / `REPLACE` packages.
- All ten SQLite execution comparisons were equivalent between raw REPLACE and
  prepared SQL, including unique-key replacement and one new row conflicting
  with two existing rows.
- Bare `REPLACE` and explicit SQLite `INSERT OR REPLACE` converged to the same
  structural fingerprint despite different literal values.
- SQLite qmark, named, numbered, repeated named, and misleading placeholder
  text inside strings/comments retained correct binding ownership and order.
- SQLite-to-MySQL rendered native `REPLACE`; MySQL-to-SQLite rendered `INSERT OR
  REPLACE`; PostgreSQL returned the controlled target-rendering failure.
- MySQL `SET`, `PARTITION`, `TABLE`, optional `INTO`, `LOW_PRIORITY`, and
  `DELAYED` forms prepare structurally. The official CTE placement
  `REPLACE ... WITH ... SELECT` is supported; the invalid leading
  `WITH ... REPLACE` form fails. No MySQL server was used, so this is
  AST/rendering evidence rather than engine validation.
- MySQL qmark inputs remained controlled binding failures, consistent with the
  current library's separation of SQL dialect from driver parameter style.
- MySQL's valid singular `VALUE` spelling and `VALUES ROW(...)` both prepare,
  with row-constructor values entering the normal binding pipeline.
- The valid `DEFAULT(column)` expression inside MySQL REPLACE SET exposes an
  upstream parse-shape loss and therefore returns a controlled failure.
- Scalar `REPLACE(value, from, to)` remained a normal expression inside
  `SELECT`; ordinary INSERT and UPDATE classification remained unchanged.

## Conclusion

REPLACE is now the fifth extended family. A narrow dialect adapter produces an
INSERT-family AST with preserved REPLACE identity, and the existing preparation
pipeline handles it without a parallel transformation mechanism. Source and
target marker tokenization use the same adapter, and unconfigured targets fail
through the fixed public envelope.

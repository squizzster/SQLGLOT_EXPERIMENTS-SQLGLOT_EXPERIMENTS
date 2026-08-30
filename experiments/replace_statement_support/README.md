# REPLACE statement support experiment

## Question

Can `REPLACE` become a fifth extended statement family by adding a narrow
SQLGlot dialect adapter and then reusing the existing INSERT preparation
pipeline?

This experiment does not modify main library code. It temporarily installs
experimental SQLite/MySQL tokenizers and parsers that produce an `exp.Insert`
with `alternative="REPLACE"`, a MySQL generator that emits native `REPLACE`,
and internal statement classification as `REPLACE`. All parameter planning,
literal lifting, binding ordering, WHERE-field extraction, fingerprinting,
caching, envelopes, and SQLite execution use the real library.

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

## Initial boundary

`exp.Replace` is not involved; it is the scalar string function. The proposed
adapter removes statement-level REPLACE from SQLGlot's fallback-command token
set and parses it through the INSERT grammar while preserving explicit REPLACE
identity.

PostgreSQL rendering is deliberately rejected because there is no schema-free
equivalent. MySQL's singular `VALUE` spelling is retained as an expected parser
limitation for evaluation. MySQL row constructors are retained as an
observation because their wrapped values are outside the current literal-lift
policy.

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
- MySQL `SET`, `PARTITION`, `TABLE`, optional `INTO`, CTE prefix,
  `LOW_PRIORITY`, and `DELAYED` forms prepared structurally. No MySQL server was
  used, so this is AST/rendering evidence rather than engine validation.
- MySQL qmark inputs remained controlled binding failures, consistent with the
  current library's separation of SQL dialect from driver parameter style.
- MySQL's valid singular `VALUE` spelling remained a parse failure.
- MySQL `VALUES ROW(...)` was an unsafe false success: SQLGlot rendered wrapped
  row expressions and the current lift policy returned no bindings. This form
  must be rejected or specifically adapted before public support.
- Scalar `REPLACE(value, from, to)` remained a normal expression inside
  `SELECT`; ordinary INSERT and UPDATE classification remained unchanged.

## Conclusion

REPLACE is mechanically suitable as a fifth extended family. After a narrow
dialect tokenizer/parser adapter produces an INSERT-family AST with preserved
REPLACE identity, the existing preparation pipeline works without a parallel
transformation mechanism.

The implementation boundary must also adapt source placeholder tokenization
and target marker tokenization because SQLGlot's normal fallback-command token
collapses the remainder of bare REPLACE. Initial public support should either
add MySQL singular `VALUE` deliberately or reject it, reject `VALUES ROW(...)`
until it has execution evidence, and retain controlled failure for targets
without configured REPLACE semantics.

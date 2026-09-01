# AST source and target

Every AST call declares both its source and target. Neither is inferred.

An endpoint has two properties:

- Representation: SQL text or SQLGlot AST.
- Dialect semantics: SQLite, PostgreSQL, MySQL, SQLGlot generic, or another
  explicit dialect.

`AST` is a valid target representation. It still carries the semantics of the
dialect used to parse or prepare it. `generic` means SQLGlot's generic dialect;
it never means a dialect-free universal AST.

```text
SQLText(sqlite)
→ SQLGlotAST(sqlite)
→ SQLText(postgres)
→ SQLGlotAST(postgres)
→ SQLText(sqlite)
```

Rules:

- Successful public AST-backed operations require both dialects. Omission
  returns a failure envelope; no dialect is inferred or used as a default.
- Parse: source SQL dialect and target AST semantics are explicit.
- Transform: source and target dialect semantics are explicit on every call.
- Generate: source AST semantics and target SQL dialect are explicit.
- Reparse generated SQL as target dialect before returning it.
- Require the target AST to retain the source operation and extended-statement
  semantic signature.
- Derive execution-facing INSERT and direct existing-row mutation evidence from
  that authoritative target AST, including nested data-modifying statements.
- Derive result-row intent from that same target AST; never infer it from SQL
  substrings or future driver cursor metadata.
- Transform a copy; retain the source AST.
- Never reuse a target-prepared AST for a different target.
- SQLGlot parsing or generation is not target-engine validation.
- Dialect-specific REPLACE and MERGE policy belongs in narrow adapters and
  structural validation, not in a parallel public pipeline.

# Statement fingerprinting

`statement_fingerprinting.py` provides the internal fingerprint mechanism for
one `SELECT`, `INSERT`, `UPDATE`, or `DELETE`. The function remains non-public;
successful `prepare_statement` packages expose its digest as
`sql_fingerprint`.

- The caller supplies explicit source and target dialects.
- Bind values and SQLGlot-native placeholders are normalized while structural
  literals, statement structure, and value-site count remain significant.
- The normalized AST is rendered for the target and reparsed as that target.
- SHA-256 covers the algorithm and SQLGlot versions, statement type, canonical
  SQL, source, and target. The dialect route is therefore part of identity.
- Bindings are not required. Fingerprinting transforms a copied AST and does not
  affect generated SQL, bindings, analysis counts, or warning state.

The retained torture corpus verifies 589 single statements: all fingerprint,
and all 587 successful prepared forms converge with their source fingerprint.
The one multi-statement case is deliberately rejected.

Generic accepted statements also expose a `sql_fingerprint`, but do not enter
this DML mechanism. Their digest covers the exact input SQL and normalized
source/target dialect route. It identifies the accepted source input without
implying value normalization, target rendering, or execution readiness.

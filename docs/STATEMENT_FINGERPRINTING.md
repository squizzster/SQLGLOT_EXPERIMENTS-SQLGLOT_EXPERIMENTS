# Internal statement fingerprinting

`statement_fingerprinting.py` provides an internal, non-public fingerprint for
one `SELECT`, `INSERT`, `UPDATE`, or `DELETE`.

- The caller supplies explicit source and target dialects.
- Bind values and SQLGlot-native placeholders are normalized while structural
  literals, statement structure, and value-site count remain significant.
- The normalized AST is rendered for the target and reparsed as that target.
- SHA-256 covers the algorithm and SQLGlot versions, statement type, canonical
  SQL, source, and target. The dialect route is therefore part of identity.
- Bindings are not required. Fingerprinting transforms a copied AST and does not
  affect `prepare_statement` or its warnings.

The retained torture corpus verifies 589 single statements: all fingerprint,
and all 587 successful prepared forms converge with their source fingerprint.
The one multi-statement case is deliberately rejected.

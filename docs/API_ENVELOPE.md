# Public API envelope

Every public API command returns at least these fields:

```python
{
    "success": bool,
    "warnings": bool,
    "msg": str,
}
```

Allowed states:

| Outcome | `success` | `warnings` | `msg` prefix |
|---|---:|---:|---|
| Completed normally | `True` | `False` | `success:` |
| Completed with a warning | `True` | `True` | `warnings:` |
| Failed | `False` | `False` | `failure:` |

The library owns every message. Messages are single-line and at most 240
characters. External-library and internal exception details never become API
messages.

`prepare_statement` also returns `envelope_type` on every outcome. Its closed
values discriminate the three complete preparation-result shapes:

| `envelope_type` | Meaning |
|---|---|
| `prepared` | A configured operation completed the extended preparation pipeline |
| `accepted` | Another source statement parsed and received generic acceptance |
| `failure` | A recognised preparation failure occurred |

Other command-specific payload fields are present only on success. Consumers
may inspect `envelope_type` to narrow a `PreparationResult`, and must check
`success` before using success-only payload fields.

Recognised caller mistakes and SQL-processing failures return a fixed failure
envelope. A `prepare_statement` failure contains the three base fields plus
`envelope_type: "failure"`; other public commands retain their own contract.
This includes malformed Python call shapes, missing or invalid arguments,
binding failures on the extended preparation route, and SQL parsing or
target-rendering failures. Failure envelopes never gain condition-specific
fields.

Unexpected internal defects raise an exception instead of being disguised as
a caller failure. Internal and external exception details never become API
messages.

A warning identifies a reportable preparation intervention, currently lifting
hardcoded values into bindings. Normal target-dialect rendering and formatting
are not warnings. Fingerprinting works on a copied AST and adds its digest to
the success payload without altering generated SQL or warning state.

The prepared envelope's `analysis.insert` member is fixed-shape AST evidence, not
an execution-readiness claim. It is `None` for non-INSERT prepared families and a
structured target, supplied-column, and optional direct binding-row report for
INSERT. It does not alter warning or failure state.

The prepared envelope's `analysis.direct_writes` member reports the ordered,
structured relation targets that receive direct AST-visible writes and whether
that target evidence is complete. It includes INSERT-only MERGE and nested write
targets without making database-schema or indirect-effect claims. Target
completeness is independent from assignment-column completeness.

The prepared envelope's `analysis.existing_row_mutations` member is also
fixed-shape AST evidence. It reports direct target-AST-visible update columns and
row-deletion effects, plus whether that evidence is complete. It does not apply
schema policy, inspect a database, or claim knowledge of indirect trigger,
cascade, or stored-routine behavior.

The prepared envelope's `analysis.returns_rows` member states whether the
authoritative target AST is a query or carries an explicit write-result
projection. It does not depend on native cursor metadata.

SQLGlot-accepted statements outside the extended `SELECT`, `INSERT`, `UPDATE`,
`DELETE`, `MERGE`, and `REPLACE` route return the normal three-field success
envelope plus only a `sql_fingerprint`. This is syntactic source acceptance,
not preparation or an execution-readiness claim. `WITH` does not add another
envelope type: the AST's effective operation determines the prepared family.

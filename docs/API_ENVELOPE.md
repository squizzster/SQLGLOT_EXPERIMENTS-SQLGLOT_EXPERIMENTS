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
| `prepared` | DML completed the extended preparation pipeline |
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

SQLGlot-accepted statements outside the extended `SELECT`, `INSERT`, `UPDATE`,
and `DELETE` route return the normal three-field success envelope plus only a
`sql_fingerprint`. This is syntactic source acceptance, not preparation or an
execution-readiness claim.

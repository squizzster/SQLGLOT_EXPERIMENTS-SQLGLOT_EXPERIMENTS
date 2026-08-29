# Public API envelope

Every public API command returns these fields:

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

Command-specific payload fields are present only on success. Consumers must
check `success` before using them.

Recognised caller mistakes and SQL-processing failures return the same fixed
three-field failure envelope. This includes malformed Python call shapes,
missing or invalid arguments, binding failures, unsupported statements, and
SQL parsing or target-rendering failures. Failure envelopes never gain
condition-specific fields.

Unexpected internal defects raise an exception instead of being disguised as
a caller failure. Internal and external exception details never become API
messages.

A warning identifies a reportable preparation intervention, currently lifting
hardcoded values into bindings. Normal target-dialect rendering and formatting
are not warnings. Fingerprinting works on a copied AST and adds its digest to
the success payload without altering generated SQL or warning state.

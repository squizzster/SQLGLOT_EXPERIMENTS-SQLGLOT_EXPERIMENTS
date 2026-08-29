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
| Completed unchanged | `True` | `False` | `success:` |
| Completed with a warning | `True` | `True` | `warnings:` |
| Failed | `False` | `False` | `failure:` |

The library owns every message. Messages are single-line and at most 240
characters. External-library and internal exception details never become API
messages.

Command-specific payload fields are present only on success. Consumers must
check `success` before using them.

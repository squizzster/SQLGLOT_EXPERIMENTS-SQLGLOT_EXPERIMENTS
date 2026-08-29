from __future__ import annotations

from typing import Literal, TypedDict


class ApiSuccessEnvelope(TypedDict):
    success: Literal[True]
    warnings: bool
    msg: str


class ApiFailureEnvelope(TypedDict):
    success: Literal[False]
    warnings: Literal[False]
    msg: str


ApiEnvelope = ApiSuccessEnvelope | ApiFailureEnvelope
MAX_MSG_LENGTH = 240


def success_envelope(*, warning: str | None = None) -> ApiSuccessEnvelope:
    warning = _compact(warning) if warning else None
    return {
        "success": True,
        "warnings": warning is not None,
        "msg": _message("warnings", warning) if warning else "success: ok",
    }


def failure_envelope(reason: str) -> ApiFailureEnvelope:
    return {
        "success": False,
        "warnings": False,
        "msg": _message("failure", reason or "unknown error"),
    }


def _compact(message: str) -> str:
    return " ".join(message.split())


def _message(prefix: str, content: str) -> str:
    message = f"{prefix}: {_compact(content)}"
    if len(message) <= MAX_MSG_LENGTH:
        return message
    return f"{message[: MAX_MSG_LENGTH - 3].rstrip()}..."

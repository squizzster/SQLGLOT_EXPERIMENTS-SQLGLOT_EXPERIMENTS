"""Small SQLGlot experiment for prepared-statement shape fingerprints."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise

import sqlglot
from sqlglot import ErrorLevel, exp
from sqlglot.dialects import Dialect
from sqlglot.tokens import Token


class PlaceholderProfile(StrEnum):
    """Placeholder spellings observed in this experiment."""

    QMARK = "qmark"  # ?
    NUMERIC = "numeric"  # :1
    NAMED = "named"  # :name
    FORMAT = "format"  # %s
    PYFORMAT = "pyformat"  # %(name)s
    DOLLAR_NUMERIC = "dollar_numeric"  # $1
    SQLITE_NUMBERED = "sqlite_numbered"  # ?1
    AT_NAMED = "at_named"  # @name
    DOLLAR_NAMED = "dollar_named"  # $name
    SQLITE_NATIVE = "sqlite_native"  # ?, ?1, :name, @name, $name


_MARKER_PATTERNS = {
    PlaceholderProfile.QMARK: re.compile(r"\?"),
    PlaceholderProfile.NUMERIC: re.compile(r":[1-9][0-9]*"),
    PlaceholderProfile.NAMED: re.compile(r":[A-Za-z_][A-Za-z0-9_]*"),
    PlaceholderProfile.FORMAT: re.compile(r"%s"),
    PlaceholderProfile.PYFORMAT: re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)s"),
    PlaceholderProfile.DOLLAR_NUMERIC: re.compile(r"\$[1-9][0-9]*"),
    PlaceholderProfile.SQLITE_NUMBERED: re.compile(r"\?[1-9][0-9]*"),
    PlaceholderProfile.AT_NAMED: re.compile(r"@[A-Za-z_][A-Za-z0-9_]*"),
    PlaceholderProfile.DOLLAR_NAMED: re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*"),
    PlaceholderProfile.SQLITE_NATIVE: re.compile(
        r"(?:\?|\?[1-9][0-9]*|[:@$][A-Za-z_][A-Za-z0-9_]*)"
    ),
}
_KNOWN_ALGORITHM = "sqlglot-shape/dialect-known/v1"
_UNKNOWN_ALGORITHM = "sqlglot-shape/engine-unknown/v1"


class UnsupportedStatementError(ValueError):
    """The input was not exactly one SELECT, INSERT, or UPDATE."""


@dataclass(frozen=True, slots=True)
class SqlStatementFingerprint:
    sha256_hex: str
    certainty: str
    canonical_dialect: str
    statement_kind: str
    canonical_sql: str
    binding_pattern: tuple[str, ...]
    source_bindings: tuple[str, ...]
    fingerprint_payload: str


@dataclass(frozen=True, slots=True)
class FingerprintVersions:
    dialect_known: SqlStatementFingerprint
    engine_unknown: SqlStatementFingerprint


def fingerprint_sql(
    sql: str,
    *,
    placeholder_profile: PlaceholderProfile | str | None = None,
    read: str | None = None,
) -> SqlStatementFingerprint:
    """Return a dialect-known fingerprint, or an engine-unknown one if read=None."""

    dialect = Dialect.get_or_raise(read) if read else None
    if placeholder_profile is None:
        normalized_sql, pattern, source_bindings = sql, (), ()
    else:
        profile = PlaceholderProfile(placeholder_profile)
        normalized_sql, pattern, source_bindings = _normalize_placeholders(
            sql, profile, dialect
        )

    expressions = sqlglot.parse(normalized_sql, read=dialect)
    if len(expressions) != 1 or expressions[0] is None:
        raise UnsupportedStatementError("Expected exactly one SQL statement.")
    expression = expressions[0]
    statement_kind = _statement_kind(expression)

    placeholders = tuple(expression.find_all(exp.Placeholder))
    if len(placeholders) != len(pattern):
        raise ValueError("SQLGlot did not parse every adapted marker as a placeholder.")

    canonical_sql = expression.sql(
        dialect=dialect,
        comments=False,
        pretty=False,
        unsupported_level=ErrorLevel.RAISE,
    )
    certainty = "dialect-known" if dialect else "engine-unknown"
    canonical_dialect = dialect.__class__.__name__.lower() if dialect else "generic"
    payload = json.dumps(
        {
            "algorithm": _KNOWN_ALGORITHM if dialect else _UNKNOWN_ALGORITHM,
            "canonical_dialect": canonical_dialect,
            "canonical_sql": canonical_sql,
            "certainty": certainty,
            "sqlglot_version": sqlglot.__version__,
            "statement_kind": statement_kind,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return SqlStatementFingerprint(
        sha256_hex=sha256(payload.encode()).hexdigest(),
        certainty=certainty,
        canonical_dialect=canonical_dialect,
        statement_kind=statement_kind,
        canonical_sql=canonical_sql,
        binding_pattern=pattern,
        source_bindings=source_bindings,
        fingerprint_payload=payload,
    )


def fingerprint_versions(
    sql: str,
    *,
    read: str,
    placeholder_profile: PlaceholderProfile | str | None = None,
) -> FingerprintVersions:
    """Run the known and unknown SQLGlot interpretations independently."""

    return FingerprintVersions(
        dialect_known=fingerprint_sql(
            sql, read=read, placeholder_profile=placeholder_profile
        ),
        engine_unknown=fingerprint_sql(sql, placeholder_profile=placeholder_profile),
    )


def _normalize_placeholders(
    sql: str,
    profile: PlaceholderProfile,
    dialect: Dialect | None,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Adapt only declared marker spans found by SQLGlot's tokenizer."""

    tokens = sqlglot.tokenize(sql, read=dialect)
    markers = _marker_spans(sql, tokens, profile)
    if not markers:
        raise ValueError(f"No {profile.value!r} placeholders found.")

    canonical_by_binding: dict[tuple[str, object], str] = {}
    edits: list[tuple[int, int, str]] = []
    pattern: list[str] = []
    source_bindings: list[str] = []
    for occurrence, (start, end, marker) in enumerate(markers, start=1):
        anonymous = marker in {"?", "%s"}
        binding = ("occurrence", occurrence) if anonymous else ("marker", marker)
        slot = canonical_by_binding.setdefault(
            binding, f"p{len(canonical_by_binding) + 1}"
        )
        edits.append((start, end, f":__sqlfp_{slot}"))
        pattern.append(slot)
        source_bindings.append(marker)

    normalized = sql
    for start, end, replacement in reversed(edits):
        normalized = normalized[:start] + replacement + normalized[end + 1 :]
    return normalized, tuple(pattern), tuple(source_bindings)


def _marker_spans(
    sql: str,
    tokens: list[Token],
    profile: PlaceholderProfile,
) -> list[tuple[int, int, str]]:
    """Match a profile against contiguous SQLGlot token spans, never comments."""

    marker_pattern = _MARKER_PATTERNS[profile]
    found: list[tuple[int, int, str]] = []
    token_index = 0
    while token_index < len(tokens):
        match: tuple[int, int, str, int] | None = None
        max_width = min(5, len(tokens) - token_index)
        for width in range(max_width, 0, -1):
            end_index = token_index + width - 1
            selected = tokens[token_index : end_index + 1]
            if any(left.end + 1 != right.start for left, right in pairwise(selected)):
                continue
            start, end = selected[0].start, selected[-1].end
            marker = sql[start : end + 1]
            if marker_pattern.fullmatch(marker):
                if (
                    profile is PlaceholderProfile.QMARK
                    and sql[end + 1 : end + 2].isdigit()
                ):
                    continue
                match = start, end, marker, end_index
                break
        if match:
            start, end, marker, end_index = match
            found.append((start, end, marker))
            token_index = end_index + 1
        else:
            token_index += 1
    return found


def _statement_kind(expression: exp.Expr) -> str:
    if isinstance(expression, exp.Query):
        return "SELECT"
    if isinstance(expression, exp.Insert):
        return "INSERT"
    if isinstance(expression, exp.Update):
        return "UPDATE"
    raise UnsupportedStatementError(
        f"SQLGlot produced unsupported {type(expression).__name__}."
    )

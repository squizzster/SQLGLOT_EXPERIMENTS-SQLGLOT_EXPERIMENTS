from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal, cast

from sqlglot import Dialect, exp, parse
from sqlglot.errors import SqlglotError

from sqlglot_experiments.dialect_adapters import generate_target_sql, parsing_dialect
from sqlglot_experiments.source_parameters import (
    ParameterOccurrence,
    ParameterPlanningError,
    source_parameter_structure,
)

StatementType = Literal["SELECT", "INSERT", "UPDATE", "DELETE"]

_ALGORITHM = "sqlglot-experiments/statement-fingerprint/v1"


class FingerprintingError(ValueError):
    """The SQL cannot produce one supported structural fingerprint."""


def fingerprint_statement(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> str:
    """Return a SHA-256 hex digest for the value-independent statement shape."""
    source_dialect = _require_dialect(source_dialect, role="source")
    target_dialect = _require_dialect(target_dialect, role="target")
    try:
        _, occurrences = source_parameter_structure(
            sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        tagged_sql, owned_markers = _tag_source_parameters(sql, occurrences)
        source_ast = _parse_single(
            tagged_sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        statement_type = _statement_type(source_ast)
        shape_ast = _normalize_value_sites(source_ast, owned_markers)
        canonical_sql = generate_target_sql(
            shape_ast,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            comments=False,
        )
        target_ast = _parse_single(
            canonical_sql,
            source_dialect=target_dialect,
            target_dialect=target_dialect,
        )
        if _statement_type(target_ast) != statement_type:
            raise FingerprintingError(
                "target rendering changed the SQL statement type"
            )
    except (ParameterPlanningError, SqlglotError) as error:
        raise FingerprintingError("SQL fingerprinting failed") from error

    payload = json.dumps(
        {
            "algorithm": _ALGORITHM,
            "canonical_sql": canonical_sql,
            "source_dialect": source_dialect,
            "statement_type": statement_type,
            "target_dialect": target_dialect,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode()).hexdigest()


def _require_dialect(dialect: str, *, role: str) -> str:
    if not dialect or not dialect.strip():
        raise FingerprintingError(f"{role}_dialect must be explicit")
    normalized = dialect.strip().lower()
    try:
        Dialect.get_or_raise(normalized)
    except ValueError as error:
        raise FingerprintingError(f"unsupported {role} dialect") from error
    return normalized


def _tag_source_parameters(
    sql: str,
    occurrences: tuple[ParameterOccurrence, ...],
) -> tuple[str, set[str]]:
    marker_prefix = "__sqlglot_experiments_fingerprint_"
    while marker_prefix in sql:
        marker_prefix = f"_{marker_prefix}"

    markers: set[str] = set()
    parts: list[str] = []
    cursor = 0
    for index, occurrence in enumerate(occurrences):
        marker = f"{marker_prefix}{index}"
        markers.add(marker)
        parts.extend((sql[cursor : occurrence.start], f":{marker}"))
        cursor = occurrence.end + 1
    parts.append(sql[cursor:])
    return "".join(parts), markers


def _parse_single(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> exp.Expr:
    read_dialect = parsing_dialect(
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    statements = [
        cast(exp.Expr, statement)
        for statement in parse(sql, read=read_dialect)
        if statement
    ]
    if len(statements) != 1:
        raise FingerprintingError(
            f"expected exactly one SQL statement, received {len(statements)}"
        )
    return statements[0]


def _statement_type(statement: exp.Expr) -> StatementType:
    if isinstance(statement, exp.Query):
        return "SELECT"
    if isinstance(statement, exp.Insert):
        return "INSERT"
    if isinstance(statement, exp.Update):
        return "UPDATE"
    if isinstance(statement, exp.Delete):
        return "DELETE"
    raise FingerprintingError(
        "only SELECT, INSERT, UPDATE, and DELETE statements are supported"
    )


def _normalize_value_sites(
    source_ast: exp.Expr,
    owned_markers: set[str],
) -> exp.Expr:
    shape_ast = source_ast.copy()
    candidates: list[exp.Expr] = []
    for raw_node in shape_ast.walk(bfs=False):
        node = cast(exp.Expr, raw_node)
        if isinstance(node, exp.Parameter):
            raise FingerprintingError("unsupported source placeholder form")
        if isinstance(node, exp.Placeholder):
            marker = node.name
            if marker not in owned_markers:
                raise FingerprintingError("unsupported source placeholder form")
            candidates.append(node)
        elif _is_value_site(node, candidates):
            candidates.append(node)

    for candidate in candidates:
        candidate.replace(exp.Placeholder())
    return shape_ast


def _is_value_site(node: exp.Expr, candidates: list[exp.Expr]) -> bool:
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return True
    if not isinstance(node, (exp.Literal, exp.Boolean, exp.Null)):
        return False
    return not (isinstance(node.parent, exp.Neg) and node.parent in candidates)

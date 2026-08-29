from __future__ import annotations

import json
from hashlib import sha256
from importlib.metadata import version
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
_CANONICALIZER = f"sqlglot/{version('sqlglot')}"


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
        tagged_sql = _tag_source_parameters(sql, occurrences)
        source_ast = _parse_single(
            tagged_sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        statement_type = _statement_type(
            source_ast,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        shape_ast = _normalize_value_sites(
            source_ast,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
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
        if (
            _statement_type(
                target_ast,
                source_dialect=target_dialect,
                target_dialect=target_dialect,
            )
            != statement_type
        ):
            raise FingerprintingError(
                "target rendering changed the SQL statement type"
            )
    except (ParameterPlanningError, SqlglotError) as error:
        raise FingerprintingError("SQL fingerprinting failed") from error

    payload = json.dumps(
        {
            "algorithm": _ALGORITHM,
            "canonicalizer": _CANONICALIZER,
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
) -> str:
    marker_prefix = "__sqlglot_experiments_fingerprint_"
    while marker_prefix in sql:
        marker_prefix = f"_{marker_prefix}"

    parts: list[str] = []
    cursor = 0
    for index, occurrence in enumerate(occurrences):
        marker = f"{marker_prefix}{index}"
        parts.extend((sql[cursor : occurrence.start], f":{marker}"))
        cursor = occurrence.end + 1
    parts.append(sql[cursor:])
    return "".join(parts)


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


def _statement_type(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> StatementType:
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
    *,
    source_dialect: str,
    target_dialect: str,
) -> exp.Expr:
    shape_ast = source_ast.copy()
    candidates: list[exp.Expr] = []
    for raw_node in shape_ast.walk(bfs=False):
        node = cast(exp.Expr, raw_node)
        if isinstance(node, (exp.Parameter, exp.Placeholder)) or _is_value_site(
            node,
            candidates,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        ):
            candidates.append(node)

    for candidate in candidates:
        candidate.replace(exp.Placeholder())
    return shape_ast


def _is_value_site(
    node: exp.Expr,
    candidates: list[exp.Expr],
    *,
    source_dialect: str,
    target_dialect: str,
) -> bool:
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return True
    if not isinstance(node, (exp.Literal, exp.Boolean, exp.Null)):
        return False
    if _is_structural_literal(
        node,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    ):
        return False
    return not (isinstance(node.parent, exp.Neg) and node.parent in candidates)


def _is_structural_literal(
    node: exp.Literal | exp.Boolean | exp.Null,
    *,
    source_dialect: str,
    target_dialect: str,
) -> bool:
    """Keep literals whose AST role identifies SQL structure rather than data."""
    if not isinstance(node, exp.Literal):
        return False
    if node.find_ancestor(exp.DataType) or isinstance(node.parent, exp.PositionalColumn):
        return True
    if not node.is_int:
        return False

    parent = node.parent
    if isinstance(parent, exp.Ordered) and isinstance(parent.parent, exp.Order):
        return True
    if isinstance(parent, exp.Group) and node.arg_key == "expressions":
        return True
    return bool(
        isinstance(parent, exp.Tuple)
        and isinstance(parent.parent, exp.Distinct)
        and parent.arg_key == "on"
    )

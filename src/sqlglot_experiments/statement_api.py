from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypedDict, cast

from sqlglot import Dialect, ErrorLevel, exp, parse
from sqlglot.tokenizer_core import TokenType

Binding = str | int | float | Decimal | bool | None
StatementType = Literal["SELECT", "INSERT", "UPDATE", "DELETE"]


class Analysis(TypedDict):
    hardcoded_value_count: int
    hardcoded_field_count: int


class PreparedStatement(TypedDict):
    dialect: str
    statement_type: StatementType
    sql: str
    bindings: list[Binding]
    analysis: Analysis


class StatementPreparationError(ValueError):
    """The SQL cannot produce one complete execution package."""


class ExistingPlaceholderError(StatementPreparationError):
    """The SQL already has bindings whose values are unavailable here."""


class UnsupportedStatementError(StatementPreparationError):
    """The SQL statement is outside the prototype's DML contract."""


class _Candidate(TypedDict):
    node: exp.Expr
    value: Binding
    field_keys: set[tuple[str, ...]]


_DIRECT_PREDICATES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
)


def prepare_statement(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> PreparedStatement:
    """Lift field-associated literals into target-rendered placeholders."""
    source_dialect = _require_dialect(source_dialect, role="source")
    target_dialect = _require_dialect(target_dialect, role="target")

    source_ast = _parse_single_statement(
        sql,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    statement_type = _statement_type(
        source_ast,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    _reject_existing_placeholders(
        sql,
        source_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    prepared_ast, bindings, field_keys = _lift_hardcoded_values(
        source_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    target_sql = _generate_sql(
        prepared_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

    return {
        "dialect": f"{source_dialect},{target_dialect}",
        "statement_type": statement_type,
        "sql": target_sql,
        "bindings": bindings,
        "analysis": {
            "hardcoded_value_count": len(bindings),
            "hardcoded_field_count": len(field_keys),
        },
    }


def _require_dialect(dialect: str, *, role: str) -> str:
    if not dialect or not dialect.strip():
        raise StatementPreparationError(f"{role}_dialect must be explicit")
    normalized = dialect.strip().lower()
    Dialect.get_or_raise(normalized)
    return normalized


def _parse_single_statement(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> exp.Expr:
    statements = [
        cast(exp.Expr, statement)
        for statement in parse(sql, read=source_dialect)
        if statement
    ]
    if len(statements) != 1:
        raise StatementPreparationError(
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
    raise UnsupportedStatementError(
        "only SELECT, INSERT, UPDATE, and DELETE statements are supported"
    )


def _reject_existing_placeholders(
    sql: str,
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> None:
    ast_has_placeholder = any(
        isinstance(node, (exp.Placeholder, exp.Parameter)) for node in statement.walk()
    )
    sqlite_has_dollar_placeholder = source_dialect == "sqlite" and any(
        token.token_type is TokenType.VAR and token.text.startswith("$")
        for token in Dialect.get_or_raise(source_dialect).tokenize(sql)
    )
    if ast_has_placeholder or sqlite_has_dollar_placeholder:
        raise ExistingPlaceholderError(
            "existing placeholders require caller-supplied bindings, which this "
            "prototype does not yet accept"
        )


def _lift_hardcoded_values(
    source_ast: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> tuple[exp.Expr, list[Binding], set[tuple[str, ...]]]:
    target_ast = source_ast.copy()
    candidates = _find_candidates(
        target_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

    bindings: list[Binding] = []
    field_keys: set[tuple[str, ...]] = set()
    for candidate in candidates:
        bindings.append(candidate["value"])
        field_keys.update(candidate["field_keys"])
        candidate["node"].replace(exp.Placeholder())

    return target_ast, bindings, field_keys


def _find_candidates(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for raw_node in statement.walk(bfs=False):
        node = cast(exp.Expr, raw_node)
        is_bindable, value = _binding_value(
            node,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        if not is_bindable:
            continue

        field_keys = _associated_field_keys(
            node,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        if field_keys is not None:
            candidates.append(
                {
                    "node": node,
                    "value": value,
                    "field_keys": field_keys,
                }
            )
    return candidates


def _binding_value(
    node: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> tuple[bool, Binding]:
    if isinstance(node, (exp.Literal, exp.Boolean, exp.Null)) or (
        isinstance(node, exp.Neg)
        and isinstance(node.this, exp.Literal)
        and not node.this.is_string
    ):
        value = node.to_py()
    else:
        return False, None

    if target_dialect == "sqlite" and isinstance(value, Decimal):
        return True, float(value)
    return True, value


def _associated_field_keys(
    node: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]] | None:
    parent = node.parent
    if isinstance(parent, _DIRECT_PREDICATES):
        if node is parent.this:
            keys = _column_keys(
                parent.expression,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
            return keys or None
        if node is parent.expression:
            keys = _column_keys(
                parent.this,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
            return keys or None

    if isinstance(parent, exp.Between) and node.arg_key in {"low", "high"}:
        keys = _column_keys(
            parent.this,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        return keys or None

    if isinstance(parent, exp.In) and node.arg_key == "expressions":
        keys = _column_keys(
            parent.this,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        return keys or None

    if isinstance(parent, exp.Tuple) and node.arg_key == "expressions":
        return _insert_field_key(
            parent,
            node.index,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )

    return None


def _column_keys(
    expression: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]]:
    return {
        tuple(identifier.name for identifier in column.parts)
        for column in expression.find_all(exp.Column)
    }


def _insert_field_key(
    row: exp.Tuple,
    position: int | None,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]] | None:
    if position is None or not isinstance(row.parent, exp.Values):
        return None
    values = row.parent
    if not isinstance(values.parent, exp.Insert):
        return None
    insert = values.parent

    table_key: tuple[str, ...] = ()
    columns: list[exp.Expr] = []
    if isinstance(insert.this, exp.Schema):
        columns = insert.this.expressions
        if isinstance(insert.this.this, exp.Table):
            table_key = tuple(identifier.name for identifier in insert.this.this.parts)
    elif isinstance(insert.this, exp.Table):
        table_key = tuple(identifier.name for identifier in insert.this.parts)

    if position >= len(columns):
        return set()
    return {(*table_key, columns[position].name)}


def _generate_sql(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> str:
    return statement.sql(
        dialect=target_dialect,
        unsupported_level=ErrorLevel.RAISE,
    )

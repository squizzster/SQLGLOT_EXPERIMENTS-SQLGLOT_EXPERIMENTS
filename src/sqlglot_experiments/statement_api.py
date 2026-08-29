from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypedDict, cast

from sqlglot import Dialect, exp, parse
from sqlglot.errors import ParseError, SqlglotError, TokenError, UnsupportedError

from sqlglot_experiments.api_envelope import (
    ApiFailureEnvelope,
    ApiSuccessEnvelope,
    failure_envelope,
    success_envelope,
)
from sqlglot_experiments.dialect_adapters import generate_target_sql, parsing_dialect
from sqlglot_experiments.source_parameters import (
    Binding,
    InputBindings,
    ParameterPlan,
    ParameterPlanningError,
    plan_source_parameters,
)
from sqlglot_experiments.statement_fingerprinting import fingerprint_statement

StatementType = Literal["SELECT", "INSERT", "UPDATE", "DELETE"]


class Analysis(TypedDict):
    hardcoded_value_count: int
    hardcoded_field_count: int


class PreparedStatement(ApiSuccessEnvelope):
    sql_fingerprint: str
    dialect: list[str]
    statement_type: StatementType
    sql: str
    bindings: list[Binding]
    analysis: Analysis


PreparationResult = PreparedStatement | ApiFailureEnvelope


class StatementPreparationError(ValueError):
    """The SQL cannot produce one complete execution package."""


class BindingCountError(StatementPreparationError):
    """Caller bindings cannot resolve every required source parameter slot."""


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
    bindings: InputBindings | None = None,
    source_dialect: str,
    target_dialect: str,
) -> PreparationResult:
    """Return the library-owned envelope and a payload only when successful."""
    try:
        return _prepare_statement(
            sql,
            bindings=bindings,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    except (StatementPreparationError, SqlglotError) as error:
        return failure_envelope(_failure_reason(error))
    except Exception:  # noqa: BLE001 - the public envelope owns failure disclosure
        return failure_envelope("internal preparation error")


def _prepare_statement(
    sql: str,
    *,
    bindings: InputBindings | None,
    source_dialect: str,
    target_dialect: str,
) -> PreparedStatement:
    source_dialect = _require_dialect(source_dialect, role="source")
    target_dialect = _require_dialect(target_dialect, role="target")
    try:
        parameter_plan = plan_source_parameters(
            sql,
            bindings=bindings,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    except ParameterPlanningError as error:
        raise BindingCountError(str(error)) from error
    source_sql, marker_values = _tag_source_placeholders(
        sql,
        parameter_plan=parameter_plan,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

    source_ast = _parse_single_statement(
        source_sql,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    statement_type = _statement_type(
        source_ast,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    _require_owned_placeholders(
        source_ast,
        marker_values=marker_values,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    prepared_ast, marker_values, field_keys, hardcoded_value_count = (
        _mark_hardcoded_values(
            source_ast,
            marker_values=marker_values,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    )
    merged_bindings = _bindings_in_target_order(
        prepared_ast,
        marker_values=marker_values,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    _make_placeholders_anonymous(
        prepared_ast,
        marker_values=marker_values,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    _require_complete_ast(
        prepared_ast,
        bindings=list(marker_values.values()),
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    target_sql = _generate_sql(
        prepared_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    target_ast = _parse_single_statement(
        target_sql,
        source_dialect=target_dialect,
        target_dialect=target_dialect,
    )
    target_statement_type = _statement_type(
        target_ast,
        source_dialect=target_dialect,
        target_dialect=target_dialect,
    )
    if target_statement_type != statement_type:
        raise StatementPreparationError(
            "target rendering changed the SQL statement type"
        )
    _require_complete_ast(
        target_ast,
        bindings=merged_bindings,
        source_dialect=target_dialect,
        target_dialect=target_dialect,
    )
    sql_fingerprint = fingerprint_statement(
        sql,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

    status = success_envelope(
        warning=(
            _hardcoded_warning(hardcoded_value_count)
            if hardcoded_value_count > 0
            else None
        )
    )
    return {
        **status,
        "sql_fingerprint": sql_fingerprint,
        "dialect": [source_dialect, target_dialect],
        "statement_type": statement_type,
        "sql": target_sql,
        "bindings": merged_bindings,
        "analysis": {
            "hardcoded_value_count": hardcoded_value_count,
            "hardcoded_field_count": len(field_keys),
        },
    }


def _hardcoded_warning(count: int) -> str:
    value_word = "value" if count == 1 else "values"
    placeholder_word = "placeholder" if count == 1 else "placeholders"
    return f"replaced {count} hardcoded {value_word} with {placeholder_word}"


def _failure_reason(error: StatementPreparationError | SqlglotError) -> str:
    if isinstance(error, BindingCountError):
        reason = f"bindings: {error}"
    elif isinstance(error, UnsupportedStatementError):
        reason = f"unsupported statement: {error}"
    elif isinstance(error, ParseError):
        reason = "invalid SQL syntax"
    elif isinstance(error, TokenError):
        reason = "invalid SQL tokens"
    elif isinstance(error, UnsupportedError):
        reason = "target dialect cannot render the statement"
    elif isinstance(error, StatementPreparationError):
        reason = str(error)
    else:
        reason = "SQLGlot processing failed"
    return reason


def _require_dialect(dialect: str, *, role: str) -> str:
    if not dialect or not dialect.strip():
        raise StatementPreparationError(f"{role}_dialect must be explicit")
    normalized = dialect.strip().lower()
    try:
        Dialect.get_or_raise(normalized)
    except ValueError as error:
        raise StatementPreparationError(
            f"unsupported {role} dialect: {normalized}"
        ) from error
    return normalized


def _parse_single_statement(
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


def _tag_source_placeholders(
    sql: str,
    *,
    parameter_plan: ParameterPlan,
    source_dialect: str,
    target_dialect: str,
) -> tuple[str, dict[str, Binding]]:
    marker_prefix = _unused_marker_prefix(sql, kind="input")
    marker_values: dict[str, Binding] = {}
    target_slot_values: dict[int, Binding] = {}
    parts: list[str] = []
    cursor = 0
    for index, (occurrence, value) in enumerate(
        zip(
            parameter_plan.occurrences,
            parameter_plan.occurrence_values,
            strict=True,
        )
    ):
        marker = f"{marker_prefix}{index}"
        if occurrence.slot_number not in target_slot_values:
            target_slot_values[occurrence.slot_number] = _target_binding_value(
                value,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
        marker_values[marker] = target_slot_values[occurrence.slot_number]
        parts.extend((sql[cursor : occurrence.start], f":{marker}"))
        cursor = occurrence.end + 1
    parts.append(sql[cursor:])
    return "".join(parts), marker_values


def _unused_marker_prefix(sql: str, *, kind: str) -> str:
    marker_prefix = f"__sqlglot_experiments_{kind}_"
    while marker_prefix in sql:
        marker_prefix = f"_{marker_prefix}"
    return marker_prefix


def _require_owned_placeholders(
    statement: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> None:
    for raw_node in statement.walk():
        node = cast(exp.Expr, raw_node)
        if isinstance(node, exp.Parameter):
            raise StatementPreparationError("unsupported source placeholder form")
        if isinstance(node, exp.Placeholder):
            marker = _placeholder_name(node)
            if marker not in marker_values:
                raise StatementPreparationError("unsupported source placeholder form")


def _mark_hardcoded_values(
    source_ast: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> tuple[exp.Expr, dict[str, Binding], set[tuple[str, ...]], int]:
    target_ast = source_ast.copy()
    candidates = _find_candidates(
        target_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    marker_values = marker_values.copy()
    marker_prefix = _unused_marker_prefix(
        " ".join(
            (
                _generate_sql(
                    target_ast,
                    source_dialect=source_dialect,
                    target_dialect=source_dialect,
                ),
                *marker_values,
            )
        ),
        kind="literal",
    )
    field_keys: set[tuple[str, ...]] = set()
    for index, candidate in enumerate(candidates):
        marker = f"{marker_prefix}{index}"
        marker_values[marker] = candidate["value"]
        field_keys.update(candidate["field_keys"])
        candidate["node"].replace(exp.Placeholder(this=marker))

    return target_ast, marker_values, field_keys, len(candidates)


def _bindings_in_target_order(
    statement: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> list[Binding]:
    marked_sql = _generate_sql(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    marker_order = [
        token.text
        for token in Dialect.get_or_raise(target_dialect).tokenize(marked_sql)
        if token.text in marker_values
    ]
    if set(marker_order) != set(marker_values):
        raise StatementPreparationError("target rendering lost a binding marker")
    return [marker_values[marker] for marker in marker_order]


def _make_placeholders_anonymous(
    statement: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> None:
    for raw_node in list(statement.walk()):
        node = cast(exp.Expr, raw_node)
        if (
            isinstance(node, exp.Placeholder)
            and _placeholder_name(node) in marker_values
        ):
            node.replace(exp.Placeholder())


def _require_complete_ast(
    statement: exp.Expr,
    *,
    bindings: list[Binding],
    source_dialect: str,
    target_dialect: str,
) -> None:
    placeholder_count = sum(
        isinstance(node, (exp.Placeholder, exp.Parameter)) for node in statement.walk()
    )
    if placeholder_count != len(bindings):
        raise StatementPreparationError(
            "target SQL placeholder count does not match returned bindings"
        )


def _placeholder_name(placeholder: exp.Placeholder) -> str | None:
    name = placeholder.this
    if isinstance(name, exp.Identifier):
        return name.name
    return name if isinstance(name, str) else None


def _target_binding_value(
    value: Binding,
    *,
    source_dialect: str,
    target_dialect: str,
) -> Binding:
    if target_dialect == "sqlite" and isinstance(value, Decimal):
        try:
            return float(value)
        except (OverflowError, ValueError) as error:
            raise StatementPreparationError(
                "binding cannot be represented by the SQLite target"
            ) from error
    return value


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

    return True, _target_binding_value(
        value,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


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
    return generate_target_sql(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

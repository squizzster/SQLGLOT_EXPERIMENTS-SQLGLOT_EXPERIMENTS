"""Authoritative statement-family identity and structural checks."""

from __future__ import annotations

from typing import Literal

from sqlglot import exp

from sqlglot_experiments.statement_dialect_policy import (
    MERGE_DIALECTS,
    REPLACE_DIALECTS,
)

StatementType = Literal[
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "REPLACE",
]


class StatementClassificationError(ValueError):
    """A recognised extended statement has no safe complete structure."""


def is_replace_statement(statement: exp.Expr) -> bool:
    """Return whether an INSERT-family AST retains REPLACE identity."""
    return isinstance(statement, exp.Insert) and (
        str(statement.args.get("alternative", "")).upper() == "REPLACE"
    )


def extended_statement_type(statement: exp.Expr) -> StatementType | None:
    """Return the extended public family without manufacturing a WITH type."""
    if is_replace_statement(statement):
        return "REPLACE"
    if isinstance(statement, exp.Query):
        return "SELECT"
    if isinstance(statement, exp.Insert):
        return "INSERT"
    if isinstance(statement, exp.Update):
        return "UPDATE"
    if isinstance(statement, exp.Delete):
        return "DELETE"
    if isinstance(statement, exp.Merge):
        return "MERGE"
    return None


def require_extended_statement_type(
    statement: exp.Expr,
    *,
    dialect: str,
) -> StatementType:
    """Return and structurally validate one extended statement family."""
    statement_type = extended_statement_type(statement)
    if statement_type is None:
        raise StatementClassificationError(
            "statement is outside the extended preparation pipeline"
        )
    for node in statement.walk():
        if is_replace_statement(node):
            _require_complete_replace(node, dialect=dialect)
        elif isinstance(node, exp.Merge):
            _require_complete_merge(node, dialect=dialect)
    return statement_type


def _require_complete_replace(statement: exp.Expr, *, dialect: str) -> None:
    if dialect not in REPLACE_DIALECTS:
        raise StatementClassificationError(
            f"{dialect} dialect does not support REPLACE"
        )
    if not isinstance(statement, exp.Insert):
        raise StatementClassificationError("REPLACE structure is invalid")
    if statement.args.get("ignore") or statement.args.get("conflict"):
        raise StatementClassificationError("REPLACE clause is invalid")
    target = statement.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table) or not target.name:
        raise StatementClassificationError("REPLACE target table is required")
    if not any(
        (
            isinstance(statement.expression, exp.Expr),
            bool(statement.args.get("default")),
            isinstance(statement.args.get("source"), exp.Expr),
        )
    ):
        raise StatementClassificationError("REPLACE source values are required")
    if dialect == "mysql" and isinstance(statement.args.get("with_"), exp.With):
        raise StatementClassificationError(
            "MySQL REPLACE requires WITH on its SELECT source"
        )
    if dialect == "mysql" and statement.meta.get("invalid_mysql_replace_set"):
        raise StatementClassificationError("MySQL REPLACE clause is invalid")
    if dialect == "mysql" and statement.meta.get("replace_set"):
        target_schema = statement.this
        values = statement.expression
        rows = values.expressions if isinstance(values, exp.Values) else []
        if (
            not isinstance(target_schema, exp.Schema)
            or len(rows) != 1
            or not isinstance(rows[0], exp.Tuple)
            or not target_schema.expressions
            or len(target_schema.expressions) != len(rows[0].expressions)
        ):
            raise StatementClassificationError("MySQL REPLACE SET clause is incomplete")
    if dialect == "sqlite" and (
        statement.meta.get("invalid_sqlite_replace_set")
        or statement.args.get("source")
        or statement.args.get("partition")
    ):
        raise StatementClassificationError("SQLite REPLACE clause is invalid")


def _require_complete_merge(statement: exp.Merge, *, dialect: str) -> None:
    if dialect not in MERGE_DIALECTS:
        raise StatementClassificationError(
            f"{dialect} dialect has no configured MERGE support"
        )
    target = statement.this
    if not isinstance(target, exp.Table) or not target.name:
        raise StatementClassificationError("MERGE target table is required")

    using = statement.args.get("using")
    if not isinstance(using, exp.Expr):
        raise StatementClassificationError("MERGE source is required")

    on = statement.args.get("on")
    using_cond = statement.args.get("using_cond")
    if not isinstance(on, exp.Expr) and not using_cond:
        raise StatementClassificationError("MERGE match condition is required")
    if using_cond and dialect != "duckdb":
        raise StatementClassificationError(
            f"{dialect} dialect does not support MERGE USING columns"
        )
    if statement.args.get("returning") and dialect not in {
        "duckdb",
        "postgres",
        "tsql",
    }:
        raise StatementClassificationError(
            f"{dialect} dialect does not support MERGE result projection"
        )

    whens = statement.args.get("whens")
    if not isinstance(whens, exp.Whens) or not whens.expressions:
        raise StatementClassificationError("MERGE action is required")

    for when in whens.expressions:
        if not isinstance(when, exp.When):
            raise StatementClassificationError("MERGE action is invalid")
        action = when.args.get("then")
        matched = bool(when.args.get("matched"))
        by_source = bool(when.args.get("source"))
        if matched and (by_source or when.meta.get("merge_by_target")):
            raise StatementClassificationError("MERGE match mode is invalid")
        if by_source and dialect not in {
            "bigquery",
            "databricks",
            "duckdb",
            "postgres",
            "tsql",
        }:
            raise StatementClassificationError(
                f"{dialect} dialect does not support MERGE BY SOURCE"
            )
        if isinstance(action, exp.Insert):
            if matched or by_source:
                raise StatementClassificationError("MERGE INSERT action is invalid")
            _require_complete_merge_insert(action, dialect=dialect)
            continue
        if isinstance(action, exp.Update):
            if not matched and not by_source:
                raise StatementClassificationError("MERGE UPDATE action is invalid")
            _require_complete_merge_update(action, dialect=dialect)
            continue
        if isinstance(action, exp.Var):
            action_name = action.name.upper()
            if action_name == "DO NOTHING" and dialect in {"duckdb", "postgres"}:
                continue
            if action_name == "DELETE" and (matched or by_source):
                continue
        raise StatementClassificationError("MERGE action is invalid")


def _require_complete_merge_insert(statement: exp.Insert, *, dialect: str) -> None:
    if statement.args.get("where") and dialect != "oracle":
        raise StatementClassificationError("MERGE INSERT condition is invalid")
    columns = statement.this
    values = statement.expression
    if statement.args.get("default") and dialect == "postgres":
        return
    if isinstance(columns, exp.Star):
        syntax = statement.meta.get("merge_syntax")
        if dialect == "snowflake" and syntax == "snowflake_all_by_name":
            return
        if dialect == "duckdb" and syntax == "duckdb_insert_by_name":
            return
        if dialect == "databricks" and syntax is None:
            return
        raise StatementClassificationError("MERGE INSERT action is invalid")
    if (
        dialect == "bigquery"
        and isinstance(columns, exp.Var)
        and columns.name.upper() == "ROW"
    ):
        return
    if columns is None and isinstance(values, exp.Tuple) and values.expressions:
        return
    if (
        isinstance(columns, exp.Tuple)
        and isinstance(values, exp.Tuple)
        and columns.expressions
        and len(columns.expressions) == len(values.expressions)
    ):
        return
    if (
        dialect == "duckdb"
        and columns is None
        and not isinstance(values, exp.Expr)
    ):
        return
    raise StatementClassificationError("MERGE INSERT action is incomplete")


def _require_complete_merge_update(statement: exp.Update, *, dialect: str) -> None:
    if statement.args.get("where") and dialect != "oracle":
        raise StatementClassificationError("MERGE UPDATE condition is invalid")
    expressions = statement.args.get("expressions")
    if isinstance(expressions, exp.Star):
        if (
            dialect == "snowflake"
            and statement.meta.get("merge_syntax") == "snowflake_all_by_name"
        ):
            return
        raise StatementClassificationError("MERGE UPDATE action is invalid")
    if (
        dialect == "databricks"
        and isinstance(expressions, list)
        and len(expressions) == 1
        and isinstance(expressions[0], exp.Star)
    ):
        return
    if isinstance(expressions, list) and expressions:
        if not all(isinstance(expression, exp.EQ) for expression in expressions):
            raise StatementClassificationError("MERGE UPDATE assignment is invalid")
        return
    if dialect == "duckdb" and not expressions:
        return
    raise StatementClassificationError("MERGE UPDATE action is incomplete")


def statement_semantic_signature(statement: exp.Expr) -> tuple[object, ...]:
    """Return schema-free semantics that target rendering must preserve."""
    replaces: list[tuple[object, ...]] = []
    merges: list[tuple[object, ...]] = []
    for node in statement.walk():
        if is_replace_statement(node) and isinstance(node, exp.Insert):
            replaces.append(_replace_signature(node))
        elif isinstance(node, exp.Merge):
            merges.append(_merge_signature(node))
    return (tuple(replaces), tuple(merges))


def _replace_signature(statement: exp.Insert) -> tuple[object, ...]:
    expression = statement.expression
    expression_kind = (
        "values"
        if isinstance(expression, exp.Values)
        else "query"
        if isinstance(expression, exp.Query)
        else type(expression).__name__
        if isinstance(expression, exp.Expr)
        else None
    )
    nested_with = (
        expression.args.get("with_") if isinstance(expression, exp.Expr) else None
    )
    return (
        expression_kind,
        statement.meta.get("replace_modifier"),
        bool(statement.meta.get("replace_set")),
        bool(statement.args.get("default")),
        bool(statement.args.get("source")),
        bool(statement.args.get("returning")),
        bool(statement.args.get("with_") or nested_with),
        bool(statement.args.get("partition")),
    )


def _merge_signature(statement: exp.Merge) -> tuple[object, ...]:
    whens = statement.args.get("whens")
    actions = (
        tuple(_merge_when_signature(when) for when in whens.expressions)
        if isinstance(whens, exp.Whens)
        else ()
    )
    using_cond = statement.args.get("using_cond")
    return (
        "on" if isinstance(statement.args.get("on"), exp.Expr) else "using",
        len(using_cond) if isinstance(using_cond, list) else 0,
        bool(statement.args.get("returning")),
        bool(statement.args.get("with_")),
        actions,
    )


def _merge_when_signature(statement: exp.When) -> tuple[object, ...]:
    action = statement.args.get("then")
    if isinstance(action, exp.Update):
        expressions = action.args.get("expressions")
        is_star = isinstance(expressions, exp.Star) or (
            isinstance(expressions, list)
            and len(expressions) == 1
            and isinstance(expressions[0], exp.Star)
        )
        action_signature: tuple[object, ...] = (
            "update",
            action.meta.get("merge_syntax"),
            "star" if is_star else "assignments",
            len(expressions) if isinstance(expressions, list) else 0,
            bool(action.args.get("where")),
        )
    elif isinstance(action, exp.Insert):
        columns = action.this
        values = action.expression
        action_signature = (
            "insert",
            action.meta.get("merge_syntax"),
            "star"
            if isinstance(columns, exp.Star)
            else "row"
            if isinstance(columns, exp.Var)
            else "columns"
            if isinstance(columns, exp.Tuple)
            else "implicit",
            len(columns.expressions) if isinstance(columns, exp.Tuple) else 0,
            len(values.expressions) if isinstance(values, exp.Tuple) else 0,
            bool(action.args.get("default")),
            bool(action.args.get("where")),
        )
    elif isinstance(action, exp.Var):
        action_signature = (action.name.upper(),)
    else:
        action_signature = (type(action).__name__,)
    return (
        bool(statement.args.get("matched")),
        bool(statement.args.get("source")),
        bool(statement.args.get("condition")),
        action_signature,
    )

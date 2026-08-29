"""Reliable physical-table fields referenced beneath WHERE nodes."""

from __future__ import annotations

from typing import TypedDict

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, find_all_in_scope, traverse_scope


class WhereField(TypedDict):
    database: str | None
    table: str
    field: str


def extract_qualified_where_fields(statement: exp.Expr) -> list[WhereField]:
    """Return distinct qualified WHERE fields that resolve to physical tables."""
    scope_by_column = {
        column_id: scope
        for scope in traverse_scope(statement)
        for column_id in scope.column_index
    }
    dml_sources = _dml_sources(statement)
    seen: set[tuple[str | None, str, str]] = set()
    fields: list[WhereField] = []

    for where in statement.find_all(exp.Where):
        for column in where.find_all(exp.Column):
            if column.find_ancestor(exp.Where) is not where or not column.table:
                continue
            table = _physical_source(
                column.table,
                scope=scope_by_column.get(id(column)),
                dml_sources=dml_sources,
            )
            if table is None or not table.name:
                continue

            identity = (table.db or None, table.name, column.name)
            if identity in seen:
                continue
            seen.add(identity)
            fields.append(
                {
                    "database": table.db or None,
                    "table": table.name,
                    "field": column.name,
                }
            )

    return fields


def _physical_source(
    qualifier: str,
    *,
    scope: Scope | None,
    dml_sources: dict[str, exp.Table],
) -> exp.Table | None:
    while scope is not None:
        source = scope.sources.get(qualifier)
        if source is not None:
            return source if isinstance(source, exp.Table) else None
        scope = scope.parent
    return dml_sources.get(qualifier)


def _dml_sources(statement: exp.Expr) -> dict[str, exp.Table]:
    if not isinstance(statement, (exp.Update, exp.Delete)):
        return {}

    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    return {
        table.alias_or_name: table
        for table in find_all_in_scope(statement, exp.Table)
        if table.name and table.name not in cte_names
    }

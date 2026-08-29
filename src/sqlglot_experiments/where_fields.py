"""Reliable physical-table fields referenced beneath WHERE nodes."""

from __future__ import annotations

from typing import TypedDict

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, find_all_in_scope, traverse_scope


class WhereField(TypedDict):
    database: str | None
    table: str | None
    field: str


def extract_where_fields(statement: exp.Expr) -> list[WhereField]:
    """Return every distinct WHERE field with physical source data when known."""
    scope_by_column = {
        column_id: scope
        for scope in traverse_scope(statement)
        for column_id in scope.column_index
    }
    dml_sources = _dml_sources(statement)
    seen: set[tuple[str | None, str | None, str]] = set()
    fields: list[WhereField] = []

    for where in statement.find_all(exp.Where):
        for column in where.find_all(exp.Column):
            if column.find_ancestor(exp.Where) is not where:
                continue
            table = _physical_source(
                column,
                scope=scope_by_column.get(id(column)),
                dml_sources=dml_sources,
            )
            database_name = (table.db or None) if table is not None else None
            table_name = (table.name or None) if table is not None else None
            identity = (database_name, table_name, column.name)
            if identity in seen:
                continue
            seen.add(identity)
            fields.append(
                {
                    "database": database_name,
                    "table": table_name,
                    "field": column.name,
                }
            )

    return fields


def _physical_source(
    column: exp.Column,
    *,
    scope: Scope | None,
    dml_sources: dict[str, exp.Table],
) -> exp.Table | None:
    qualifier = column.table
    if qualifier:
        while scope is not None:
            source = scope.sources.get(qualifier)
            if source is not None:
                return source if isinstance(source, exp.Table) else None
            scope = scope.parent
        return dml_sources.get(qualifier)

    if scope is not None:
        if scope.can_be_correlated:
            return None
        sources = [source for _, source in scope.selected_sources.values()]
        if len(sources) == 1 and isinstance(sources[0], exp.Table):
            return sources[0]
        return None

    if len(dml_sources) == 1:
        return next(iter(dml_sources.values()))
    return None


def _dml_sources(statement: exp.Expr) -> dict[str, exp.Table]:
    if not isinstance(statement, (exp.Update, exp.Delete)):
        return {}

    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    return {
        table.alias_or_name: table
        for table in find_all_in_scope(statement, exp.Table)
        if table.name and table.name not in cte_names
    }

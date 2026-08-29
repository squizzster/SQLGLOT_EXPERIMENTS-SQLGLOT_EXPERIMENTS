"""Conservative WHERE-field extraction from SQLGlot ASTs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal, TypedDict, cast

from sqlglot import exp, parse
from sqlglot.optimizer.scope import Scope, traverse_scope

from sqlglot_experiments.source_parameters import source_parameter_structure

Certainty = Literal["observed", "inferred", "unresolved"]


class FieldReference(TypedDict):
    statement_index: int
    where_index: int
    column_sql: str
    field: str
    written_catalog: str | None
    written_database: str | None
    written_table: str | None
    catalog: str | None
    database: str | None
    table: str | None
    source_alias: str | None
    certainty: Certainty
    resolution: str


class StatementExtraction(TypedDict):
    statement_index: int
    statement_type: str
    where_clause_count: int
    field_reference_count: int
    oracle_field_reference_count: int
    field_capture_mismatch_count: int
    scope_count: int
    scope_error_count: int
    fields: list[FieldReference]


class ExtractionResult(TypedDict):
    statement_count: int
    where_clause_count: int
    field_reference_count: int
    oracle_field_reference_count: int
    field_capture_mismatch_count: int
    scope_error_count: int
    statements: list[StatementExtraction]


class _ResolvedSource(TypedDict):
    catalog: str | None
    database: str | None
    table: str | None
    source_alias: str | None
    certainty: Certainty
    resolution: str


def extract_where_fields(sql: str, *, dialect: str) -> ExtractionResult:
    """Extract syntactic column references owned by every WHERE node."""
    parseable_sql = _normalize_source_parameters(sql, dialect=dialect)
    statements = [
        cast(exp.Expr, statement) for statement in parse(parseable_sql, read=dialect)
    ]
    extractions = [
        _extract_statement(statement, statement_index=index, dialect=dialect)
        for index, statement in enumerate(statements)
    ]
    return {
        "statement_count": len(extractions),
        "where_clause_count": sum(
            extraction["where_clause_count"] for extraction in extractions
        ),
        "field_reference_count": sum(
            extraction["field_reference_count"] for extraction in extractions
        ),
        "oracle_field_reference_count": sum(
            extraction["oracle_field_reference_count"] for extraction in extractions
        ),
        "field_capture_mismatch_count": sum(
            extraction["field_capture_mismatch_count"] for extraction in extractions
        ),
        "scope_error_count": sum(
            extraction["scope_error_count"] for extraction in extractions
        ),
        "statements": extractions,
    }


def _extract_statement(
    statement: exp.Expr,
    *,
    statement_index: int,
    dialect: str,
) -> StatementExtraction:
    where_nodes = list(statement.find_all(exp.Where))
    where_indexes = {id(where): index for index, where in enumerate(where_nodes)}
    scope_by_column, scopes, scope_error_count = _column_scopes(statement)

    candidate_pairs: set[tuple[int, int]] = set()
    fields: list[FieldReference] = []
    for where in where_nodes:
        where_index = where_indexes[id(where)]
        for column in where.find_all(exp.Column):
            if column.find_ancestor(exp.Where) is not where:
                continue
            candidate_pairs.add((id(where), id(column)))
            fields.append(
                _field_reference(
                    column,
                    statement=statement,
                    statement_index=statement_index,
                    where_index=where_index,
                    scope=scope_by_column.get(id(column)),
                    dialect=dialect,
                )
            )

    oracle_pairs = {
        (id(where), id(column))
        for column in statement.find_all(exp.Column)
        if (where := column.find_ancestor(exp.Where)) is not None
    }

    return {
        "statement_index": statement_index,
        "statement_type": _statement_type(statement),
        "where_clause_count": len(where_nodes),
        "field_reference_count": len(candidate_pairs),
        "oracle_field_reference_count": len(oracle_pairs),
        "field_capture_mismatch_count": len(candidate_pairs ^ oracle_pairs),
        "scope_count": len(scopes),
        "scope_error_count": scope_error_count,
        "fields": fields,
    }


def _column_scopes(
    statement: exp.Expr,
) -> tuple[dict[int, Scope], list[Scope], int]:
    try:
        scopes = traverse_scope(statement)
        return (
            {
                column_id: scope
                for scope in scopes
                for column_id in scope.column_index
            },
            scopes,
            0,
        )
    except Exception:  # noqa: BLE001 - failure is measured experiment evidence
        return {}, [], 1


def _field_reference(
    column: exp.Column,
    *,
    statement: exp.Expr,
    statement_index: int,
    where_index: int,
    scope: Scope | None,
    dialect: str,
) -> FieldReference:
    resolved = _resolve_source(column, statement=statement, scope=scope)
    return {
        "statement_index": statement_index,
        "where_index": where_index,
        "column_sql": column.sql(dialect=dialect),
        "field": column.name,
        "written_catalog": column.catalog or None,
        "written_database": column.db or None,
        "written_table": column.table or None,
        **resolved,
    }


def _resolve_source(
    column: exp.Column,
    *,
    statement: exp.Expr,
    scope: Scope | None,
) -> _ResolvedSource:
    qualifier = column.table or None
    if qualifier:
        for candidate_scope, correlation in _scope_chain(scope):
            source = candidate_scope.sources.get(qualifier)
            if source is None:
                continue
            return _source_result(
                source,
                alias=qualifier,
                certainty="observed",
                resolution=(
                    "correlated_qualified_source"
                    if correlation
                    else "qualified_source"
                ),
            )

        dml_source = _dml_sources(statement).get(qualifier)
        if dml_source is not None:
            return _table_result(
                dml_source,
                alias=qualifier,
                certainty="observed",
                resolution="qualified_dml_source",
            )

        return {
            "catalog": column.catalog or None,
            "database": column.db or None,
            "table": column.table or None,
            "source_alias": qualifier,
            "certainty": "unresolved",
            "resolution": "unresolved_qualified_source",
        }

    if scope is not None:
        source_items = _selected_source_items(scope)
        if len(source_items) == 1:
            alias, source = source_items[0]
            return _source_result(
                source,
                alias=alias,
                certainty="inferred",
                resolution="single_scope_source",
            )
        if len(source_items) > 1:
            return _unresolved("ambiguous_scope_sources")

        for parent, _ in _scope_chain(scope.parent):
            parent_items = _selected_source_items(parent)
            if len(parent_items) == 1:
                alias, source = parent_items[0]
                return _source_result(
                    source,
                    alias=alias,
                    certainty="inferred",
                    resolution="single_outer_scope_source",
                )
            if len(parent_items) > 1:
                return _unresolved("ambiguous_outer_scope_sources")

    dml_sources = _dml_sources(statement)
    if len(dml_sources) == 1:
        alias, table = next(iter(dml_sources.items()))
        return _table_result(
            table,
            alias=alias,
            certainty="inferred",
            resolution="single_dml_source",
        )
    if len(dml_sources) > 1:
        return _unresolved("ambiguous_dml_sources")
    return _unresolved("no_source_context")


def _scope_chain(scope: Scope | None) -> Iterator[tuple[Scope, bool]]:
    correlated = False
    while scope is not None:
        yield scope, correlated
        correlated = True
        scope = scope.parent


def _selected_source_items(scope: Scope) -> list[tuple[str, exp.Table | Scope]]:
    return [
        (alias, source)
        for alias, (_, source) in scope.selected_sources.items()
    ]


def _source_result(
    source: exp.Table | Scope,
    *,
    alias: str,
    certainty: Certainty,
    resolution: str,
) -> _ResolvedSource:
    if isinstance(source, exp.Table):
        return _table_result(
            source,
            alias=alias,
            certainty=certainty,
            resolution=resolution,
        )
    return {
        "catalog": None,
        "database": None,
        "table": None,
        "source_alias": alias,
        "certainty": "unresolved",
        "resolution": f"{resolution}_is_derived",
    }


def _table_result(
    table: exp.Table,
    *,
    alias: str,
    certainty: Certainty,
    resolution: str,
) -> _ResolvedSource:
    if not table.name:
        return {
            "catalog": None,
            "database": None,
            "table": None,
            "source_alias": alias,
            "certainty": "unresolved",
            "resolution": f"{resolution}_has_no_physical_table",
        }
    return {
        "catalog": table.catalog or None,
        "database": table.db or None,
        "table": table.name or None,
        "source_alias": alias,
        "certainty": certainty,
        "resolution": resolution,
    }


def _unresolved(resolution: str) -> _ResolvedSource:
    return {
        "catalog": None,
        "database": None,
        "table": None,
        "source_alias": None,
        "certainty": "unresolved",
        "resolution": resolution,
    }


def _dml_sources(statement: exp.Expr) -> dict[str, exp.Table]:
    if not isinstance(statement, (exp.Update, exp.Delete)):
        return {}

    sources: dict[str, exp.Table] = {}
    target = statement.this
    if isinstance(target, exp.Table):
        sources[target.alias_or_name] = target

    from_ = statement.args.get("from_")
    if isinstance(from_, exp.From) and isinstance(from_.this, exp.Table):
        sources[from_.this.alias_or_name] = from_.this

    using = statement.args.get("using")
    using_nodes: list[Any] = using if isinstance(using, list) else [using]
    for node in using_nodes:
        if isinstance(node, exp.Table):
            sources[node.alias_or_name] = node
    return sources


def _statement_type(statement: exp.Expr) -> str:
    if isinstance(statement, exp.Query):
        return "SELECT"
    if isinstance(statement, exp.Insert):
        return "INSERT"
    if isinstance(statement, exp.Update):
        return "UPDATE"
    if isinstance(statement, exp.Delete):
        return "DELETE"
    return type(statement).__name__.upper()


def _normalize_source_parameters(sql: str, *, dialect: str) -> str:
    _, occurrences = source_parameter_structure(
        sql,
        source_dialect=dialect,
        target_dialect=dialect,
    )
    if not occurrences:
        return sql

    parts: list[str] = []
    cursor = 0
    for occurrence in occurrences:
        parts.extend((sql[cursor : occurrence.start], "?"))
        cursor = occurrence.end + 1
    parts.append(sql[cursor:])
    return "".join(parts)

"""Probe MERGE preparation and the real AST role of WITH."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import patch

from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope

from sqlglot_experiments import (
    PreparedStatement,
    prepare_statement,
    statement_api,
    statement_fingerprinting,
    where_fields,
)


def _experimental_extended_statement_type(statement: exp.Expr) -> str | None:
    if isinstance(statement, exp.Merge):
        return "MERGE"
    return _ORIGINAL_EXTENDED_STATEMENT_TYPE(statement)


def _experimental_api_statement_type(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> str:
    if isinstance(statement, exp.Merge):
        return "MERGE"
    return _ORIGINAL_API_STATEMENT_TYPE(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


def _experimental_fingerprint_statement_type(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> str:
    if isinstance(statement, exp.Merge):
        return "MERGE"
    return _ORIGINAL_FINGERPRINT_STATEMENT_TYPE(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


def _experimental_insert_field_key(
    row: exp.Tuple,
    position: int | None,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]] | None:
    """Associate literals in a MERGE INSERT action with its target columns."""
    existing = _ORIGINAL_INSERT_FIELD_KEY(
        row,
        position,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    if existing is not None:
        return existing
    if position is None or not isinstance(row.parent, exp.Insert):
        return None

    insert_action = row.parent
    if row is not insert_action.expression:
        return None
    merge = insert_action.find_ancestor(exp.Merge)
    columns = insert_action.this
    if not isinstance(merge, exp.Merge) or not isinstance(columns, exp.Tuple):
        return None
    if position >= len(columns.expressions):
        return set()

    target = merge.this
    table_key = (
        tuple(identifier.name for identifier in target.parts)
        if isinstance(target, exp.Table)
        else ()
    )
    return {(*table_key, columns.expressions[position].name)}


def _experimental_extract_where_fields(
    statement: exp.Expr,
    *,
    source_dialect: str,
) -> list[str]:
    """Include query scopes nested beneath a MERGE root."""
    if not isinstance(statement, exp.Merge):
        return _ORIGINAL_EXTRACT_WHERE_FIELDS(
            statement,
            source_dialect=source_dialect,
        )

    normalized = normalize_identifiers(
        statement.copy(),
        dialect=source_dialect,
    )
    scope_by_column = {
        column_id: scope
        for query in normalized.find_all(exp.Query)
        for scope in traverse_scope(query)
        for column_id in scope.column_index
    }
    seen: set[tuple[str | None, str | None, str]] = set()
    fields: list[str] = []
    for where in normalized.find_all(exp.Where):
        for column in where.find_all(exp.Column):
            if column.find_ancestor(exp.Where) is not where:
                continue
            table = where_fields._physical_source(
                column,
                scope=scope_by_column.get(id(column)),
                dml_sources={},
            )
            identity, field_name = where_fields._field_identity_and_name(
                column,
                table=table,
                source_dialect=source_dialect,
            )
            if identity in seen:
                continue
            seen.add(identity)
            fields.append(field_name)
    return fields


_ORIGINAL_EXTENDED_STATEMENT_TYPE = statement_api._extended_statement_type
_ORIGINAL_API_STATEMENT_TYPE = statement_api._statement_type
_ORIGINAL_FINGERPRINT_STATEMENT_TYPE = statement_fingerprinting._statement_type
_ORIGINAL_INSERT_FIELD_KEY = statement_api._insert_field_key
_ORIGINAL_EXTRACT_WHERE_FIELDS = statement_api.extract_where_fields


@contextmanager
def merge_support() -> Iterator[None]:
    """Temporarily add MERGE classification and INSERT-action value mapping."""
    statement_api._prepare_statement_structure.cache_clear()
    try:
        with (
            patch.object(
                statement_api,
                "_extended_statement_type",
                side_effect=_experimental_extended_statement_type,
            ),
            patch.object(
                statement_api,
                "_statement_type",
                side_effect=_experimental_api_statement_type,
            ),
            patch.object(
                statement_fingerprinting,
                "_statement_type",
                side_effect=_experimental_fingerprint_statement_type,
            ),
            patch.object(
                statement_api,
                "_insert_field_key",
                side_effect=_experimental_insert_field_key,
            ),
            patch.object(
                statement_api,
                "extract_where_fields",
                side_effect=_experimental_extract_where_fields,
            ),
        ):
            yield
    finally:
        statement_api._prepare_statement_structure.cache_clear()


@dataclass(frozen=True)
class Case:
    name: str
    sql: str
    source_dialect: str = "postgres"
    target_dialect: str = "postgres"
    bindings: Sequence[object] | Mapping[str, object] | None = None
    expected_success: bool = True
    expected_statement_type: str | None = None
    expected_binding_count: int | None = None
    expected_where_fields: tuple[str, ...] | None = None
    expected_sql_fragment: str | None = None
    known_unsupported_target: bool = False
    known_parser_gap: bool = False
    known_unsafe_parse_success: bool = False


WITH_CASES = (
    Case(
        "with.select",
        "WITH selected AS (SELECT * FROM people WHERE tenant_id = 7) "
        "SELECT * FROM selected WHERE active = TRUE",
        expected_statement_type="SELECT",
        expected_binding_count=2,
        expected_where_fields=("active", "people.tenant_id"),
    ),
    Case(
        "with.recursive_select",
        "WITH RECURSIVE nums(n) AS ("
        "SELECT 1 UNION ALL SELECT n + 1 FROM nums WHERE n < 5"
        ") SELECT * FROM nums WHERE n > 2",
        expected_statement_type="SELECT",
        expected_binding_count=2,
    ),
    Case(
        "with.multiple_ctes",
        "WITH active_people AS (SELECT * FROM people WHERE active = TRUE), "
        "recent AS (SELECT * FROM orders WHERE created_at >= '2024-01-01') "
        "SELECT * FROM active_people JOIN recent ON recent.person_id = active_people.id "
        "WHERE recent.total > 100",
        expected_statement_type="SELECT",
        expected_binding_count=3,
    ),
    Case(
        "with.materialized",
        "WITH selected AS MATERIALIZED (SELECT * FROM people WHERE id = 1) "
        "SELECT * FROM selected",
        expected_statement_type="SELECT",
        expected_binding_count=1,
        expected_where_fields=("people.id",),
    ),
    Case(
        "with.insert",
        "WITH source_row AS (SELECT 1 AS ignored) "
        "INSERT INTO people (id, name) VALUES (2, 'Fred')",
        expected_statement_type="INSERT",
        expected_binding_count=2,
    ),
    Case(
        "with.data_modifying_cte_insert",
        "WITH moved AS (DELETE FROM people WHERE inactive_since < '2024-01-01' RETURNING *) "
        "INSERT INTO people_archive SELECT * FROM moved",
        expected_statement_type="INSERT",
        expected_binding_count=1,
        expected_where_fields=("inactive_since",),
    ),
    Case(
        "with.update",
        "WITH selected AS (SELECT id FROM people WHERE tenant_id = 7) "
        "UPDATE people SET active = FALSE FROM selected "
        "WHERE people.id = selected.id",
        expected_statement_type="UPDATE",
        expected_binding_count=2,
    ),
    Case(
        "with.delete",
        "WITH selected AS (SELECT id FROM people WHERE tenant_id = 7) "
        "DELETE FROM people USING selected "
        "WHERE people.id = selected.id AND people.active = FALSE",
        expected_statement_type="DELETE",
        expected_binding_count=2,
    ),
    Case(
        "with.merge",
        "WITH incoming AS (SELECT 1 AS id, 'Fred' AS name) "
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = i.name "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, i.name)",
        expected_statement_type="MERGE",
        expected_binding_count=0,
        expected_sql_fragment="WITH incoming AS",
    ),
    Case(
        "with.merge_inside_select_cte",
        "WITH changed AS ("
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred' RETURNING *"
        ") SELECT * FROM changed",
        expected_statement_type="SELECT",
        expected_binding_count=1,
    ),
    Case(
        "with.quoted_and_commented",
        '/* lead */ WITH "Chosen" AS (SELECT * FROM "People" WHERE "id" = 1) '
        'SELECT * FROM "Chosen" WHERE "active" = TRUE',
        expected_statement_type="SELECT",
        expected_binding_count=2,
    ),
    Case(
        "with.postgres_numbered_binding",
        "WITH selected AS (SELECT * FROM people WHERE tenant_id = $1) "
        "SELECT * FROM selected WHERE active = TRUE",
        bindings=[7],
        expected_statement_type="SELECT",
        expected_binding_count=2,
    ),
    Case(
        "with.incomplete",
        "WITH selected AS (SELECT * FROM people)",
        expected_success=False,
    ),
)


MERGE_CASES = (
    Case(
        "merge.postgres.full_hardcoded",
        "MERGE INTO people AS p USING incoming AS i "
        "ON p.id = i.id AND p.tenant_id = 7 "
        "WHEN MATCHED AND p.active = TRUE "
        "THEN UPDATE SET name = 'Fred', active = FALSE "
        "WHEN NOT MATCHED THEN INSERT (id, name, active) "
        "VALUES (i.id, 'Fred', TRUE)",
        expected_statement_type="MERGE",
        expected_binding_count=6,
    ),
    Case(
        "merge.postgres.existing_and_hardcoded",
        "MERGE INTO people AS p USING incoming AS i "
        "ON p.id = i.id AND p.tenant_id = $1 "
        "WHEN MATCHED THEN UPDATE SET name = $2, active = FALSE "
        "WHEN NOT MATCHED THEN INSERT (id, name, active) VALUES (i.id, $2, TRUE)",
        bindings=[7, "Fred"],
        expected_statement_type="MERGE",
        expected_binding_count=5,
    ),
    Case(
        "merge.postgres.do_nothing",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED AND p.active = FALSE THEN DO NOTHING "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')",
        expected_statement_type="MERGE",
        expected_binding_count=2,
    ),
    Case(
        "merge.postgres.default_values",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, DEFAULT)",
        expected_statement_type="MERGE",
        expected_binding_count=0,
    ),
    Case(
        "merge.duckdb.source_where",
        "MERGE INTO people AS p USING ("
        "SELECT * FROM incoming WHERE tenant_id = 7"
        ") AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred' "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')",
        source_dialect="duckdb",
        target_dialect="duckdb",
        expected_statement_type="MERGE",
        expected_binding_count=3,
        expected_where_fields=("incoming.tenant_id",),
    ),
    Case(
        "merge.duckdb.using_columns",
        "MERGE INTO people USING incoming USING (id) "
        "WHEN MATCHED THEN UPDATE SET active = FALSE "
        "WHEN NOT MATCHED THEN INSERT BY NAME",
        source_dialect="duckdb",
        target_dialect="duckdb",
        expected_statement_type="MERGE",
        expected_binding_count=1,
        expected_sql_fragment="INSERT (BY AS NAME)",
        known_unsafe_parse_success=True,
    ),
    Case(
        "merge.duckdb.multiple_actions_returning",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED AND p.active = FALSE THEN DELETE "
        "WHEN MATCHED AND p.name <> 'Fred' THEN UPDATE SET name = 'Fred' "
        "WHEN NOT MATCHED THEN INSERT BY NAME RETURNING merge_action, p.*",
        source_dialect="duckdb",
        target_dialect="duckdb",
        expected_statement_type="MERGE",
        expected_binding_count=3,
        expected_sql_fragment="RETURNING",
    ),
    Case(
        "merge.tsql.by_source_delete",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred' "
        "WHEN NOT MATCHED BY TARGET THEN INSERT (id, name) VALUES (i.id, 'Fred') "
        "WHEN NOT MATCHED BY SOURCE THEN DELETE;",
        source_dialect="tsql",
        target_dialect="tsql",
        expected_statement_type="MERGE",
        expected_binding_count=2,
    ),
    Case(
        "merge.bigquery",
        "MERGE people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED AND p.active = FALSE THEN UPDATE SET name = 'Fred' "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')",
        source_dialect="bigquery",
        target_dialect="bigquery",
        expected_statement_type="MERGE",
        expected_binding_count=3,
    ),
    Case(
        "merge.snowflake.star_actions",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET * "
        "WHEN NOT MATCHED THEN INSERT *",
        source_dialect="snowflake",
        target_dialect="snowflake",
        expected_statement_type="MERGE",
        expected_binding_count=0,
    ),
    Case(
        "merge.snowflake.all_by_name_parser_gap",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE ALL BY NAME "
        "WHEN NOT MATCHED THEN INSERT ALL BY NAME",
        source_dialect="snowflake",
        target_dialect="snowflake",
        expected_success=False,
        known_parser_gap=True,
    ),
    Case(
        "merge.databricks.star_actions",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET * "
        "WHEN NOT MATCHED THEN INSERT *",
        source_dialect="databricks",
        target_dialect="databricks",
        expected_statement_type="MERGE",
        expected_binding_count=0,
    ),
    Case(
        "merge.oracle",
        "MERGE INTO people p USING incoming i ON (p.id = i.id) "
        "WHEN MATCHED THEN UPDATE SET p.name = 'Fred' "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')",
        source_dialect="oracle",
        target_dialect="oracle",
        expected_statement_type="MERGE",
        expected_binding_count=2,
    ),
    Case(
        "merge.cross_postgres_to_duckdb",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred' "
        "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')",
        source_dialect="postgres",
        target_dialect="duckdb",
        expected_statement_type="MERGE",
        expected_binding_count=2,
    ),
    Case(
        "merge.quoted_and_commented",
        '/* lead */ MERGE INTO "People" AS p USING "Incoming" AS i '
        'ON p."id" = i."id" WHEN MATCHED THEN UPDATE SET "name" = \'Fred\' '
        'WHEN NOT MATCHED THEN INSERT ("id", "name") VALUES (i."id", \'Fred\')',
        expected_statement_type="MERGE",
        expected_binding_count=2,
    ),
    Case(
        "merge.sqlite_target_observation",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred'",
        target_dialect="sqlite",
        expected_statement_type="MERGE",
        expected_binding_count=1,
        known_unsupported_target=True,
    ),
    Case(
        "merge.mysql_target_observation",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred'",
        target_dialect="mysql",
        expected_statement_type="MERGE",
        expected_binding_count=1,
        known_unsupported_target=True,
    ),
    Case(
        "merge.binding_count_failure",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id AND p.tenant_id = $1 "
        "WHEN MATCHED THEN UPDATE SET name = 'Fred'",
        bindings=[],
        expected_success=False,
    ),
    Case(
        "merge.malformed",
        "MERGE INTO people USING incoming WHEN MATCHED UPDATE",
        expected_statement_type="MERGE",
        expected_binding_count=0,
        known_unsafe_parse_success=True,
    ),
    Case(
        "merge.multiple_statements",
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN MATCHED THEN DELETE; SELECT 1",
        expected_success=False,
    ),
)


@dataclass(frozen=True)
class DuckDBExecutionCase:
    name: str
    sql: str


DUCKDB_EXECUTION_CASES = (
    DuckDBExecutionCase(
        "insert_unmatched",
        "MERGE INTO people AS p USING (SELECT 3 AS id, 'Third' AS name, TRUE AS active) AS i "
        "ON p.id = i.id WHEN NOT MATCHED THEN INSERT (id, name, active) "
        "VALUES (i.id, i.name, i.active)",
    ),
    DuckDBExecutionCase(
        "update_matched_hardcoded",
        "MERGE INTO people AS p USING (SELECT 1 AS id) AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = 'Updated', active = FALSE",
    ),
    DuckDBExecutionCase(
        "conditional_delete",
        "MERGE INTO people AS p USING (SELECT 1 AS id) AS i ON p.id = i.id "
        "WHEN MATCHED AND p.active = TRUE THEN DELETE",
    ),
    DuckDBExecutionCase(
        "insert_action_hardcoded",
        "MERGE INTO people AS p USING (SELECT 3 AS id) AS i ON p.id = i.id "
        "WHEN NOT MATCHED THEN INSERT (id, name, active) VALUES (i.id, 'Third', TRUE)",
    ),
    DuckDBExecutionCase(
        "multiple_source_rows",
        "MERGE INTO people AS p USING ("
        "SELECT 1 AS id, 'Updated' AS name UNION ALL SELECT 3, 'Third'"
        ") AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = i.name "
        "WHEN NOT MATCHED THEN INSERT (id, name, active) VALUES (i.id, i.name, TRUE)",
    ),
    DuckDBExecutionCase(
        "with_prefix",
        "WITH incoming AS (SELECT 3 AS id, 'Third' AS name) "
        "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
        "WHEN NOT MATCHED THEN INSERT (id, name, active) VALUES (i.id, i.name, TRUE)",
    ),
    DuckDBExecutionCase(
        "multiple_actions_returning",
        "MERGE INTO people AS p USING ("
        "SELECT 1 AS id, 'Updated' AS name UNION ALL SELECT 3, 'Third'"
        ") AS i ON p.id = i.id "
        "WHEN MATCHED THEN UPDATE SET name = i.name "
        "WHEN NOT MATCHED THEN INSERT (id, name, active) VALUES (i.id, i.name, TRUE) "
        "RETURNING merge_action, *",
    ),
)


def _case_result(case: Case) -> dict[str, Any]:
    package = prepare_statement(
        case.sql,
        bindings=case.bindings,
        source_dialect=case.source_dialect,
        target_dialect=case.target_dialect,
    )
    expectation_errors: list[str] = []
    if package["success"] != case.expected_success:
        expectation_errors.append(
            f"success was {package['success']}, expected {case.expected_success}"
        )
    if package["success"]:
        if package.get("statement_type") != case.expected_statement_type:
            expectation_errors.append(
                f"statement_type was {package.get('statement_type')!r}, "
                f"expected {case.expected_statement_type!r}"
            )
        if (
            case.expected_binding_count is not None
            and len(package.get("bindings", ())) != case.expected_binding_count
        ):
            expectation_errors.append(
                f"binding count was {len(package.get('bindings', ()))}, "
                f"expected {case.expected_binding_count}"
            )
        if (
            case.expected_where_fields is not None
            and tuple(package.get("where_fields", ())) != case.expected_where_fields
        ):
            expectation_errors.append(
                f"where_fields were {package.get('where_fields')!r}, "
                f"expected {list(case.expected_where_fields)!r}"
            )
        if case.expected_sql_fragment and case.expected_sql_fragment not in package.get(
            "sql", ""
        ):
            expectation_errors.append(
                f"SQL did not contain {case.expected_sql_fragment!r}"
            )
    return {
        "name": case.name,
        "package": package,
        "expectation_errors": expectation_errors,
        "known_unsupported_target": case.known_unsupported_target,
        "known_parser_gap": case.known_parser_gap,
        "known_unsafe_parse_success": case.known_unsafe_parse_success,
    }


def _duckdb_state(connection: Any) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT id, name, active FROM people ORDER BY id"
    ).fetchall()


def _run_duckdb_statement(
    duckdb: Any,
    sql: str,
    bindings: Sequence[object] = (),
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE people(id INTEGER PRIMARY KEY, name VARCHAR, active BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO people VALUES (1, 'Original', TRUE), (2, 'Keep', TRUE)"
        )
        cursor = connection.execute(sql, list(bindings))
        returned = cursor.fetchall() if cursor.description else []
        return returned, _duckdb_state(connection)
    finally:
        connection.close()


def _duckdb_execution_result(case: DuckDBExecutionCase) -> dict[str, Any]:
    duckdb = importlib.import_module("duckdb")
    raw_result, raw_state = _run_duckdb_statement(duckdb, case.sql)
    package = prepare_statement(
        case.sql,
        source_dialect="duckdb",
        target_dialect="duckdb",
    )
    if not package["success"] or package.get("envelope_type") != "prepared":
        return {
            "name": case.name,
            "equivalent": False,
            "package": package,
        }
    prepared_package = cast(PreparedStatement, package)
    prepared_result, prepared_state = _run_duckdb_statement(
        duckdb,
        prepared_package["sql"],
        prepared_package["bindings"],
    )
    return {
        "name": case.name,
        "equivalent": (
            raw_result == prepared_result and raw_state == prepared_state
        ),
        "package": package,
        "raw_result": raw_result,
        "prepared_result": prepared_result,
        "raw_state": raw_state,
        "prepared_state": prepared_state,
    }


def run_experiment(*, execute_duckdb: bool = True) -> dict[str, Any]:
    """Run the structural matrix and optional real DuckDB comparisons."""
    with merge_support():
        results = [_case_result(case) for case in (*WITH_CASES, *MERGE_CASES)]
        duckdb_results = (
            [_duckdb_execution_result(case) for case in DUCKDB_EXECUTION_CASES]
            if execute_duckdb
            else []
        )
        hardcoded_package = cast(
            PreparedStatement,
            prepare_statement(
                "MERGE INTO people AS p USING incoming AS i "
                "ON p.id = i.id AND p.tenant_id = 7 "
                "WHEN MATCHED THEN UPDATE SET name = 'Fred'",
                source_dialect="postgres",
                target_dialect="postgres",
            ),
        )
        parameterized_package = cast(
            PreparedStatement,
            prepare_statement(
                "MERGE INTO people AS p USING incoming AS i "
                "ON p.id = i.id AND p.tenant_id = $1 "
                "WHEN MATCHED THEN UPDATE SET name = $2",
                bindings=[7, "Fred"],
                source_dialect="postgres",
                target_dialect="postgres",
            ),
        )
        hardcoded_fingerprint = hardcoded_package["sql_fingerprint"]
        parameterized_fingerprint = parameterized_package["sql_fingerprint"]

    successful_packages = [
        result["package"] for result in results if result["package"]["success"]
    ]
    return {
        "summary": {
            "case_count": len(results),
            "with_case_count": len(WITH_CASES),
            "merge_case_count": len(MERGE_CASES),
            "expectation_failure_count": sum(
                bool(result["expectation_errors"]) for result in results
            ),
            "prepared_count": sum(
                package.get("envelope_type") == "prepared"
                for package in successful_packages
            ),
            "merge_prepared_count": sum(
                package.get("statement_type") == "MERGE"
                for package in successful_packages
            ),
            "with_statement_type_count": sum(
                package.get("statement_type") == "WITH"
                for package in successful_packages
            ),
            "known_unsupported_target_success_count": sum(
                result["known_unsupported_target"]
                and result["package"]["success"]
                for result in results
            ),
            "known_parser_gap_count": sum(
                result["known_parser_gap"] for result in results
            ),
            "known_unsafe_parse_success_count": sum(
                result["known_unsafe_parse_success"]
                and result["package"]["success"]
                for result in results
            ),
            "fingerprint_converges": (
                hardcoded_fingerprint == parameterized_fingerprint
            ),
            "duckdb_execution_count": len(duckdb_results),
            "duckdb_equivalent_count": sum(
                result["equivalent"] for result in duckdb_results
            ),
        },
        "results": results,
        "duckdb_results": duckdb_results,
    }


def main() -> None:
    print(json.dumps(run_experiment(), indent=2, default=str))


if __name__ == "__main__":
    main()

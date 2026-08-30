"""Probe REPLACE as a fifth extended statement family without changing main."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ClassVar, cast
from unittest.mock import patch

from sqlglot import Dialect, ErrorLevel, exp
from sqlglot.dialects.mysql import MySQL
from sqlglot.dialects.sqlite import SQLite
from sqlglot.errors import UnsupportedError
from sqlglot.generators.mysql import MySQLGenerator
from sqlglot.tokenizer_core import TokenType

from sqlglot_experiments import (
    prepare_statement,
    source_parameters,
    statement_api,
    statement_fingerprinting,
)
from sqlglot_experiments.dialect_adapters import (
    _SourceSQLite,
    generate_target_sql,
    parsing_dialect,
)


class _ReplaceParserMixin:
    def _parse_replace_statement(self: Any) -> exp.Insert:
        statement = cast(exp.Insert, self._parse_insert())
        statement.set("alternative", "REPLACE")
        return statement


class _MySQLReplaceParserMixin(_ReplaceParserMixin):
    def _parse_replace_statement(self: Any) -> exp.Insert:
        modifier: str | None = None
        if self._match_texts(("LOW_PRIORITY", "DELAYED")):
            modifier = self._prev.text.upper()
        statement = super()._parse_replace_statement()
        if modifier:
            statement.meta["replace_modifier"] = modifier
        return statement


class _ReplaceSourceSQLite(_SourceSQLite):
    class Tokenizer(_SourceSQLite.Tokenizer):
        COMMANDS = _SourceSQLite.Tokenizer.COMMANDS - {TokenType.REPLACE}

    class Parser(_ReplaceParserMixin, _SourceSQLite.Parser):
        STATEMENT_PARSERS: ClassVar = {
            **_SourceSQLite.Parser.STATEMENT_PARSERS,
            TokenType.REPLACE: lambda self: self._parse_replace_statement(),
        }


class _ReplaceMySQLGenerator(MySQLGenerator):
    def insert_sql(self, expression: exp.Insert) -> str:
        if str(expression.args.get("alternative", "")).upper() != "REPLACE":
            return super().insert_sql(expression)

        statement = expression.copy()
        statement.set("with_", None)
        statement.set("alternative", None)
        insert_sql = super().insert_sql(statement)
        if not insert_sql.startswith("INSERT"):
            raise UnsupportedError("cannot render the MySQL REPLACE statement")
        modifier = expression.meta.get("replace_modifier")
        replace = f"REPLACE {modifier}" if modifier else "REPLACE"
        return self.prepend_ctes(expression, f"{replace}{insert_sql[6:]}")


class _ReplaceMySQL(MySQL):
    class Tokenizer(MySQL.Tokenizer):
        COMMANDS = MySQL.Tokenizer.COMMANDS - {TokenType.REPLACE}

    class Parser(_MySQLReplaceParserMixin, MySQL.Parser):
        STATEMENT_PARSERS: ClassVar = {
            **MySQL.Parser.STATEMENT_PARSERS,
            TokenType.REPLACE: lambda self: self._parse_replace_statement(),
        }

    Generator = _ReplaceMySQLGenerator


def _experimental_parsing_dialect(
    *,
    source_dialect: str,
    target_dialect: str,
) -> type[SQLite | MySQL] | str:
    if source_dialect == "sqlite":
        return _ReplaceSourceSQLite
    if source_dialect == "mysql":
        return _ReplaceMySQL
    return parsing_dialect(
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


def _is_replace(statement: exp.Expr) -> bool:
    return isinstance(statement, exp.Insert) and (
        str(statement.args.get("alternative", "")).upper() == "REPLACE"
    )


def _experimental_generate_target_sql(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
    comments: bool = True,
) -> str:
    if not _is_replace(statement):
        return generate_target_sql(
            statement,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            comments=comments,
        )
    if target_dialect == "mysql":
        return statement.sql(
            dialect=_ReplaceMySQL,
            comments=comments,
            unsupported_level=ErrorLevel.RAISE,
        )
    if target_dialect == "sqlite":
        return generate_target_sql(
            statement,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            comments=comments,
        )
    raise UnsupportedError(
        f"{target_dialect} has no configured REPLACE statement rendering"
    )


def _experimental_statement_type(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> str:
    if _is_replace(statement):
        return "REPLACE"
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
    if _is_replace(statement):
        return "REPLACE"
    return _ORIGINAL_FINGERPRINT_STATEMENT_TYPE(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


def _experimental_lexical_parameters(
    sql: str,
    *,
    source_dialect: str,
) -> tuple[source_parameters._LexicalParameter, ...]:
    if source_dialect == "sqlite":
        return source_parameters._sqlite_parameters(
            sql,
            tokens=_ReplaceSourceSQLite.Tokenizer().tokenize(sql),
        )
    return _ORIGINAL_LEXICAL_PARAMETERS(
        sql,
        source_dialect=source_dialect,
    )


def _experimental_bindings_in_target_order(
    statement: exp.Expr,
    *,
    marker_values: dict[str, object],
    source_dialect: str,
    target_dialect: str,
) -> list[object]:
    marked_sql = _experimental_generate_target_sql(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    tokens = (
        _ReplaceMySQL.Tokenizer().tokenize(marked_sql)
        if target_dialect == "mysql"
        else Dialect.get_or_raise(target_dialect).tokenize(marked_sql)
    )
    marker_order = [token.text for token in tokens if token.text in marker_values]
    if set(marker_order) != set(marker_values):
        raise statement_api.StatementPreparationError(
            "target rendering lost a binding marker"
        )
    return [marker_values[marker] for marker in marker_order]


_ORIGINAL_API_STATEMENT_TYPE = statement_api._statement_type
_ORIGINAL_FINGERPRINT_STATEMENT_TYPE = statement_fingerprinting._statement_type
_ORIGINAL_LEXICAL_PARAMETERS = source_parameters._lexical_parameters


@contextmanager
def replace_support() -> Iterator[None]:
    """Temporarily install the proposed parser, generator, and type adapters."""
    statement_api._prepare_statement_structure.cache_clear()
    try:
        with (
            patch.object(
                statement_api,
                "parsing_dialect",
                side_effect=_experimental_parsing_dialect,
            ),
            patch.object(
                statement_fingerprinting,
                "parsing_dialect",
                side_effect=_experimental_parsing_dialect,
            ),
            patch.object(
                statement_api,
                "generate_target_sql",
                side_effect=_experimental_generate_target_sql,
            ),
            patch.object(
                statement_fingerprinting,
                "generate_target_sql",
                side_effect=_experimental_generate_target_sql,
            ),
            patch.object(
                statement_api,
                "_statement_type",
                side_effect=_experimental_statement_type,
            ),
            patch.object(
                statement_fingerprinting,
                "_statement_type",
                side_effect=_experimental_fingerprint_statement_type,
            ),
            patch.object(
                source_parameters,
                "_lexical_parameters",
                side_effect=_experimental_lexical_parameters,
            ),
            patch.object(
                statement_api,
                "_bindings_in_target_order",
                side_effect=_experimental_bindings_in_target_order,
            ),
        ):
            yield
    finally:
        statement_api._prepare_statement_structure.cache_clear()


@dataclass(frozen=True)
class Case:
    name: str
    source_dialect: str
    target_dialect: str
    sql: str
    bindings: Sequence[object] | Mapping[str, object] | None = None
    expected_success: bool = True
    expected_binding_count: int | None = None
    expected_sql_fragment: str | None = None
    known_unsafe_success: bool = False


CASES = (
    Case(
        "sqlite.values.hardcoded",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
        expected_sql_fragment="INSERT OR REPLACE INTO people",
    ),
    Case(
        "sqlite.values.qmark",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (?, ?)",
        bindings=[1, "Mark"],
        expected_binding_count=2,
    ),
    Case(
        "sqlite.values.named",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (:id, :name)",
        bindings={"id": 1, "name": "Mark"},
        expected_binding_count=2,
    ),
    Case(
        "sqlite.values.numbered",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (?2, ?1)",
        bindings=["Mark", 1],
        expected_binding_count=2,
    ),
    Case(
        "sqlite.placeholders_inside_text",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (?, '? :ignored') -- ? ignored",
        bindings=[1],
        expected_binding_count=2,
    ),
    Case(
        "sqlite.values.repeated_named",
        "sqlite",
        "sqlite",
        "REPLACE INTO pairs (left_id, right_id) VALUES (:id, :id)",
        bindings={"id": 7},
        expected_binding_count=2,
    ),
    Case(
        "sqlite.values.multirow",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (1, 'A'), (2, 'B')",
        expected_binding_count=4,
    ),
    Case(
        "sqlite.values.no_columns",
        "sqlite",
        "sqlite",
        "REPLACE INTO people VALUES (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "sqlite.default_values",
        "sqlite",
        "sqlite",
        "REPLACE INTO defaults DEFAULT VALUES",
        expected_binding_count=0,
    ),
    Case(
        "sqlite.select.where",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) "
        "SELECT id, name FROM incoming WHERE active = 1",
        expected_binding_count=1,
    ),
    Case(
        "sqlite.returning",
        "sqlite",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (1, 'Mark') RETURNING id",
        expected_binding_count=2,
    ),
    Case(
        "sqlite.quoted",
        "sqlite",
        "sqlite",
        'REPLACE INTO "order" ("select", "value") VALUES (1, \'x\')',
        expected_binding_count=2,
    ),
    Case(
        "sqlite.leading_comment_case",
        "sqlite",
        "sqlite",
        "/* route */ replace into people (id, name) values (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "sqlite.explicit_alias",
        "sqlite",
        "sqlite",
        "INSERT OR REPLACE INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "sqlite.cte_prefix",
        "sqlite",
        "sqlite",
        "WITH incoming(id, name) AS (SELECT 1, 'Mark') "
        "REPLACE INTO people SELECT * FROM incoming",
        expected_binding_count=0,
    ),
    Case(
        "sqlite.qualified_table",
        "sqlite",
        "sqlite",
        "REPLACE INTO main.people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "mysql.values.hardcoded",
        "mysql",
        "mysql",
        "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
        expected_sql_fragment="REPLACE INTO people",
    ),
    Case(
        "mysql.values.qmark",
        "mysql",
        "mysql",
        "REPLACE INTO people (id, name) VALUES (?, ?)",
        bindings=[1, "Mark"],
        expected_success=False,
    ),
    Case(
        "mysql.set.hardcoded",
        "mysql",
        "mysql",
        "REPLACE INTO people SET id = 1, name = 'Mark'",
        expected_binding_count=2,
    ),
    Case(
        "mysql.set.qmark",
        "mysql",
        "mysql",
        "REPLACE INTO people SET id = ?, name = ?",
        bindings=[1, "Mark"],
        expected_success=False,
    ),
    Case(
        "mysql.values.multirow",
        "mysql",
        "mysql",
        "REPLACE INTO people (id, name) VALUES (1, 'A'), (2, 'B')",
        expected_binding_count=4,
    ),
    Case(
        "mysql.partition",
        "mysql",
        "mysql",
        "REPLACE INTO people PARTITION (p0) (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "mysql.select.where",
        "mysql",
        "mysql",
        "REPLACE INTO people (id, name) "
        "SELECT id, name FROM incoming WHERE active = 1",
        expected_binding_count=1,
    ),
    Case(
        "mysql.table_source",
        "mysql",
        "mysql",
        "REPLACE INTO people TABLE incoming",
        expected_binding_count=0,
    ),
    Case(
        "mysql.optional_into",
        "mysql",
        "mysql",
        "REPLACE people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "mysql.quoted",
        "mysql",
        "mysql",
        "REPLACE INTO `order` (`select`, `value`) VALUES (1, 'x')",
        expected_binding_count=2,
    ),
    Case(
        "mysql.leading_comment_case",
        "mysql",
        "mysql",
        "/* route */ replace into people (id, name) values (1, 'Mark')",
        expected_binding_count=2,
    ),
    Case(
        "mysql.low_priority",
        "mysql",
        "mysql",
        "REPLACE LOW_PRIORITY INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
        expected_sql_fragment="REPLACE LOW_PRIORITY INTO people",
    ),
    Case(
        "mysql.delayed",
        "mysql",
        "mysql",
        "REPLACE DELAYED INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
        expected_sql_fragment="REPLACE DELAYED INTO people",
    ),
    Case(
        "mysql.cte_prefix",
        "mysql",
        "mysql",
        "WITH incoming AS (SELECT 1 AS id, 'Mark' AS name) "
        "REPLACE INTO people SELECT * FROM incoming",
        expected_binding_count=0,
        expected_sql_fragment="REPLACE INTO people",
    ),
    Case(
        "sqlite_to_mysql.values",
        "sqlite",
        "mysql",
        "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
        expected_sql_fragment="REPLACE INTO people",
    ),
    Case(
        "mysql_to_sqlite.values",
        "mysql",
        "sqlite",
        "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
        expected_binding_count=2,
        expected_sql_fragment="INSERT OR REPLACE INTO people",
    ),
    Case(
        "mysql_to_postgres.rejected",
        "mysql",
        "postgres",
        "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
        expected_success=False,
    ),
    Case(
        "mysql.value_singular.limitation",
        "mysql",
        "mysql",
        "REPLACE INTO people (id, name) VALUE (1, 'Mark')",
        expected_success=False,
    ),
    Case(
        "mysql.row_constructor.observation",
        "mysql",
        "mysql",
        "REPLACE INTO people (id, name) VALUES ROW(1, 'A'), ROW(2, 'B')",
        expected_binding_count=0,
        known_unsafe_success=True,
    ),
    Case(
        "invalid.malformed",
        "sqlite",
        "sqlite",
        "REPLACE woof where",
        expected_success=False,
    ),
    Case(
        "invalid.multiple_statements",
        "sqlite",
        "sqlite",
        "REPLACE INTO people VALUES (1, 'A'); SELECT 1",
        expected_success=False,
    ),
)


@dataclass(frozen=True)
class SQLiteExecutionCase:
    name: str
    setup: tuple[str, ...]
    sql: str
    bindings: Sequence[object] | Mapping[str, object] | None
    observation_sql: str


SQLITE_EXECUTION_CASES = (
    SQLiteExecutionCase(
        "insert_new",
        ("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",),
        "REPLACE INTO people (id, name) VALUES (1, 'New')",
        None,
        "SELECT id, name FROM people ORDER BY id",
    ),
    SQLiteExecutionCase(
        "replace_primary_key",
        (
            "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
            "INSERT INTO people VALUES (1, 'Old')",
        ),
        "REPLACE INTO people (id, name) VALUES (1, 'New')",
        None,
        "SELECT id, name FROM people ORDER BY id",
    ),
    SQLiteExecutionCase(
        "replace_unique_key",
        (
            "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE)",
            "INSERT INTO people VALUES (1, 'a@example.test')",
        ),
        "REPLACE INTO people (id, email) VALUES (?, ?)",
        [2, "a@example.test"],
        "SELECT id, email FROM people ORDER BY id",
    ),
    SQLiteExecutionCase(
        "replace_two_conflicting_rows",
        (
            "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE)",
            "INSERT INTO people VALUES (1, 'a@example.test')",
            "INSERT INTO people VALUES (2, 'b@example.test')",
        ),
        "REPLACE INTO people (id, email) VALUES (1, 'b@example.test')",
        None,
        "SELECT id, email FROM people ORDER BY id",
    ),
    SQLiteExecutionCase(
        "named_bindings",
        ("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)",),
        "REPLACE INTO people (id, name) VALUES (:id, :name)",
        {"id": 1, "name": "Named"},
        "SELECT id, name FROM people",
    ),
    SQLiteExecutionCase(
        "multirow",
        ("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)",),
        "REPLACE INTO people (id, name) VALUES (1, 'A'), (2, 'B')",
        None,
        "SELECT id, name FROM people ORDER BY id",
    ),
    SQLiteExecutionCase(
        "replace_select",
        (
            "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE incoming (id INTEGER, name TEXT, active INTEGER)",
            "INSERT INTO incoming VALUES (1, 'A', 1), (2, 'B', 0)",
        ),
        "REPLACE INTO people (id, name) "
        "SELECT id, name FROM incoming WHERE active = 1",
        None,
        "SELECT id, name FROM people ORDER BY id",
    ),
    SQLiteExecutionCase(
        "cte_prefix",
        ("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)",),
        "WITH incoming(id, name) AS (SELECT 1, 'A') "
        "REPLACE INTO people SELECT * FROM incoming",
        None,
        "SELECT id, name FROM people",
    ),
    SQLiteExecutionCase(
        "default_values",
        (
            "CREATE TABLE defaults (id INTEGER PRIMARY KEY, name TEXT DEFAULT 'new')",
        ),
        "REPLACE INTO defaults DEFAULT VALUES",
        None,
        "SELECT id, name FROM defaults",
    ),
    SQLiteExecutionCase(
        "returning",
        ("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)",),
        "REPLACE INTO people (id, name) VALUES (1, 'A') RETURNING id, name",
        None,
        "SELECT id, name FROM people",
    ),
)


def _execute_sqlite_case(case: SQLiteExecutionCase) -> dict[str, Any]:
    raw = sqlite3.connect(":memory:")
    prepared = sqlite3.connect(":memory:")
    try:
        for setup_sql in case.setup:
            raw.execute(setup_sql)
            prepared.execute(setup_sql)

        raw_parameters: Sequence[object] | Mapping[str, object] = (
            () if case.bindings is None else case.bindings
        )
        raw_rows = raw.execute(case.sql, raw_parameters).fetchall()
        package = cast(
            dict[str, Any],
            prepare_statement(
                case.sql,
                bindings=case.bindings,
                source_dialect="sqlite",
                target_dialect="sqlite",
            ),
        )
        if not package["success"]:
            return {"name": case.name, "equivalent": False, "package": package}
        prepared_rows = prepared.execute(
            package["sql"], package["bindings"]
        ).fetchall()
        raw_state = raw.execute(case.observation_sql).fetchall()
        prepared_state = prepared.execute(case.observation_sql).fetchall()
        return {
            "name": case.name,
            "equivalent": raw_rows == prepared_rows
            and raw_state == prepared_state,
            "raw_rows": raw_rows,
            "prepared_rows": prepared_rows,
            "raw_state": raw_state,
            "prepared_state": prepared_state,
            "package": package,
        }
    finally:
        raw.close()
        prepared.close()


def run_experiment() -> dict[str, Any]:
    with replace_support():
        results: list[dict[str, Any]] = []
        for case in CASES:
            package = cast(
                dict[str, Any],
                prepare_statement(
                    case.sql,
                    bindings=case.bindings,
                    source_dialect=case.source_dialect,
                    target_dialect=case.target_dialect,
                ),
            )
            expectation_met = package["success"] is case.expected_success
            if package["success"]:
                expectation_met = expectation_met and (
                    package["envelope_type"] == "prepared"
                    and package["statement_type"] == "REPLACE"
                )
                if case.expected_binding_count is not None:
                    expectation_met = expectation_met and (
                        len(package["bindings"]) == case.expected_binding_count
                    )
                if case.expected_sql_fragment is not None:
                    expectation_met = expectation_met and (
                        case.expected_sql_fragment in package["sql"]
                    )
            results.append(
                {
                    "name": case.name,
                    "expectation_met": expectation_met,
                    "package": package,
                }
            )

        execution_results = [
            _execute_sqlite_case(case) for case in SQLITE_EXECUTION_CASES
        ]

        hardcoded = cast(
            dict[str, Any],
            prepare_statement(
                "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
                source_dialect="sqlite",
                target_dialect="sqlite",
            ),
        )
        explicit = cast(
            dict[str, Any],
            prepare_statement(
                "INSERT OR REPLACE INTO people (id, name) VALUES (1, 'Other')",
                source_dialect="sqlite",
                target_dialect="sqlite",
            ),
        )
        fingerprint_converges = (
            hardcoded.get("sql_fingerprint") == explicit.get("sql_fingerprint")
        )

    observed = Counter(
        "success" if result["package"]["success"] else "failure"
        for result in results
    )
    return {
        "summary": {
            "case_count": len(results),
            "expected_success_count": sum(
                case.expected_success for case in CASES
            ),
            "observed_success_count": observed["success"],
            "expectation_failure_count": sum(
                not result["expectation_met"] for result in results
            ),
            "sqlite_execution_count": len(execution_results),
            "sqlite_equivalent_count": sum(
                result["equivalent"] for result in execution_results
            ),
            "fingerprint_converges": fingerprint_converges,
            "known_unsafe_success_count": sum(
                result["package"]["success"] and case.known_unsafe_success
                for case, result in zip(CASES, results, strict=True)
            ),
        },
        "results": results,
        "execution_results": execution_results,
    }


def main() -> None:
    print(json.dumps(run_experiment(), indent=2, default=repr))


if __name__ == "__main__":
    main()

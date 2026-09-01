from __future__ import annotations

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import ANY, patch

from demo.sqlite_consumer import create_demo_database, execute_package, run_demo
from sqlglot_experiments import (
    AcceptedStatement,
    InputBindings,
    PreparationResult,
    PreparedStatement,
)
from sqlglot_experiments import prepare_statement as prepare_statement_result


def prepare_statement(
    sql: str,
    *,
    bindings: InputBindings | None = None,
    source_dialect: str,
    target_dialect: str,
) -> PreparedStatement:
    result = prepare_statement_result(
        sql,
        bindings=bindings,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    if not result["success"]:
        raise AssertionError(result["msg"])
    return cast(PreparedStatement, result)


def assert_failure(
    testcase: unittest.TestCase,
    result: PreparationResult,
    *,
    message_prefix: str,
) -> None:
    testcase.assertEqual(result["success"], False)
    testcase.assertEqual(result["warnings"], False)
    testcase.assertEqual(result["envelope_type"], "failure")
    testcase.assertTrue(result["msg"].startswith(message_prefix), result["msg"])
    testcase.assertEqual(
        set(result),
        {"success", "warnings", "msg", "envelope_type"},
    )


def call_public_api(*args: object, **kwargs: object) -> PreparationResult:
    """Exercise the runtime boundary as an untyped external caller."""
    return prepare_statement_result(*args, **kwargs)  # type: ignore[call-overload]


class StatementApiTests(unittest.TestCase):
    def test_select_returns_compact_execution_package(self) -> None:
        package = prepare_statement(
            """
            SELECT id, name, value, category, created_at
            FROM big_table
            WHERE category = 'sales'
            """,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package,
            {
                "success": True,
                "warnings": True,
                "msg": "warnings: replaced 1 hardcoded value with placeholder",
                "envelope_type": "prepared",
                "sql_fingerprint": ANY,
                "dialect": ["sqlite", "sqlite"],
                "statement_type": "SELECT",
                "sql": (
                    "SELECT id, name, value, category, created_at "
                    "FROM big_table WHERE category = ?"
                ),
                "bindings": ["sales"],
                "where_fields": ["big_table.category"],
                "analysis": {
                    "hardcoded_value_count": 1,
                    "hardcoded_field_count": 1,
                    "insert": None,
                    "existing_row_mutations": {
                        "effects": [],
                        "evidence_complete": True,
                    },
                },
            },
        )
        self.assertRegex(package["sql_fingerprint"], r"^[0-9a-f]{64}$")

    def test_insert_preserves_row_and_column_binding_order(self) -> None:
        package = prepare_statement(
            """
            INSERT INTO people (forename, age)
            VALUES ('Mark', 42), ('Paul', 43)
            """,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["statement_type"], "INSERT")
        self.assertEqual(package["success"], True)
        self.assertEqual(package["warnings"], True)
        self.assertEqual(
            package["msg"],
            "warnings: replaced 4 hardcoded values with placeholders",
        )
        self.assertEqual(package["bindings"], ["Mark", 42, "Paul", 43])
        self.assertEqual(
            package["sql"],
            "INSERT INTO people (forename, age) VALUES (?, ?), (?, ?)",
        )
        self.assertEqual(package["analysis"]["hardcoded_field_count"], 2)

    def test_insert_without_column_list_lifts_values_but_does_not_invent_fields(
        self,
    ) -> None:
        package = prepare_statement(
            "INSERT INTO flags VALUES (1, TRUE, NULL)",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["sql"], "INSERT INTO flags VALUES (?, ?, ?)")
        self.assertEqual(package["bindings"], [1, True, None])
        self.assertEqual(
            package["analysis"],
            {
                "hardcoded_value_count": 3,
                "hardcoded_field_count": 0,
                "insert": {
                    "target": {
                        "catalog": None,
                        "schema": None,
                        "table": "flags",
                    },
                    "supplied_columns": [],
                    "plain_values_binding_rows": [[0, 1, 2]],
                },
                "existing_row_mutations": {
                    "effects": [],
                    "evidence_complete": True,
                },
            },
        )

    def test_update_orders_set_bindings_before_where_bindings(self) -> None:
        package = prepare_statement(
            "UPDATE people SET forename = 'Paul', age = 44 WHERE id = 7",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["statement_type"], "UPDATE")
        self.assertEqual(package["bindings"], ["Paul", 44, 7])
        self.assertEqual(
            package["sql"],
            "UPDATE people SET forename = ?, age = ? WHERE id = ?",
        )
        self.assertEqual(package["analysis"]["hardcoded_field_count"], 3)

    def test_delete_lifts_string_and_negative_number(self) -> None:
        package = prepare_statement(
            "DELETE FROM people WHERE status = 'inactive' OR score < -1.5",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["statement_type"], "DELETE")
        self.assertEqual(package["bindings"], ["inactive", -1.5])
        self.assertEqual(
            package["sql"],
            "DELETE FROM people WHERE status = ? OR score < ?",
        )

    def test_nested_in_and_between_literals_are_lifted_in_sql_order(self) -> None:
        package = prepare_statement(
            """
            SELECT * FROM people
            WHERE score BETWEEN 10 AND 20
              AND status IN ('active', 'paused')
              AND id IN (
                  SELECT person_id FROM audit
                  WHERE kind = 'login'
              )
            """,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [10, 20, "active", "paused", "login"])
        self.assertEqual(package["analysis"]["hardcoded_value_count"], 5)
        self.assertEqual(package["analysis"]["hardcoded_field_count"], 3)

    def test_repeated_field_counts_once_but_values_count_twice(self) -> None:
        package = prepare_statement(
            "SELECT * FROM orders WHERE total > 10 AND total < 100",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [10, 100])
        self.assertEqual(
            package["analysis"],
            {
                "hardcoded_value_count": 2,
                "hardcoded_field_count": 1,
                "insert": None,
                "existing_row_mutations": {
                    "effects": [],
                    "evidence_complete": True,
                },
            },
        )

    def test_non_field_literals_remain_sql_and_are_not_bindings(self) -> None:
        package = prepare_statement(
            """
            SELECT 'fixed', JSON_EXTRACT(payload, '$.kind')
            FROM events
            WHERE note = 'x''; DELETE FROM events; --'
            LIMIT 10
            """,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], ["x'; DELETE FROM events; --"])
        self.assertIn("'fixed'", package["sql"])
        self.assertIn("'$.kind'", package["sql"])
        self.assertIn("LIMIT 10", package["sql"])

    def test_statement_without_eligible_literals_has_empty_bindings(self) -> None:
        package = prepare_statement(
            "SELECT id FROM people WHERE active IS NULL",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [])
        self.assertEqual(
            (package["success"], package["warnings"], package["msg"]),
            (True, False, "success: ok"),
        )
        self.assertEqual(
            package["analysis"],
            {
                "hardcoded_value_count": 0,
                "hardcoded_field_count": 0,
                "insert": None,
                "existing_row_mutations": {
                    "effects": [],
                    "evidence_complete": True,
                },
            },
        )

    def test_existing_placeholder_returns_complete_package(self) -> None:
        package = prepare_statement(
            "SELECT * FROM orders WHERE category = ?",
            bindings=["sales"],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package,
            {
                "success": True,
                "warnings": False,
                "msg": "success: ok",
                "envelope_type": "prepared",
                "sql_fingerprint": ANY,
                "dialect": ["sqlite", "sqlite"],
                "statement_type": "SELECT",
                "sql": "SELECT * FROM orders WHERE category = ?",
                "bindings": ["sales"],
                "where_fields": ["orders.category"],
                "analysis": {
                    "hardcoded_value_count": 0,
                    "hardcoded_field_count": 0,
                    "insert": None,
                    "existing_row_mutations": {
                        "effects": [],
                        "evidence_complete": True,
                    },
                },
            },
        )
        self.assertRegex(package["sql_fingerprint"], r"^[0-9a-f]{64}$")

    def test_fingerprint_converges_without_changing_warning_state(self) -> None:
        hardcoded = prepare_statement(
            "SELECT * FROM orders WHERE category = 'sales'",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        parameterized = prepare_statement(
            "SELECT * FROM orders WHERE category = ?",
            bindings=["sales"],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(hardcoded["sql_fingerprint"], parameterized["sql_fingerprint"])
        self.assertEqual((hardcoded["success"], hardcoded["warnings"]), (True, True))
        self.assertEqual(
            (parameterized["success"], parameterized["warnings"]),
            (True, False),
        )

    def test_sqlite_placeholder_forms_are_normalized_to_qmark(self) -> None:
        for placeholder in (
            "?",
            "?1",
            ":category",
            "@category",
            "$category",
            "$1",
        ):
            with self.subTest(placeholder=placeholder):
                package = prepare_statement(
                    f"SELECT * FROM people WHERE category = {placeholder}",
                    bindings=["sales"],
                    source_dialect="sqlite",
                    target_dialect="sqlite",
                )

                self.assertEqual(
                    package["sql"],
                    "SELECT * FROM people WHERE category = ?",
                )
                self.assertEqual(package["bindings"], ["sales"])

    def test_repeated_named_placeholders_reuse_the_source_slot(self) -> None:
        package = prepare_statement(
            "SELECT * FROM jobs WHERE tenant = :value OR owner = :value",
            bindings=[7],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package["sql"],
            "SELECT * FROM jobs WHERE tenant = ? OR owner = ?",
        )
        self.assertEqual(package["bindings"], [7, 7])

    def test_named_mapping_handles_reuse_and_keyword_like_names(self) -> None:
        supplied = {
            "x": 7,
            "text": "a:b?%",
            "nothing": None,
            "blob": b"abc",
        }
        package = prepare_statement(
            "SELECT :x+:x, :text, :nothing, :blob",
            bindings=supplied,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["sql"], "SELECT ? + ?, ?, ?, ?")
        self.assertEqual(
            package["bindings"],
            [7, 7, "a:b?%", None, b"abc"],
        )
        self.assertEqual(
            supplied,
            {"x": 7, "text": "a:b?%", "nothing": None, "blob": b"abc"},
        )

    def test_sqlite_numbered_parameters_reuse_explicit_slots(self) -> None:
        package = prepare_statement(
            "SELECT ?2, ?1, ?2 + ?1",
            bindings=[4, 9],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["sql"], "SELECT ?, ?, ? + ?")
        self.assertEqual(package["bindings"], [9, 4, 9, 4])

    def test_numbered_slots_follow_generated_limit_offset_order(self) -> None:
        package = prepare_statement(
            "SELECT row_id FROM set_left ORDER BY row_id LIMIT ?2, ?1",
            bindings=[3, 2],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package["sql"],
            "SELECT row_id FROM set_left ORDER BY row_id LIMIT ? OFFSET ?",
        )
        self.assertEqual(package["bindings"], [3, 2])

    def test_sqlite_slot_allocation_handles_gaps_and_explicit_references(self) -> None:
        package = prepare_statement(
            "SELECT ?3, ?, ?1",
            bindings=[10, 20, 30, 40],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [30, 40, 10])
        failure = prepare_statement_result(
            "SELECT ?3, ?, ?1",
            bindings=[10, 20, 30],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        assert_failure(
            self,
            failure,
            message_prefix="failure: bindings:",
        )

    def test_sqlite_dollar_names_are_not_numeric_slots(self) -> None:
        sequence_package = prepare_statement(
            "SELECT $2, $1, $2",
            bindings=[10, 20],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        mapping_package = prepare_statement(
            "SELECT $2, $1, $2",
            bindings={"1": 10, "2": 20},
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(sequence_package["bindings"], [10, 20, 10])
        self.assertEqual(mapping_package["bindings"], [20, 10, 20])

    def test_sqlite_mapping_uses_prefixless_names_for_distinct_slots(self) -> None:
        package = prepare_statement(
            "SELECT :x, @x, $x",
            bindings={"x": 7, "unused": "ignored"},
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [7, 7, 7])

    def test_mapping_rejects_anonymous_or_missing_sqlite_slots(self) -> None:
        for sql, bindings in (
            ("SELECT ?, :x", {"x": 7}),
            ("SELECT :missing", {}),
        ):
            with self.subTest(sql=sql):
                failure = prepare_statement_result(
                    sql,
                    bindings=bindings,
                    source_dialect="sqlite",
                    target_dialect="sqlite",
                )
                assert_failure(
                    self,
                    failure,
                    message_prefix="failure: bindings:",
                )

    def test_sqlite_explicit_parameter_can_name_an_existing_slot(self) -> None:
        package = prepare_statement(
            "SELECT ?, ?1",
            bindings={"1": 7},
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [7, 7])

    def test_reused_source_slot_and_lifted_literal_follow_target_order(self) -> None:
        package = prepare_statement(
            """
            SELECT * FROM jobs
            WHERE tenant = :x AND state = 'open' OR owner = :x
            """,
            bindings={"x": 7},
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [7, "open", 7])
        self.assertEqual(package["analysis"]["hardcoded_value_count"], 1)

    def test_existing_and_hardcoded_values_share_target_sql_order(self) -> None:
        package = prepare_statement(
            """
            SELECT * FROM orders
            WHERE category = ? AND status = 'open' AND tenant_id = ?
            """,
            bindings=["sales", 7],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package["sql"],
            "SELECT * FROM orders WHERE category = ? AND status = ? AND tenant_id = ?",
        )
        self.assertEqual(package["bindings"], ["sales", "open", 7])
        self.assertEqual(
            package["analysis"],
            {
                "hardcoded_value_count": 1,
                "hardcoded_field_count": 1,
                "insert": None,
                "existing_row_mutations": {
                    "effects": [],
                    "evidence_complete": True,
                },
            },
        )

    def test_bindings_follow_generated_limit_offset_order(self) -> None:
        package = prepare_statement(
            "SELECT n FROM numbers WHERE kind = ? LIMIT ?, ?",
            bindings=["prime", 2, 3],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package["sql"],
            "SELECT n FROM numbers WHERE kind = ? LIMIT ? OFFSET ?",
        )
        self.assertEqual(package["bindings"], ["prime", 3, 2])

    def test_cte_bindings_follow_generated_sql_not_ast_walk_order(self) -> None:
        package = prepare_statement(
            """
            WITH selected AS (
                SELECT * FROM jobs WHERE tenant = ?
            )
            SELECT * FROM selected WHERE state = ?
            """,
            bindings=[7, "open"],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [7, "open"])
        self.assertLess(
            package["sql"].index("tenant = ?"), package["sql"].index("state = ?")
        )

    def test_caller_binding_count_must_match_input_placeholders(self) -> None:
        cases = [
            ("SELECT * FROM people WHERE id = ?", None),
            ("SELECT * FROM people WHERE id = ?", []),
            ("SELECT * FROM people", [1]),
            ("SELECT * FROM people WHERE id = ?", [1, 2]),
        ]
        for sql, bindings in cases:
            with self.subTest(sql=sql, bindings=bindings):
                failure = prepare_statement_result(
                    sql,
                    bindings=bindings,
                    source_dialect="sqlite",
                    target_dialect="sqlite",
                )
                assert_failure(
                    self,
                    failure,
                    message_prefix="failure: bindings:",
                )

    def test_invalid_binding_container_returns_a_controlled_failure(self) -> None:
        result = prepare_statement_result(
            "SELECT * FROM people WHERE id = ?",
            bindings=42,  # type: ignore[arg-type]
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            result,
            {
                "success": False,
                "warnings": False,
                "msg": "failure: bindings: bindings must be a sequence or mapping of values",
                "envelope_type": "failure",
            },
        )

    def test_dollar_text_in_a_string_is_not_a_placeholder(self) -> None:
        package = prepare_statement(
            "SELECT '$category' AS marker FROM people WHERE id = 1",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [1])
        self.assertIn("'$category'", package["sql"])

    def test_question_marks_in_strings_and_comments_are_not_placeholders(self) -> None:
        package = prepare_statement(
            "SELECT '?' AS marker FROM people WHERE id = ? -- ? ignored",
            bindings=[7],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [7])
        self.assertIn("'?' AS marker", package["sql"])

    def test_multiple_statements_are_rejected(self) -> None:
        result = prepare_statement_result(
            "SELECT 1; SELECT 2",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        assert_failure(
            self,
            result,
            message_prefix="failure: expected exactly one SQL statement",
        )

    def test_non_dml_statement_returns_only_acceptance_envelope_and_id(
        self,
    ) -> None:
        result = prepare_statement_result(
            "CREATE TABLE example (id INTEGER)",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            result,
            {
                "success": True,
                "warnings": False,
                "msg": "success: ok",
                "envelope_type": "accepted",
                "sql_fingerprint": ANY,
            },
        )
        accepted = cast(AcceptedStatement, result)
        self.assertRegex(accepted["sql_fingerprint"], r"^[0-9a-f]{64}$")

    def test_recognised_non_dml_families_take_the_generic_route(self) -> None:
        cases = (
            ("sqlite", "CREATE TABLE people (id INTEGER)"),
            (
                "sqlite",
                ("CREATE TABLE active_people AS SELECT * FROM people WHERE active = 1"),
            ),
            ("sqlite", "ALTER TABLE people ADD COLUMN active INTEGER"),
            ("sqlite", "ALTER TABLE people RENAME TO persons"),
            (
                "sqlite",
                ("CREATE INDEX active_people_idx ON people(id) WHERE active = 1"),
            ),
            (
                "sqlite",
                ("CREATE VIEW active_people AS SELECT * FROM people WHERE active = 1"),
            ),
            ("sqlite", "DROP TABLE people"),
            ("sqlite", "BEGIN"),
            ("sqlite", "COMMIT"),
            ("sqlite", "ROLLBACK"),
            ("sqlite", "SAVEPOINT probe"),
            ("sqlite", "PRAGMA table_info(people)"),
            ("sqlite", "PRAGMA quick_check"),
            (
                "sqlite",
                "EXPLAIN QUERY PLAN SELECT * FROM people WHERE active = 1",
            ),
            ("sqlite", "ANALYZE"),
            ("sqlite", "VACUUM"),
            ("mysql", "TRUNCATE TABLE people"),
            ("mysql", "CREATE DATABASE analytics"),
            ("mysql", "USE analytics"),
            ("mysql", "SET SESSION sql_mode = 'STRICT_ALL_TABLES'"),
            ("mysql", "SHOW TABLES"),
            ("mysql", "DESCRIBE people"),
            ("postgres", "CREATE TYPE mood AS ENUM ('sad', 'ok', 'happy')"),
            (
                "postgres",
                "COMMENT ON TABLE people IS 'customer records'",
            ),
            ("postgres", "SET search_path TO public"),
            ("postgres", "GRANT SELECT ON TABLE people TO analyst"),
            ("postgres", "REVOKE SELECT ON TABLE people FROM analyst"),
            ("postgres", "CALL refresh_people(1)"),
            ("postgres", "COPY people (id, name) FROM STDIN"),
            (
                "mysql",
                (
                    "CREATE TABLE people ("
                    "id BIGINT AUTO_INCREMENT PRIMARY KEY, name TEXT)"
                ),
            ),
        )
        self.assertEqual(len(cases), 30)
        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                result = prepare_statement_result(
                    sql,
                    source_dialect=dialect,
                    target_dialect=dialect,
                )

                self.assertEqual(result["success"], True, result["msg"])
                self.assertEqual(
                    set(result),
                    {
                        "success",
                        "warnings",
                        "msg",
                        "envelope_type",
                        "sql_fingerprint",
                    },
                )
                self.assertEqual(result["envelope_type"], "accepted")

    def test_generic_route_does_not_run_extended_preparation(self) -> None:
        with patch(
            "sqlglot_experiments.statement_api._prepare_statement"
        ) as extended_pipeline:
            result = prepare_statement_result(
                "CREATE VIEW active_people AS SELECT * FROM people WHERE active = ?",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )

        self.assertEqual(result["success"], True)
        extended_pipeline.assert_not_called()

    def test_generic_fingerprint_is_stable_and_route_specific(self) -> None:
        def fingerprint(*, target_dialect: str) -> str:
            result = prepare_statement_result(
                "CREATE TABLE people (id INTEGER)",
                source_dialect="sqlite",
                target_dialect=target_dialect,
            )
            self.assertEqual(result["success"], True)
            return cast(AcceptedStatement, result)["sql_fingerprint"]

        sqlite_fingerprint = fingerprint(target_dialect="sqlite")

        self.assertEqual(
            sqlite_fingerprint,
            fingerprint(target_dialect="sqlite"),
        )
        self.assertNotEqual(
            sqlite_fingerprint,
            fingerprint(target_dialect="postgres"),
        )

    def test_malformed_statement_returns_our_controlled_failure(self) -> None:
        result = prepare_statement_result(
            "SELECT FROM",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            result,
            {
                "success": False,
                "warnings": False,
                "msg": "failure: invalid SQL syntax",
                "envelope_type": "failure",
            },
        )

    def test_gibberish_returns_our_controlled_failure(self) -> None:
        result = prepare_statement_result(
            "srerlct woof where",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            result,
            {
                "success": False,
                "warnings": False,
                "msg": "failure: invalid SQL syntax",
                "envelope_type": "failure",
            },
        )

    def test_external_token_error_returns_our_controlled_failure(self) -> None:
        result = prepare_statement_result(
            "SELECT $2,$1,$2",
            bindings=[4, 9],
            source_dialect="postgres",
            target_dialect="sqlite",
        )

        self.assertEqual(
            result,
            {
                "success": False,
                "warnings": False,
                "msg": "failure: invalid SQL tokens",
                "envelope_type": "failure",
            },
        )

    def test_unexpected_internal_error_escapes_as_a_defect(self) -> None:
        with (
            patch(
                "sqlglot_experiments.statement_api._prepare_statement",
                side_effect=RuntimeError("external details must not escape"),
            ),
            self.assertRaisesRegex(RuntimeError, "external details must not escape"),
        ):
            prepare_statement_result(
                "SELECT 1",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )

    def test_envelope_message_has_a_hard_compact_limit(self) -> None:
        parameter_name = "x" * 2_000
        result = prepare_statement_result(
            f"SELECT :{parameter_name}",
            bindings={},
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(result["success"], False)
        self.assertEqual(result["warnings"], False)
        self.assertLessEqual(len(result["msg"]), 240)
        self.assertTrue(result["msg"].endswith("..."))

    def test_known_invalid_calls_return_specific_fixed_envelopes(self) -> None:
        cases: tuple[tuple[str, tuple[object, ...], dict[str, object], str], ...] = (
            ("missing sql", (), {}, "failure: sql is required"),
            (
                "non-string sql",
                (42,),
                {"source_dialect": "sqlite", "target_dialect": "sqlite"},
                "failure: sql must be a string",
            ),
            (
                "blank sql",
                ("  ",),
                {"source_dialect": "sqlite", "target_dialect": "sqlite"},
                "failure: sql is required",
            ),
            (
                "missing source dialect",
                ("SELECT 1",),
                {"target_dialect": "sqlite"},
                "failure: source dialect is required",
            ),
            (
                "non-string source dialect",
                ("SELECT 1",),
                {"source_dialect": 42, "target_dialect": "sqlite"},
                "failure: source dialect must be a string",
            ),
            (
                "blank source dialect",
                ("SELECT 1",),
                {"source_dialect": "  ", "target_dialect": "sqlite"},
                "failure: source dialect is required",
            ),
            (
                "unsupported source dialect",
                ("SELECT 1",),
                {"source_dialect": "not_a_dialect", "target_dialect": "sqlite"},
                "failure: unsupported source dialect: not_a_dialect",
            ),
            (
                "missing target dialect",
                ("SELECT 1",),
                {"source_dialect": "sqlite"},
                "failure: target dialect is required",
            ),
            (
                "non-string target dialect",
                ("SELECT 1",),
                {"source_dialect": "sqlite", "target_dialect": 42},
                "failure: target dialect must be a string",
            ),
            (
                "blank target dialect",
                ("SELECT 1",),
                {"source_dialect": "sqlite", "target_dialect": "  "},
                "failure: target dialect is required",
            ),
            (
                "unsupported target dialect",
                ("SELECT 1",),
                {"source_dialect": "sqlite", "target_dialect": "not_a_dialect"},
                "failure: unsupported target dialect: not_a_dialect",
            ),
            (
                "extra positional argument",
                ("SELECT 1", "sqlite"),
                {"target_dialect": "sqlite"},
                "failure: only sql may be passed positionally",
            ),
            (
                "duplicate sql",
                ("SELECT 1",),
                {
                    "sql": "SELECT 2",
                    "source_dialect": "sqlite",
                    "target_dialect": "sqlite",
                },
                "failure: sql was provided more than once",
            ),
            (
                "unexpected argument",
                ("SELECT 1",),
                {
                    "source_dialect": "sqlite",
                    "target_dialect": "sqlite",
                    "timeout": 1,
                },
                "failure: unexpected argument: timeout",
            ),
        )

        for label, args, kwargs, message in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    call_public_api(*args, **kwargs),
                    {
                        "success": False,
                        "warnings": False,
                        "msg": message,
                        "envelope_type": "failure",
                    },
                )

    def test_sql_may_be_supplied_by_keyword(self) -> None:
        result = prepare_statement_result(
            sql="SELECT 1",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(result["success"], True)

    def test_postgres_target_uses_sqlglot_postgres_placeholder(self) -> None:
        package = prepare_statement(
            """
            SELECT * FROM main.people
            WHERE category = ? AND status = 'active'
            """,
            bindings=["sales"],
            source_dialect="sqlite",
            target_dialect="postgres",
        )

        self.assertEqual(package["dialect"], ["sqlite", "postgres"])
        self.assertEqual(
            package["sql"],
            "SELECT * FROM main.people WHERE category = %s AND status = %s",
        )
        self.assertEqual(package["bindings"], ["sales", "active"])

    def test_reused_sqlite_slot_is_expanded_for_postgres_target(self) -> None:
        package = prepare_statement(
            "SELECT :x, :x, status FROM jobs WHERE state = 'open'",
            bindings={"x": 7},
            source_dialect="sqlite",
            target_dialect="postgres",
        )

        self.assertEqual(
            package["sql"],
            "SELECT %s, %s, status FROM jobs WHERE state = %s",
        )
        self.assertEqual(package["bindings"], [7, 7, "open"])

    def test_caller_decimal_is_preserved_for_postgres_target(self) -> None:
        value = Decimal("1234567890.123456789")
        package = prepare_statement(
            "SELECT * FROM readings WHERE value = ?",
            bindings=[value],
            source_dialect="sqlite",
            target_dialect="postgres",
        )

        self.assertEqual(package["bindings"], [value])

    def test_postgres_numeric_placeholders_become_ordered_sqlite_bindings(self) -> None:
        package = prepare_statement(
            """
            SELECT * FROM jobs
            WHERE tenant = $1 AND state = $2
            """,
            bindings=[7, "open"],
            source_dialect="postgres",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package["sql"],
            "SELECT * FROM jobs WHERE tenant = ? AND state = ?",
        )
        self.assertEqual(package["bindings"], [7, "open"])

    def test_postgres_numeric_slots_are_reused_and_can_have_gaps(self) -> None:
        package = prepare_statement(
            "SELECT $2, $1, $2",
            bindings=[4, 9],
            source_dialect="postgres",
            target_dialect="sqlite",
        )

        self.assertEqual(package["sql"], "SELECT ?, ?, ?")
        self.assertEqual(package["bindings"], [9, 4, 9])
        failure = prepare_statement_result(
            "SELECT $2",
            bindings=[9],
            source_dialect="postgres",
            target_dialect="sqlite",
        )
        assert_failure(
            self,
            failure,
            message_prefix="failure: bindings:",
        )

    def test_postgres_source_mapping_is_not_inferred(self) -> None:
        failure = prepare_statement_result(
            "SELECT $1",
            bindings={"1": 7},
            source_dialect="postgres",
            target_dialect="sqlite",
        )
        assert_failure(
            self,
            failure,
            message_prefix="failure: bindings:",
        )

    def test_database_qualified_fields_remain_distinct(self) -> None:
        package = prepare_statement(
            """
            SELECT live.orders.id
            FROM live.orders
            JOIN archive.orders
              ON live.orders.id = archive.orders.id
            WHERE live.orders.status = 'open'
              AND archive.orders.status = 'closed'
            """,
            source_dialect="mysql",
            target_dialect="mysql",
        )

        self.assertEqual(package["dialect"], ["mysql", "mysql"])
        self.assertEqual(package["bindings"], ["open", "closed"])
        self.assertEqual(package["analysis"]["hardcoded_field_count"], 2)
        self.assertIn("live.orders.status = ?", package["sql"])
        self.assertIn("archive.orders.status = ?", package["sql"])


class SqliteConsumerTests(unittest.TestCase):
    def test_demo_executes_prepared_select_against_file_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_demo(Path(directory) / "demo.sqlite3")

        self.assertEqual(report["hardcoded"]["package"]["bindings"], ["sales"])
        self.assertEqual(report["parameterized"]["package"]["bindings"], ["sales"])
        self.assertEqual(report["hardcoded"]["rows"], report["parameterized"]["rows"])
        self.assertEqual(
            [row[1] for row in report["parameterized"]["rows"]],
            ["North", "West"],
        )

    def test_all_supported_statements_execute_from_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "demo.sqlite3"
            create_demo_database(database)
            calls = [
                (
                    """
                    INSERT INTO big_table (name, value, category, created_at)
                    VALUES (?, 40, 'sales', '2026-08-30')
                    """,
                    ["East"],
                ),
                ("UPDATE big_table SET value = ? WHERE name = 'East'", [41]),
                ("DELETE FROM big_table WHERE name = ?", ["South"]),
                ("SELECT name, value FROM big_table WHERE category = ?", ["sales"]),
            ]

            with sqlite3.connect(database) as connection:
                results = [
                    execute_package(
                        connection,
                        prepare_statement(
                            sql,
                            bindings=bindings,
                            source_dialect="sqlite",
                            target_dialect="sqlite",
                        ),
                    )
                    for sql, bindings in calls
                ]

        self.assertEqual(results[-1], [("North", 10), ("West", 30), ("East", 41)])

    def test_decimal_binding_is_usable_by_python_sqlite(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE readings (value REAL)")
            package = prepare_statement(
                "INSERT INTO readings (value) VALUES (?)",
                bindings=[Decimal("1.5")],
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            execute_package(connection, package)
            value = connection.execute("SELECT value FROM readings").fetchone()[0]

        self.assertEqual(package["bindings"], [1.5])
        self.assertEqual(value, 1.5)

    def test_reordered_limit_bindings_execute_with_original_meaning(self) -> None:
        source_sql = "SELECT n FROM numbers WHERE kind = ? LIMIT ?, ?"
        source_bindings = ["all", 2, 3]
        package = prepare_statement(
            source_sql,
            bindings=source_bindings,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE numbers (n INTEGER, kind TEXT)")
            connection.executemany(
                "INSERT INTO numbers VALUES (?, 'all')",
                [(number,) for number in range(10)],
            )
            direct_rows = connection.execute(source_sql, source_bindings).fetchall()
            prepared_rows = execute_package(connection, package)

        self.assertEqual(direct_rows, [(2,), (3,), (4,)])
        self.assertEqual(prepared_rows, direct_rows)

    def test_verified_complex_query_matches_direct_sqlite_execution(self) -> None:
        project_root = Path(__file__).parents[1]
        fixture = (project_root / "assets/original_source/test_fixture.sql").read_text()
        query = (
            project_root / "assets/original_source/verified_sqlite_query.sql"
        ).read_text()
        package = prepare_statement(
            query,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        with sqlite3.connect(":memory:") as connection:
            connection.executescript(fixture)
            direct_rows = connection.execute(query).fetchall()
            prepared_rows = execute_package(connection, package)

        self.assertEqual(prepared_rows, direct_rows)
        self.assertGreater(package["analysis"]["hardcoded_value_count"], 0)
        self.assertEqual(package["sql"].count("?"), len(package["bindings"]))

    def test_sqlite_numeric_cast_affinity_survives_target_rendering(self) -> None:
        sql = "SELECT CAST('3.0e5' AS NUMERIC)"
        package = prepare_statement(
            sql,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        with sqlite3.connect(":memory:") as connection:
            direct = connection.execute(sql).fetchone()
            prepared = connection.execute(
                package["sql"], package["bindings"]
            ).fetchone()

        self.assertEqual(package["sql"], "SELECT CAST('3.0e5' AS NUMERIC)")
        self.assertEqual(prepared, direct)
        self.assertIs(type(prepared[0]), int)

    def test_sqlite_cast_affinities_survive_same_dialect_rendering(self) -> None:
        for type_name in (
            "BOOLEAN",
            "BINARY",
            "VARBINARY",
            "DATE",
            "BLOB",
            "DECIMAL",
            "NUMERIC",
            "INTEGER",
            "REAL",
            "TEXT",
        ):
            with self.subTest(type_name=type_name):
                sql = (
                    f"SELECT CAST(0.5 AS {type_name}), TYPEOF(CAST(0.5 AS {type_name}))"
                )
                package = prepare_statement(
                    sql,
                    source_dialect="sqlite",
                    target_dialect="sqlite",
                )

                with sqlite3.connect(":memory:") as connection:
                    direct = connection.execute(sql).fetchone()
                    prepared = connection.execute(
                        package["sql"], package["bindings"]
                    ).fetchone()

                self.assertEqual(prepared, direct)

    def test_sqlite_partial_index_predicate_survives_target_rendering(self) -> None:
        sql = """
            SELECT customer_id
            FROM customers INDEXED BY probe_email_index
            WHERE email IS NOT NULL
              AND LOWER(email) = 'ada@example.test'
        """
        package = prepare_statement(
            sql,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        with sqlite3.connect(":memory:") as connection:
            connection.execute(
                "CREATE TABLE customers (customer_id INTEGER, email TEXT)"
            )
            connection.execute(
                """
                CREATE INDEX probe_email_index
                ON customers (LOWER(email))
                WHERE email IS NOT NULL
                """
            )
            connection.execute("INSERT INTO customers VALUES (1, 'ada@example.test')")
            prepared = connection.execute(
                package["sql"], package["bindings"]
            ).fetchall()

        self.assertIn("email IS NOT NULL", package["sql"])
        self.assertEqual(prepared, [(1,)])


if __name__ == "__main__":
    unittest.main()

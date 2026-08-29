from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlglot import ParseError

from demo.sqlite_consumer import create_demo_database, execute_package, run_demo
from sqlglot_experiments import (
    ExistingPlaceholderError,
    StatementPreparationError,
    UnsupportedStatementError,
    prepare_statement,
)


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
                "dialect": "sqlite,sqlite",
                "statement_type": "SELECT",
                "sql": (
                    "SELECT id, name, value, category, created_at "
                    "FROM big_table WHERE category = ?"
                ),
                "bindings": ["sales"],
                "analysis": {
                    "hardcoded_value_count": 1,
                    "hardcoded_field_count": 1,
                },
            },
        )

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
            {"hardcoded_value_count": 3, "hardcoded_field_count": 0},
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
            {"hardcoded_value_count": 2, "hardcoded_field_count": 1},
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
            package["analysis"],
            {"hardcoded_value_count": 0, "hardcoded_field_count": 0},
        )

    def test_existing_placeholder_is_rejected(self) -> None:
        for placeholder in ("?", ":category", "@category", "$category", "$1"):
            with (
                self.subTest(placeholder=placeholder),
                self.assertRaises(ExistingPlaceholderError),
            ):
                prepare_statement(
                    f"SELECT * FROM people WHERE category = {placeholder}",
                    source_dialect="sqlite",
                    target_dialect="sqlite",
                )

    def test_dollar_text_in_a_string_is_not_a_placeholder(self) -> None:
        package = prepare_statement(
            "SELECT '$category' AS marker FROM people WHERE id = 1",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(package["bindings"], [1])
        self.assertIn("'$category'", package["sql"])

    def test_multiple_statements_are_rejected(self) -> None:
        with self.assertRaises(StatementPreparationError):
            prepare_statement(
                "SELECT 1; SELECT 2",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )

    def test_unsupported_statement_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedStatementError):
            prepare_statement(
                "CREATE TABLE example (id INTEGER)",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )

    def test_malformed_statement_raises_sqlglot_parse_error(self) -> None:
        with self.assertRaises(ParseError):
            prepare_statement(
                "SELECT FROM",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )

    def test_source_and_target_are_required(self) -> None:
        with self.assertRaises(TypeError):
            prepare_statement("SELECT 1")  # type: ignore[call-arg]

    def test_postgres_target_uses_sqlglot_postgres_placeholder(self) -> None:
        package = prepare_statement(
            "SELECT * FROM main.people WHERE category = 'sales'",
            source_dialect="sqlite",
            target_dialect="postgres",
        )

        self.assertEqual(package["dialect"], "sqlite,postgres")
        self.assertEqual(
            package["sql"],
            "SELECT * FROM main.people WHERE category = %s",
        )
        self.assertEqual(package["bindings"], ["sales"])

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

        self.assertEqual(package["dialect"], "mysql,mysql")
        self.assertEqual(package["bindings"], ["open", "closed"])
        self.assertEqual(package["analysis"]["hardcoded_field_count"], 2)
        self.assertIn("live.orders.status = ?", package["sql"])
        self.assertIn("archive.orders.status = ?", package["sql"])


class SqliteConsumerTests(unittest.TestCase):
    def test_demo_executes_prepared_select_against_file_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_demo(Path(directory) / "demo.sqlite3")

        self.assertEqual(report["package"]["bindings"], ["sales"])
        self.assertEqual([row[1] for row in report["rows"]], ["North", "West"])

    def test_all_supported_statements_execute_from_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "demo.sqlite3"
            create_demo_database(database)
            statements = [
                """
                INSERT INTO big_table (name, value, category, created_at)
                VALUES ('East', 40, 'sales', '2026-08-30')
                """,
                "UPDATE big_table SET value = 41 WHERE name = 'East'",
                "DELETE FROM big_table WHERE name = 'South'",
                "SELECT name, value FROM big_table WHERE category = 'sales'",
            ]

            with sqlite3.connect(database) as connection:
                results = [
                    execute_package(
                        connection,
                        prepare_statement(
                            sql,
                            source_dialect="sqlite",
                            target_dialect="sqlite",
                        ),
                    )
                    for sql in statements
                ]

        self.assertEqual(results[-1], [("North", 10), ("West", 30), ("East", 41)])

    def test_decimal_binding_is_usable_by_python_sqlite(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE readings (value REAL)")
            package = prepare_statement(
                "INSERT INTO readings (value) VALUES (1.5)",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            execute_package(connection, package)
            value = connection.execute("SELECT value FROM readings").fetchone()[0]

        self.assertEqual(package["bindings"], [1.5])
        self.assertEqual(value, 1.5)

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


if __name__ == "__main__":
    unittest.main()

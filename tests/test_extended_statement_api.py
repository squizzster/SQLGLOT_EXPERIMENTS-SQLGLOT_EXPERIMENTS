from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import unittest
from typing import Any, cast
from unittest.mock import ANY

from sqlglot_experiments import PreparedStatement, prepare_statement
from sqlglot_experiments.statement_fingerprinting import fingerprint_statement


def require_prepared(result: object) -> PreparedStatement:
    package = cast(dict[str, Any], result)
    if not package["success"]:
        raise AssertionError(package["msg"])
    if package["envelope_type"] != "prepared":
        raise AssertionError(package)
    return cast(PreparedStatement, package)


class ExtendedStatementApiTests(unittest.TestCase):
    def test_replace_returns_the_fixed_prepared_envelope(self) -> None:
        package = prepare_statement(
            "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(
            package,
            {
                "success": True,
                "warnings": True,
                "msg": "warnings: replaced 2 hardcoded values with placeholders",
                "envelope_type": "prepared",
                "sql_fingerprint": ANY,
                "dialect": ["sqlite", "sqlite"],
                "statement_type": "REPLACE",
                "sql": "INSERT OR REPLACE INTO people (id, name) VALUES (?, ?)",
                "bindings": [1, "Mark"],
                "where_fields": [],
                "analysis": {
                    "hardcoded_value_count": 2,
                    "hardcoded_field_count": 2,
                    "returns_rows": False,
                    "insert": None,
                    "direct_writes": {
                        "targets": [
                            {"catalog": None, "schema": None, "table": "people"}
                        ],
                        "evidence_complete": True,
                    },
                    "existing_row_mutations": {
                        "effects": [
                            {
                                "target": {
                                    "catalog": None,
                                    "schema": None,
                                    "table": "people",
                                },
                                "updated_columns": [],
                                "deletes_rows": True,
                            }
                        ],
                        "evidence_complete": True,
                    },
                },
            },
        )

    def test_merge_returns_the_same_fixed_prepared_envelope(self) -> None:
        package = prepare_statement(
            "MERGE INTO people p USING incoming i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET name = 'Fred' "
            "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')",
            source_dialect="postgres",
            target_dialect="postgres",
        )

        self.assertEqual(
            package,
            {
                "success": True,
                "warnings": True,
                "msg": "warnings: replaced 2 hardcoded values with placeholders",
                "envelope_type": "prepared",
                "sql_fingerprint": ANY,
                "dialect": ["postgres", "postgres"],
                "statement_type": "MERGE",
                "sql": (
                    "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE SET name = %s "
                    "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, %s)"
                ),
                "bindings": ["Fred", "Fred"],
                "where_fields": [],
                "analysis": {
                    "hardcoded_value_count": 2,
                    "hardcoded_field_count": 2,
                    "returns_rows": False,
                    "insert": None,
                    "direct_writes": {
                        "targets": [
                            {"catalog": None, "schema": None, "table": "people"}
                        ],
                        "evidence_complete": True,
                    },
                    "existing_row_mutations": {
                        "effects": [
                            {
                                "target": {
                                    "catalog": None,
                                    "schema": None,
                                    "table": "people",
                                },
                                "updated_columns": ["name"],
                                "deletes_rows": False,
                            }
                        ],
                        "evidence_complete": True,
                    },
                },
            },
        )

    def test_with_reports_the_effective_statement_family(self) -> None:
        cases = (
            (
                "SELECT",
                "postgres",
                (
                    "WITH chosen AS (SELECT * FROM people WHERE id = 1) "
                    "SELECT * FROM chosen"
                ),
            ),
            (
                "INSERT",
                "postgres",
                (
                    "WITH chosen AS (SELECT id, name FROM incoming) "
                    "INSERT INTO people SELECT * FROM chosen"
                ),
            ),
            (
                "UPDATE",
                "postgres",
                (
                    "WITH chosen AS (SELECT id FROM incoming) "
                    "UPDATE people SET active = FALSE FROM chosen "
                    "WHERE people.id = chosen.id"
                ),
            ),
            (
                "DELETE",
                "postgres",
                (
                    "WITH chosen AS (SELECT id FROM incoming) "
                    "DELETE FROM people USING chosen WHERE people.id = chosen.id"
                ),
            ),
            (
                "MERGE",
                "postgres",
                (
                    "WITH incoming AS (SELECT 1 AS id) "
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN DELETE"
                ),
            ),
            (
                "REPLACE",
                "sqlite",
                (
                    "WITH incoming(id, name) AS (SELECT 1, 'Fred') "
                    "REPLACE INTO people SELECT * FROM incoming"
                ),
            ),
        )

        for expected_type, dialect, sql in cases:
            with self.subTest(expected_type=expected_type):
                package = require_prepared(
                    prepare_statement(
                        sql,
                        source_dialect=dialect,
                        target_dialect=dialect,
                    )
                )
                self.assertEqual(package["statement_type"], expected_type)
                self.assertNotEqual(package["statement_type"], "WITH")

    def test_mysql_replace_forms_preserve_replace_identity(self) -> None:
        cases = (
            "REPLACE INTO people (id, name) VALUE (1, 'Mark')",
            "REPLACE LOW_PRIORITY INTO people (id, name) VALUES (1, 'Mark')",
            "REPLACE INTO people (id, name) VALUES ROW(1, 'Mark')",
            "REPLACE INTO people SET id = 1, name = 'Mark'",
            "REPLACE INTO people TABLE incoming",
            (
                "REPLACE INTO people (id, name) "
                "WITH incoming AS (SELECT 1 AS id, 'Mark' AS name) "
                "SELECT id, name FROM incoming"
            ),
        )

        for sql in cases:
            with self.subTest(sql=sql):
                package = require_prepared(
                    prepare_statement(
                        sql,
                        source_dialect="mysql",
                        target_dialect="mysql",
                    )
                )
                self.assertEqual(package["statement_type"], "REPLACE")
                self.assertTrue(package["sql"].startswith("REPLACE"))

    def test_mysql_replace_set_is_not_normalized_to_values(self) -> None:
        package = require_prepared(
            prepare_statement(
                "REPLACE INTO people SET id = 1, counter = counter + 1",
                source_dialect="mysql",
                target_dialect="mysql",
            )
        )

        self.assertEqual(
            package["sql"],
            "REPLACE INTO people SET id = ?, counter = counter + 1",
        )
        self.assertEqual(package["bindings"], [1])

    def test_scalar_replace_function_remains_select(self) -> None:
        package = require_prepared(
            prepare_statement(
                "SELECT REPLACE(name, 'a', 'b') FROM people WHERE id = 1",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        )

        self.assertEqual(package["statement_type"], "SELECT")

    def test_replace_parameter_forms_use_the_existing_binding_pipeline(self) -> None:
        package = require_prepared(
            prepare_statement(
                "REPLACE INTO people (id, name, alias) VALUES (?2, :name, :name)",
                bindings=["ignored-slot-one", 7, "Mark"],
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        )

        self.assertEqual(package["statement_type"], "REPLACE")
        self.assertEqual(
            package["sql"],
            "INSERT OR REPLACE INTO people (id, name, alias) VALUES (?, ?, ?)",
        )
        self.assertEqual(package["bindings"], [7, "Mark", "Mark"])

    def test_merge_bindings_follow_generated_clause_order(self) -> None:
        package = require_prepared(
            prepare_statement(
                "MERGE INTO people p USING incoming i "
                "ON p.id = i.id AND p.tenant_id = $1 "
                "WHEN MATCHED AND p.active = TRUE "
                "THEN UPDATE SET name = $2, active = FALSE "
                "WHEN NOT MATCHED THEN INSERT (id, name, active) "
                "VALUES (i.id, $2, TRUE)",
                bindings=[7, "Fred"],
                source_dialect="postgres",
                target_dialect="postgres",
            )
        )

        self.assertEqual(
            package["sql"],
            "MERGE INTO people AS p USING incoming AS i "
            "ON p.id = i.id AND p.tenant_id = %s "
            "WHEN MATCHED AND p.active = %s "
            "THEN UPDATE SET name = %s, active = %s "
            "WHEN NOT MATCHED THEN INSERT (id, name, active) "
            "VALUES (i.id, %s, %s)",
        )
        self.assertEqual(package["bindings"], [7, True, "Fred", False, "Fred", True])

    def test_configured_merge_dialect_extensions_round_trip(self) -> None:
        cases = (
            (
                "duckdb",
                (
                    "MERGE INTO people p USING incoming i USING (id) "
                    "WHEN MATCHED THEN UPDATE "
                    "WHEN NOT MATCHED THEN INSERT BY NAME"
                ),
                "INSERT BY NAME",
            ),
            (
                "snowflake",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE ALL BY NAME "
                    "WHEN NOT MATCHED THEN INSERT ALL BY NAME"
                ),
                "ALL BY NAME",
            ),
            (
                "databricks",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE SET * "
                    "WHEN NOT MATCHED THEN INSERT *"
                ),
                "UPDATE SET *",
            ),
            (
                "bigquery",
                (
                    "MERGE people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT ROW"
                ),
                "INSERT ROW",
            ),
            (
                "oracle",
                (
                    "MERGE INTO people p USING incoming i ON (p.id = i.id) "
                    "WHEN MATCHED THEN UPDATE SET p.name = 'Fred' "
                    "WHEN NOT MATCHED THEN INSERT (id, name) "
                    "VALUES (i.id, 'Fred')"
                ),
                "WHEN MATCHED",
            ),
            (
                "tsql",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE SET p.name = 'Fred' "
                    "WHEN NOT MATCHED BY SOURCE THEN DELETE OUTPUT $action;"
                ),
                "OUTPUT $action",
            ),
        )

        for dialect, sql, fragment in cases:
            with self.subTest(dialect=dialect):
                package = require_prepared(
                    prepare_statement(
                        sql,
                        source_dialect=dialect,
                        target_dialect=dialect,
                    )
                )
                self.assertEqual(package["statement_type"], "MERGE")
                self.assertIn(fragment, package["sql"])
                if dialect == "tsql":
                    self.assertTrue(package["sql"].endswith(";"))

    def test_postgres_merge_by_source_uses_the_same_pipeline(self) -> None:
        package = require_prepared(
            prepare_statement(
                "MERGE INTO people p USING incoming i ON p.id = i.id "
                "WHEN NOT MATCHED BY SOURCE THEN DELETE",
                source_dialect="postgres",
                target_dialect="postgres",
            )
        )

        self.assertEqual(package["statement_type"], "MERGE")
        self.assertIn("WHEN NOT MATCHED BY SOURCE THEN DELETE", package["sql"])

    def test_dialect_specific_merge_actions_are_preserved(self) -> None:
        postgres = require_prepared(
            prepare_statement(
                "MERGE INTO people p USING incoming i ON p.id = i.id "
                "WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
                source_dialect="postgres",
                target_dialect="postgres",
            )
        )
        oracle = require_prepared(
            prepare_statement(
                "MERGE INTO people p USING incoming i ON (p.id = i.id) "
                "WHEN MATCHED THEN UPDATE SET p.name = 'Fred' "
                "WHERE i.active = 1",
                source_dialect="oracle",
                target_dialect="oracle",
            )
        )

        self.assertIn("INSERT DEFAULT VALUES", postgres["sql"])
        self.assertIn("UPDATE SET p.name = ? WHERE i.active = ?", oracle["sql"])

    def test_merge_source_query_where_fields_resolve_to_physical_source(self) -> None:
        package = require_prepared(
            prepare_statement(
                "MERGE INTO main.people p USING ("
                "SELECT * FROM staging.incoming WHERE tenant_id = 7"
                ") i ON p.id = i.id "
                "WHEN MATCHED AND p.active = FALSE THEN UPDATE SET name = 'Fred'",
                source_dialect="duckdb",
                target_dialect="duckdb",
            )
        )

        self.assertEqual(package["where_fields"], ["staging.incoming.tenant_id"])

    def test_cross_dialect_extension_loss_is_a_controlled_failure(self) -> None:
        cases = (
            (
                "bigquery",
                "postgres",
                (
                    "MERGE people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT ROW"
                ),
            ),
            (
                "duckdb",
                "snowflake",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT BY NAME"
                ),
            ),
            (
                "duckdb",
                "bigquery",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN DELETE RETURNING merge_action, *"
                ),
            ),
            (
                "sqlite",
                "postgres",
                "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
            ),
        )

        for source, target, sql in cases:
            with self.subTest(source=source, target=target):
                package = cast(
                    dict[str, Any],
                    prepare_statement(
                        sql,
                        source_dialect=source,
                        target_dialect=target,
                    ),
                )
                self.assertEqual(package["success"], False)
                self.assertEqual(package["envelope_type"], "failure")
                self.assertEqual(
                    set(package),
                    {"success", "warnings", "msg", "envelope_type"},
                )

    def test_common_extended_forms_cross_configured_dialects(self) -> None:
        merge_sql = (
            "MERGE INTO people p USING incoming i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET name = 'Fred' "
            "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id, 'Fred')"
        )
        replace_sql = "REPLACE INTO people (id, name) VALUES (1, 'Fred')"
        cases = (
            ("MERGE", "postgres", "duckdb", merge_sql),
            ("MERGE", "duckdb", "postgres", merge_sql),
            ("MERGE", "postgres", "snowflake", merge_sql),
            ("REPLACE", "sqlite", "duckdb", replace_sql),
            (
                "REPLACE",
                "duckdb",
                "sqlite",
                "INSERT OR REPLACE INTO people (id, name) VALUES (1, 'Fred')",
            ),
            ("REPLACE", "sqlite", "mysql", replace_sql),
            ("REPLACE", "mysql", "sqlite", replace_sql),
        )

        for expected_type, source, target, sql in cases:
            with self.subTest(source=source, target=target):
                package = require_prepared(
                    prepare_statement(
                        sql,
                        source_dialect=source,
                        target_dialect=target,
                    )
                )
                self.assertEqual(package["statement_type"], expected_type)
                self.assertEqual(package["dialect"], [source, target])

    def test_unadapted_vendor_extensions_are_controlled_failures(self) -> None:
        cases = (
            (
                "mysql",
                (
                    "REPLACE /*+ NO_RANGE_OPTIMIZATION(people PRIMARY) */ "
                    "INTO people (id) VALUES (1)"
                ),
            ),
            (
                "tsql",
                (
                    "MERGE TOP (10) PERCENT INTO people p USING incoming i "
                    "ON p.id = i.id WHEN MATCHED THEN DELETE;"
                ),
            ),
            (
                "oracle",
                (
                    "MERGE INTO people p USING incoming i ON (p.id = i.id) "
                    "WHEN MATCHED THEN UPDATE SET p.name = 'Fred' "
                    "DELETE WHERE p.active = 0"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT (id) OVERRIDING SYSTEM VALUE "
                    "VALUES (i.id)"
                ),
            ),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect):
                package = cast(
                    dict[str, Any],
                    prepare_statement(
                        sql,
                        source_dialect=dialect,
                        target_dialect=dialect,
                    ),
                )
                self.assertEqual(package["success"], False, package)
                self.assertEqual(
                    set(package),
                    {"success", "warnings", "msg", "envelope_type"},
                )
                if dialect == "mysql":
                    self.assertEqual(
                        package["msg"],
                        "failure: MySQL REPLACE optimizer hints cannot be preserved",
                    )

    def test_known_malformed_extended_forms_are_controlled_failures(self) -> None:
        cases = (
            ("sqlite", "REPLACE INTO people"),
            ("mysql", "REPLACE INTO people"),
            ("sqlite", "REPLACE people (id) VALUES (1)"),
            ("sqlite", "REPLACE OR IGNORE INTO people (id) VALUES (1)"),
            ("sqlite", "REPLACE INTO people SET id = 1"),
            ("sqlite", "REPLACE INTO people (id) VALUES ROW(1)"),
            ("mysql", "REPLACE IGNORE INTO people (id) VALUES (1)"),
            ("mysql", "REPLACE OR IGNORE INTO people (id) VALUES (1)"),
            (
                "mysql",
                "REPLACE INTO people SET id = 1, name = DEFAULT(id)",
            ),
            (
                "mysql",
                ("REPLACE INTO people (id) VALUES (1) ON DUPLICATE KEY UPDATE id = 2"),
            ),
            (
                "postgres",
                ("MERGE INTO people p USING incoming i ON p.id = i.id"),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN THEN DELETE"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED DELETE"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT THEN INSERT (id) VALUES (1)"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE SET name = 'Fred' "
                    "WHERE p.active = TRUE"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT (id) VALUES (i.id) "
                    "WHERE i.active = TRUE"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN INSERT (id) VALUES (i.id)"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN DELETE"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (i.id)"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE *"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT *"
                ),
            ),
            (
                "bigquery",
                (
                    "MERGE people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT *"
                ),
            ),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED BY TARGET THEN DELETE"
                ),
            ),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                package = cast(
                    dict[str, Any],
                    prepare_statement(
                        sql,
                        source_dialect=dialect,
                        target_dialect=dialect,
                    ),
                )
                self.assertEqual(package["success"], False, package)
                self.assertEqual(package["envelope_type"], "failure")

    def test_fingerprints_ignore_replace_and_merge_values(self) -> None:
        replace_literal = fingerprint_statement(
            "REPLACE INTO people (id, name) VALUES (1, 'Mark')",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        replace_placeholders = fingerprint_statement(
            "REPLACE INTO people (id, name) VALUES (?, ?)",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        merge_fred = fingerprint_statement(
            "MERGE INTO people p USING incoming i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET name = 'Fred'",
            source_dialect="postgres",
            target_dialect="postgres",
        )
        merge_mark = fingerprint_statement(
            "MERGE INTO people p USING incoming i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET name = 'Mark'",
            source_dialect="postgres",
            target_dialect="postgres",
        )

        self.assertEqual(replace_literal, replace_placeholders)
        self.assertEqual(merge_fred, merge_mark)

    def test_cached_extended_structure_never_reuses_caller_values(self) -> None:
        first = require_prepared(
            prepare_statement(
                "REPLACE INTO people (id, name) VALUES (?, ?)",
                bindings=[1, "First"],
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        )
        second = require_prepared(
            prepare_statement(
                "REPLACE INTO people (id, name) VALUES (?, ?)",
                bindings=[2, "Second"],
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        )

        self.assertEqual(first["bindings"], [1, "First"])
        self.assertEqual(second["bindings"], [2, "Second"])
        self.assertEqual(first["sql_fingerprint"], second["sql_fingerprint"])


class SqliteReplaceExecutionTests(unittest.TestCase):
    def test_prepared_replace_executes_with_native_replace_semantics(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO people VALUES (1, 'Old')")

        package = require_prepared(
            prepare_statement(
                "REPLACE INTO people (id, name) VALUES (1, 'New')",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        )
        connection.execute(package["sql"], package["bindings"])

        self.assertEqual(
            connection.execute("SELECT id, name FROM people").fetchall(),
            [(1, "New")],
        )

    def test_replace_select_keeps_a_column_named_set(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(
            'CREATE TABLE src ("set" INTEGER);'
            'CREATE TABLE dst ("set" INTEGER PRIMARY KEY);'
            "INSERT INTO src VALUES (7);"
        )
        package = require_prepared(
            prepare_statement(
                'REPLACE INTO dst SELECT "set" FROM src',
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        )
        connection.execute(package["sql"], package["bindings"])

        self.assertEqual(
            connection.execute('SELECT "set" FROM dst').fetchall(),
            [(7,)],
        )


@unittest.skipUnless(importlib.util.find_spec("duckdb"), "duckdb is optional")
class DuckDbExtendedExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.duckdb = importlib.import_module("duckdb")

    def test_prepared_replace_executes(self) -> None:
        connection = self.duckdb.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR)")
        connection.execute("INSERT INTO people VALUES (1, 'Old')")
        package = require_prepared(
            prepare_statement(
                "INSERT OR REPLACE INTO people VALUES (1, 'New')",
                source_dialect="duckdb",
                target_dialect="duckdb",
            )
        )

        connection.execute(package["sql"], package["bindings"])

        self.assertEqual(
            connection.execute("SELECT * FROM people").fetchall(),
            [(1, "New")],
        )

    def test_prepared_merge_matches_direct_execution(self) -> None:
        sql = (
            "MERGE INTO people p USING incoming i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET name = 'Updated' "
            "WHEN NOT MATCHED THEN INSERT BY NAME"
        )

        def connection_with_data() -> Any:
            connection = self.duckdb.connect(":memory:")
            connection.execute(
                "CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR)"
            )
            connection.execute("CREATE TABLE incoming (id INTEGER, name VARCHAR)")
            connection.execute("INSERT INTO people VALUES (1, 'Old')")
            connection.execute("INSERT INTO incoming VALUES (1, 'Source'), (2, 'New')")
            return connection

        direct = connection_with_data()
        prepared = connection_with_data()
        self.addCleanup(direct.close)
        self.addCleanup(prepared.close)
        direct.execute(sql)
        package = require_prepared(
            prepare_statement(
                sql,
                source_dialect="duckdb",
                target_dialect="duckdb",
            )
        )
        prepared.execute(package["sql"], package["bindings"])

        self.assertEqual(
            prepared.execute("SELECT * FROM people ORDER BY id").fetchall(),
            direct.execute("SELECT * FROM people ORDER BY id").fetchall(),
        )


if __name__ == "__main__":
    unittest.main()

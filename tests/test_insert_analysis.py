from __future__ import annotations

import unittest
from typing import cast

from sqlglot_experiments import PreparedStatement, prepare_statement


def prepared(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str | None = None,
) -> PreparedStatement:
    package = prepare_statement(
        sql,
        source_dialect=source_dialect,
        target_dialect=target_dialect or source_dialect,
    )
    if not package["success"] or package["envelope_type"] != "prepared":
        raise AssertionError(package)
    return cast(PreparedStatement, package)


class InsertAnalysisTests(unittest.TestCase):
    def test_explicit_target_and_supplied_columns_are_reported(self) -> None:
        package = prepared(
            "INSERT INTO people (lookup_code, payload) VALUES ('A', 1)",
            source_dialect="sqlite",
        )

        self.assertEqual(
            package["analysis"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": None,
                    "table": "people",
                },
                "supplied_columns": ["lookup_code", "payload"],
                "plain_values_binding_rows": [[0, 1]],
            },
        )

    def test_quoted_dots_remain_identifier_content_not_qualification(self) -> None:
        package = prepared(
            'INSERT INTO "inventory.log" ("select", "sku.code") VALUES (1, \'A-1\')',
            source_dialect="sqlite",
        )

        self.assertEqual(
            package["analysis"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": None,
                    "table": "inventory.log",
                },
                "supplied_columns": ["select", "sku.code"],
                "plain_values_binding_rows": [[0, 1]],
            },
        )

    def test_qualification_is_structured_instead_of_flattened(self) -> None:
        mysql = prepared(
            "INSERT INTO reporting.people (lookup_code) VALUES ('A')",
            source_dialect="mysql",
        )
        snowflake = prepared(
            "INSERT INTO analytics.public.people (lookup_code) VALUES ('A')",
            source_dialect="snowflake",
        )

        self.assertEqual(
            mysql["analysis"]["insert"]["target"],  # type: ignore[index]
            {"catalog": None, "schema": "reporting", "table": "people"},
        )
        self.assertEqual(
            snowflake["analysis"]["insert"]["target"],  # type: ignore[index]
            {
                "catalog": "analytics",
                "schema": "public",
                "table": "people",
            },
        )

    def test_static_facts_ignore_later_execution_semantics(self) -> None:
        cases = (
            (
                "sqlite",
                (
                    "INSERT INTO people (lookup_code, payload) VALUES ('A', 1), "
                    "('B', 2) ON CONFLICT(lookup_code) DO UPDATE "
                    "SET payload = excluded.payload RETURNING lookup_code"
                ),
            ),
            (
                "mysql",
                (
                    "INSERT IGNORE INTO people (lookup_code, payload) "
                    "VALUES ('A', 1) ON DUPLICATE KEY UPDATE "
                    "payload = VALUES(payload)"
                ),
            ),
            (
                "postgres",
                (
                    "WITH incoming AS (SELECT 'A' AS lookup_code, 1 AS payload) "
                    "INSERT INTO people (lookup_code, payload) SELECT * FROM incoming"
                ),
            ),
            (
                "mysql",
                "INSERT INTO people SET lookup_code = 'A', payload = 1",
            ),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                package = prepared(sql, source_dialect=dialect)
                analysis = package["analysis"]["insert"]
                self.assertIsNotNone(analysis)
                assert analysis is not None
                self.assertEqual(analysis["target"]["table"], "people")
                self.assertEqual(
                    analysis["supplied_columns"],
                    ["lookup_code", "payload"],
                )
                self.assertIsNone(analysis["plain_values_binding_rows"])

    def test_absent_column_ownership_is_empty_and_never_invented(self) -> None:
        cases = (
            ("sqlite", "INSERT INTO people VALUES ('A', 1)"),
            ("sqlite", "INSERT INTO people DEFAULT VALUES"),
            ("duckdb", "INSERT INTO people BY NAME SELECT 'A' AS lookup_code"),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                package = prepared(sql, source_dialect=dialect)
                analysis = package["analysis"]["insert"]
                self.assertIsNotNone(analysis)
                assert analysis is not None
                self.assertEqual(analysis["target"]["table"], "people")
                self.assertEqual(analysis["supplied_columns"], [])

    def test_duplicate_columns_remain_visible_for_consumer_rejection(self) -> None:
        package = prepared(
            "INSERT INTO people (lookup_code, lookup_code) VALUES ('A', 'B')",
            source_dialect="sqlite",
        )

        analysis = package["analysis"]["insert"]
        self.assertIsNotNone(analysis)
        assert analysis is not None
        self.assertEqual(
            analysis["supplied_columns"],
            ["lookup_code", "lookup_code"],
        )

    def test_postgres_target_alias_keeps_the_insert_column_list(self) -> None:
        package = prepared(
            "INSERT INTO public.people AS destination (lookup_code, payload) "
            "VALUES ('A', 1)",
            source_dialect="postgres",
        )

        self.assertEqual(
            package["analysis"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": "public",
                    "table": "people",
                },
                "supplied_columns": ["lookup_code", "payload"],
                "plain_values_binding_rows": [[0, 1]],
            },
        )

    def test_vendor_insert_forms_keep_target_owned_facts(self) -> None:
        cases = (
            (
                "mysql",
                (
                    "INSERT INTO people PARTITION (p0, p1) "
                    "(lookup_code, payload) VALUES ('A', 1)"
                ),
                (None, None, "people"),
            ),
            (
                "snowflake",
                (
                    "INSERT OVERWRITE INTO analytics.public.people "
                    "(lookup_code, payload) VALUES ('A', 1)"
                ),
                ("analytics", "public", "people"),
            ),
            (
                "databricks",
                (
                    "INSERT INTO hive_metastore.default.people "
                    "PARTITION (day = '2026-08-31') "
                    "(lookup_code, payload) VALUES ('A', 1)"
                ),
                ("hive_metastore", "default", "people"),
            ),
            (
                "oracle",
                (
                    "INSERT /*+ APPEND */ INTO reporting.people "
                    "(lookup_code, payload) VALUES ('A', 1)"
                ),
                (None, "reporting", "people"),
            ),
            (
                "tsql",
                (
                    "INSERT INTO dbo.people (lookup_code, payload) "
                    "OUTPUT inserted.lookup_code VALUES ('A', 1)"
                ),
                (None, "dbo", "people"),
            ),
        )

        for dialect, sql, expected_target in cases:
            with self.subTest(dialect=dialect):
                package = prepared(sql, source_dialect=dialect)
                analysis = package["analysis"]["insert"]
                self.assertIsNotNone(analysis)
                assert analysis is not None
                target = analysis["target"]
                self.assertEqual(
                    (target["catalog"], target["schema"], target["table"]),
                    expected_target,
                )
                self.assertEqual(
                    analysis["supplied_columns"],
                    ["lookup_code", "payload"],
                )

    def test_quoted_multi_part_targets_do_not_flatten_identifier_content(self) -> None:
        cases = (
            (
                "duckdb",
                (
                    'INSERT INTO "memory.db"."odd.schema"."order" '
                    '("a.b", "select") VALUES (1, 2)'
                ),
            ),
            (
                "databricks",
                (
                    "INSERT INTO `memory.db`.`odd.schema`.`order` "
                    "(`a.b`, `select`) VALUES (1, 2)"
                ),
            ),
            (
                "tsql",
                (
                    "INSERT INTO [memory.db].[odd.schema].[order] "
                    "([a.b], [select]) VALUES (1, 2)"
                ),
            ),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect):
                package = prepared(sql, source_dialect=dialect)
                analysis = package["analysis"]["insert"]
                self.assertIsNotNone(analysis)
                assert analysis is not None
                self.assertEqual(
                    analysis["target"],
                    {
                        "catalog": "memory.db",
                        "schema": "odd.schema",
                        "table": "order",
                    },
                )
                self.assertEqual(
                    analysis["supplied_columns"],
                    ["a.b", "select"],
                )

    def test_cross_dialect_rendering_retains_target_owned_facts(self) -> None:
        cases = (
            ("sqlite", "postgres", "main"),
            ("postgres", "sqlite", "public"),
            ("mysql", "sqlite", "reporting"),
            ("oracle", "tsql", "reporting"),
        )

        for source, target, schema in cases:
            with self.subTest(source=source, target=target):
                package = prepared(
                    f"INSERT INTO {schema}.people (lookup_code, payload) "
                    "VALUES ('A', 1)",
                    source_dialect=source,
                    target_dialect=target,
                )
                self.assertEqual(
                    package["analysis"]["insert"],
                    {
                        "target": {
                            "catalog": None,
                            "schema": schema,
                            "table": "people",
                        },
                        "supplied_columns": ["lookup_code", "payload"],
                        "plain_values_binding_rows": [[0, 1]],
                    },
                )

    def test_plain_values_rows_map_cells_to_returned_binding_indexes(self) -> None:
        package = prepare_statement(
            "INSERT INTO people (row_id, lookup_code, qty, note) "
            "VALUES (:first_id, 'A-1', 3, 'fresh'), (2, 'B-2', 1, 'new')",
            bindings={"first_id": 99},
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        if not package["success"] or package["envelope_type"] != "prepared":
            raise AssertionError(package)

        self.assertEqual(
            package["bindings"],
            [99, "A-1", 3, "fresh", 2, "B-2", 1, "new"],
        )
        analysis = package["analysis"]["insert"]
        self.assertIsNotNone(analysis)
        assert analysis is not None
        self.assertEqual(
            analysis["plain_values_binding_rows"],
            [[0, 1, 2, 3], [4, 5, 6, 7]],
        )

    def test_modified_or_computed_insert_has_no_plain_value_mapping(self) -> None:
        cases = (
            (
                "sqlite",
                (
                    "INSERT INTO people (lookup_code, payload) VALUES ('A', 1) "
                    "ON CONFLICT(lookup_code) DO UPDATE "
                    "SET payload = excluded.payload"
                ),
            ),
            (
                "sqlite",
                "INSERT INTO people (lookup_code) VALUES (upper('a'))",
            ),
            (
                "sqlite",
                "INSERT INTO people (lookup_code) SELECT 'A'",
            ),
            (
                "sqlite",
                "INSERT OR IGNORE INTO people (lookup_code) VALUES ('A')",
            ),
            (
                "postgres",
                "INSERT INTO people (lookup_code) VALUES ('A') RETURNING lookup_code",
            ),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                package = prepared(sql, source_dialect=dialect)
                analysis = package["analysis"]["insert"]
                self.assertIsNotNone(analysis)
                assert analysis is not None
                self.assertIsNone(analysis["plain_values_binding_rows"])

    def test_all_supported_insert_dialects_share_the_contract(self) -> None:
        for dialect in (
            "bigquery",
            "databricks",
            "duckdb",
            "mysql",
            "oracle",
            "postgres",
            "snowflake",
            "sqlite",
            "tsql",
        ):
            with self.subTest(dialect=dialect):
                package = prepared(
                    "INSERT INTO people (lookup_code, payload) VALUES ('A', 1)",
                    source_dialect=dialect,
                )
                analysis = package["analysis"]["insert"]
                self.assertIsNotNone(analysis)
                assert analysis is not None
                self.assertEqual(analysis["target"]["table"], "people")
                self.assertEqual(
                    analysis["supplied_columns"],
                    ["lookup_code", "payload"],
                )

    def test_non_insert_families_never_receive_insert_facts(self) -> None:
        cases = (
            ("sqlite", "SELECT * FROM people"),
            ("sqlite", "UPDATE people SET lookup_code = 'A'"),
            ("sqlite", "DELETE FROM people"),
            ("sqlite", "REPLACE INTO people (lookup_code) VALUES ('A')"),
            (
                "postgres",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT (id) VALUES (i.id)"
                ),
            ),
        )

        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                package = prepared(sql, source_dialect=dialect)
                self.assertIsNone(package["analysis"]["insert"])


if __name__ == "__main__":
    unittest.main()

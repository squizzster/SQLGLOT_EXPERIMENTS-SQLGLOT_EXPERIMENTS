from __future__ import annotations

import unittest

from sqlglot_experiments import prepare_statement


class ResultRowAnalysisTests(unittest.TestCase):
    def test_query_and_explicit_result_projection_return_rows(self) -> None:
        cases = (
            ("sqlite", "SELECT id FROM people", "SELECT"),
            (
                "sqlite",
                "UPDATE people SET active = 1 RETURNING id",
                "UPDATE",
            ),
            ("sqlite", "DELETE FROM people RETURNING id", "DELETE"),
            (
                "sqlite",
                "REPLACE INTO people (id) VALUES (1) RETURNING id",
                "REPLACE",
            ),
            (
                "duckdb",
                (
                    "MERGE INTO people USING incoming ON people.id = incoming.id "
                    "WHEN MATCHED THEN DELETE RETURNING people.id"
                ),
                "MERGE",
            ),
        )

        for dialect, statement, statement_type in cases:
            with self.subTest(dialect=dialect, statement_type=statement_type):
                package = prepare_statement(
                    statement,
                    source_dialect=dialect,
                    target_dialect=dialect,
                )
                self.assertTrue(package["success"], package)
                if not package["success"] or package["envelope_type"] != "prepared":
                    self.fail(str(package))
                self.assertEqual(package["statement_type"], statement_type)
                self.assertIs(package["analysis"]["returns_rows"], True)

    def test_nonreturning_writes_do_not_return_rows(self) -> None:
        cases = (
            ("sqlite", "INSERT INTO people (id) VALUES (1)"),
            ("sqlite", "UPDATE people SET active = 1"),
            ("sqlite", "DELETE FROM people"),
            ("sqlite", "REPLACE INTO people (id) VALUES (1)"),
            (
                "duckdb",
                (
                    "MERGE INTO people USING incoming ON people.id = incoming.id "
                    "WHEN MATCHED THEN DELETE"
                ),
            ),
        )

        for dialect, statement in cases:
            with self.subTest(dialect=dialect, statement=statement):
                package = prepare_statement(
                    statement,
                    source_dialect=dialect,
                    target_dialect=dialect,
                )
                self.assertTrue(package["success"], package)
                if not package["success"] or package["envelope_type"] != "prepared":
                    self.fail(str(package))
                self.assertIs(package["analysis"]["returns_rows"], False)

    def test_lexical_decoys_do_not_create_result_intent(self) -> None:
        package = prepare_statement(
            "UPDATE people SET note = 'RETURNING id' /* RETURNING id */",
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertTrue(package["success"], package)
        if not package["success"] or package["envelope_type"] != "prepared":
            self.fail(str(package))
        self.assertIs(package["analysis"]["returns_rows"], False)


if __name__ == "__main__":
    unittest.main()

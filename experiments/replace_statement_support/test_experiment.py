from __future__ import annotations

import unittest
from typing import Any, cast

from experiments.replace_statement_support.run_experiment import (
    CASES,
    SQLITE_EXECUTION_CASES,
    replace_support,
    run_experiment,
)
from sqlglot_experiments import prepare_statement


class ReplaceStatementExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_experiment()

    def test_matrix_is_adversarial_and_every_expectation_is_met(self) -> None:
        self.assertEqual(len(CASES), 37)
        self.assertEqual(
            self.report["summary"]["expectation_failure_count"],
            0,
        )

    def test_successes_are_prepared_replace_packages(self) -> None:
        successes = [
            result["package"]
            for result in self.report["results"]
            if result["package"]["success"]
        ]
        self.assertEqual(len(successes), 31)
        self.assertTrue(
            all(package["envelope_type"] == "prepared" for package in successes)
        )
        self.assertTrue(
            all(package["statement_type"] == "REPLACE" for package in successes)
        )

    def test_mysql_row_constructor_is_an_observed_unsafe_success(self) -> None:
        self.assertEqual(
            self.report["summary"]["known_unsafe_success_count"],
            1,
        )
        result = next(
            result
            for result in self.report["results"]
            if result["name"] == "mysql.row_constructor.observation"
        )
        self.assertEqual(result["package"]["success"], True)
        self.assertEqual(result["package"]["bindings"], [])
        self.assertIn("VALUES (ROW(", result["package"]["sql"])

    def test_all_sqlite_execution_cases_are_equivalent(self) -> None:
        self.assertEqual(len(SQLITE_EXECUTION_CASES), 10)
        self.assertEqual(
            self.report["summary"]["sqlite_equivalent_count"],
            len(SQLITE_EXECUTION_CASES),
        )

    def test_alias_spellings_and_values_share_a_fingerprint(self) -> None:
        self.assertEqual(
            self.report["summary"]["fingerprint_converges"],
            True,
        )

    def test_experimental_patches_do_not_escape_the_context(self) -> None:
        sql = "REPLACE INTO people (id, name) VALUES (1, 'Mark')"
        before = prepare_statement(
            sql,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        with replace_support():
            during = prepare_statement(
                sql,
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        after = prepare_statement(
            sql,
            source_dialect="sqlite",
            target_dialect="sqlite",
        )

        self.assertEqual(before["envelope_type"], "accepted")
        self.assertEqual(during["envelope_type"], "prepared")
        self.assertEqual(after["envelope_type"], "accepted")

    def test_scalar_replace_function_and_normal_dml_are_unchanged(self) -> None:
        with replace_support():
            scalar = prepare_statement(
                "SELECT REPLACE(name, 'a', 'b') FROM people WHERE id = 1",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            insert = prepare_statement(
                "INSERT INTO people (id, name) VALUES (1, 'Mark')",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            update = prepare_statement(
                "UPDATE people SET name = 'Mark' WHERE id = 1",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )

        self.assertEqual(
            cast(dict[str, Any], scalar)["statement_type"],
            "SELECT",
        )
        self.assertEqual(
            cast(dict[str, Any], insert)["statement_type"],
            "INSERT",
        )
        self.assertEqual(
            cast(dict[str, Any], update)["statement_type"],
            "UPDATE",
        )


if __name__ == "__main__":
    unittest.main()

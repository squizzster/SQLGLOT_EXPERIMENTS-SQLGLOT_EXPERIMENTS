from __future__ import annotations

import unittest
from typing import Any, cast

from experiments.merge_with_statement_support.run_experiment import (
    DUCKDB_EXECUTION_CASES,
    MERGE_CASES,
    WITH_CASES,
    run_experiment,
)
from sqlglot_experiments import prepare_statement


class MergeWithStatementExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_experiment()

    def test_matrix_is_adversarial_and_every_expectation_is_met(self) -> None:
        self.assertEqual(len(WITH_CASES), 13)
        self.assertEqual(len(MERGE_CASES), 20)
        self.assertEqual(
            self.report["summary"]["expectation_failure_count"],
            0,
        )

    def test_with_is_preserved_as_a_clause_on_the_effective_statement(self) -> None:
        with_results = self.report["results"][: len(WITH_CASES)]
        successful_types = {
            result["package"]["statement_type"]
            for result in with_results
            if result["package"]["success"]
        }
        self.assertEqual(
            successful_types,
            {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"},
        )
        self.assertEqual(
            self.report["summary"]["with_statement_type_count"],
            0,
        )

    def test_merge_packages_are_prepared_and_insert_values_are_lifted(self) -> None:
        result = next(
            result
            for result in self.report["results"]
            if result["name"] == "merge.postgres.full_hardcoded"
        )
        package = result["package"]
        self.assertEqual(package["envelope_type"], "prepared")
        self.assertEqual(package["statement_type"], "MERGE")
        self.assertEqual(len(package["bindings"]), 6)
        self.assertNotIn("'Fred'", package["sql"])

    def test_real_duckdb_results_and_final_state_are_equivalent(self) -> None:
        self.assertEqual(
            self.report["summary"]["duckdb_execution_count"],
            len(DUCKDB_EXECUTION_CASES),
        )
        self.assertEqual(
            self.report["summary"]["duckdb_equivalent_count"],
            len(DUCKDB_EXECUTION_CASES),
        )

    def test_unconfigured_engine_targets_are_rejected(self) -> None:
        self.assertEqual(
            self.report["summary"]["known_unsupported_target_success_count"],
            0,
        )

    def test_known_parser_edges_are_adapted_or_rejected(self) -> None:
        self.assertEqual(self.report["summary"]["known_parser_gap_count"], 0)
        self.assertEqual(
            self.report["summary"]["known_unsafe_parse_success_count"],
            0,
        )

    def test_hardcoded_and_parameterized_merge_fingerprints_converge(self) -> None:
        self.assertEqual(self.report["summary"]["fingerprint_converges"], True)

    def test_merge_support_is_stable_across_public_calls(self) -> None:
        sql = (
            "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET name = 'Fred'"
        )
        first = prepare_statement(
            sql,
            source_dialect="postgres",
            target_dialect="postgres",
        )
        second = prepare_statement(
            sql,
            source_dialect="postgres",
            target_dialect="postgres",
        )

        self.assertEqual(first["envelope_type"], "prepared")
        self.assertEqual(second["envelope_type"], "prepared")
        self.assertEqual(cast(dict[str, Any], second)["statement_type"], "MERGE")

    def test_existing_with_select_needs_no_experimental_patch(self) -> None:
        package = prepare_statement(
            "WITH selected AS (SELECT * FROM people WHERE id = 1) "
            "SELECT * FROM selected WHERE active = TRUE",
            source_dialect="postgres",
            target_dialect="postgres",
        )

        self.assertEqual(package["envelope_type"], "prepared")
        prepared = cast(dict[str, Any], package)
        self.assertEqual(prepared["statement_type"], "SELECT")
        self.assertEqual(prepared["bindings"], [1, True])


if __name__ == "__main__":
    unittest.main()

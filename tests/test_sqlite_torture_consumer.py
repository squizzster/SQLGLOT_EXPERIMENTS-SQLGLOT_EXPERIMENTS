from __future__ import annotations

import unittest
from typing import Any

from demo.sqlite_torture_consumer import run_torture_suite


class SqliteTortureConsumerTests(unittest.TestCase):
    report: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_torture_suite()

    def test_every_retained_and_local_case_is_attempted(self) -> None:
        summary = self.report["summary"]
        self.assertEqual(
            summary["source_case_counts"],
            {"external": 581, "local": 9},
        )
        self.assertEqual(summary["case_count"], 590)
        names = [result["name"] for result in self.report["results"]]
        self.assertEqual(len(names), len(set(names)))

    def test_external_milestone_baseline_is_explicit(self) -> None:
        self.assertEqual(
            self.report["summary"]["source_status_counts"]["external"],
            {
                "equivalent": 533,
                "execution_failure": 1,
                "expected_brick_rejection": 3,
                "expected_engine_error": 40,
                "prepare_failure": 3,
                "result_mismatch": 1,
            },
        )
        external_failures = {
            result["name"]
            for result in self.report["failures"]
            if result["source_set"] == "external"
        }
        self.assertEqual(
            external_failures,
            {
                "binding.named_repeated_and_null",
                "binding.numbered_parameters",
                "binding.repeated_named_filter",
                "index.expression_partial_lookup",
                "numeric.cast_text_prefix_and_exponent",
            },
        )

    def test_invalid_input_is_reported_as_early_brick_rejection(self) -> None:
        rejected = {
            result["name"]: (
                result["failure_kind"],
                result["error"]["exception"],
            )
            for result in self.report["results"]
            if result["status"] == "expected_brick_rejection"
        }
        self.assertEqual(
            rejected,
            {
                "binding.missing_named_parameter": (
                    "binding_count",
                    "BindingCountError",
                ),
                "binding.multiple_statements_rejected": (
                    "statement_count",
                    "StatementPreparationError",
                ),
                "binding.wrong_parameter_count": (
                    "binding_count",
                    "BindingCountError",
                ),
            },
        )

    def test_every_hardcoded_replacement_preserves_external_behavior(self) -> None:
        external_with_replacements = [
            result
            for result in self.report["results"]
            if result["source_set"] == "external"
            and result.get("package", {})
            .get("analysis", {})
            .get("hardcoded_value_count", 0)
            > 0
        ]
        self.assertEqual(len(external_with_replacements), 141)
        self.assertEqual(
            sum(
                result["package"]["analysis"]["hardcoded_value_count"]
                for result in external_with_replacements
            ),
            227,
        )
        self.assertTrue(
            all(
                result["status"] in {"equivalent", "expected_engine_error"}
                for result in external_with_replacements
            )
        )

    def test_local_fixed_and_placeholder_pairs_converge(self) -> None:
        local = {
            result["name"]: result
            for result in self.report["results"]
            if result["source_set"] == "local"
        }
        for statement_type in ("select", "insert", "update", "delete"):
            with self.subTest(statement_type=statement_type):
                fixed = local[f"local.{statement_type}.fixed"]
                placeholder = local[f"local.{statement_type}.placeholder"]
                self.assertEqual(fixed["status"], "equivalent")
                self.assertEqual(placeholder["status"], "equivalent")
                self.assertEqual(fixed["package"]["sql"], placeholder["package"]["sql"])
                self.assertEqual(
                    fixed["package"]["bindings"],
                    placeholder["package"]["bindings"],
                )
                self.assertGreater(
                    fixed["package"]["analysis"]["hardcoded_value_count"], 0
                )
                self.assertEqual(
                    placeholder["package"]["analysis"]["hardcoded_value_count"],
                    0,
                )

    def test_mapping_input_is_not_repaired_by_the_consumer(self) -> None:
        result = next(
            result
            for result in self.report["results"]
            if result["name"] == "local.insert.named_mapping"
        )
        self.assertEqual(result["status"], "execution_failure")
        self.assertEqual(result["package"]["bindings"], ["id", "label"])
        self.assertEqual(result["error"]["sqlite_errorname"], "SQLITE_MISMATCH")


if __name__ == "__main__":
    unittest.main()

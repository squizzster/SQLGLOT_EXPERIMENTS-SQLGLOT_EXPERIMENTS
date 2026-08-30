from __future__ import annotations

import importlib.util
import unittest
from typing import Any, cast

from experiments.merge_with_statement_support.run_experiment import (
    DUCKDB_EXECUTION_CASES,
    MERGE_CASES,
    WITH_CASES,
    _duckdb_execution_result,
)
from experiments.replace_statement_support.run_experiment import (
    CASES as REPLACE_CASES,
)
from experiments.replace_statement_support.run_experiment import (
    SQLITE_EXECUTION_CASES,
    _execute_sqlite_case,
)
from sqlglot_experiments import prepare_statement


class ExtendedAdversarialRegressionTests(unittest.TestCase):
    def test_all_retained_replace_cases_have_the_integrated_outcome(self) -> None:
        self.assertEqual(len(REPLACE_CASES), 37)
        for case in REPLACE_CASES:
            with self.subTest(case=case.name):
                package = cast(
                    dict[str, Any],
                    prepare_statement(
                        case.sql,
                        bindings=case.bindings,
                        source_dialect=case.source_dialect,
                        target_dialect=case.target_dialect,
                    ),
                )
                expected_success = case.expected_success
                self.assertEqual(package["success"], expected_success, package)
                if not expected_success:
                    self.assertEqual(package["envelope_type"], "failure")
                    continue

                self.assertEqual(package["envelope_type"], "prepared")
                self.assertEqual(package["statement_type"], "REPLACE")
                expected_binding_count = case.expected_binding_count
                if expected_binding_count is not None:
                    self.assertEqual(
                        len(package["bindings"]),
                        expected_binding_count,
                    )
                if case.expected_sql_fragment is not None:
                    self.assertIn(case.expected_sql_fragment, package["sql"])

    def test_all_retained_with_and_merge_cases_have_the_integrated_outcome(
        self,
    ) -> None:
        cases = (*WITH_CASES, *MERGE_CASES)
        self.assertEqual(len(cases), 33)
        for case in cases:
            with self.subTest(case=case.name):
                package = cast(
                    dict[str, Any],
                    prepare_statement(
                        case.sql,
                        bindings=case.bindings,
                        source_dialect=case.source_dialect,
                        target_dialect=case.target_dialect,
                    ),
                )
                expected_success = case.expected_success
                self.assertEqual(package["success"], expected_success, package)
                if not expected_success:
                    self.assertEqual(package["envelope_type"], "failure")
                    continue

                self.assertEqual(package["envelope_type"], "prepared")
                self.assertEqual(
                    package["statement_type"],
                    case.expected_statement_type,
                )
                if case.expected_binding_count is not None:
                    self.assertEqual(
                        len(package["bindings"]),
                        case.expected_binding_count,
                    )
                if case.expected_where_fields is not None:
                    self.assertEqual(
                        tuple(package["where_fields"]),
                        case.expected_where_fields,
                    )
                expected_fragment = case.expected_sql_fragment
                if expected_fragment is not None:
                    self.assertIn(expected_fragment, package["sql"])

    def test_all_retained_sqlite_replace_cases_match_native_execution(self) -> None:
        self.assertEqual(len(SQLITE_EXECUTION_CASES), 10)
        for case in SQLITE_EXECUTION_CASES:
            with self.subTest(case=case.name):
                result = _execute_sqlite_case(case)
                self.assertEqual(result["equivalent"], True, result)


@unittest.skipUnless(importlib.util.find_spec("duckdb"), "duckdb is optional")
class DuckDbAdversarialExecutionTests(unittest.TestCase):
    def test_all_retained_merge_cases_match_native_execution(self) -> None:
        self.assertEqual(len(DUCKDB_EXECUTION_CASES), 7)
        for case in DUCKDB_EXECUTION_CASES:
            with self.subTest(case=case.name):
                result = _duckdb_execution_result(case)
                self.assertEqual(result["equivalent"], True, result)


if __name__ == "__main__":
    unittest.main()

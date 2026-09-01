from __future__ import annotations

import unittest

from sqlglot_experiments import prepare_statement


class UnresolvedFunctionCallAnalysisTests(unittest.TestCase):
    def test_mysql_stored_or_udf_calls_are_visible_to_execution_policy(self) -> None:
        statements = (
            "UPDATE people SET value = mutate_myisam(value)",
            "UPDATE people SET value = app.mutate_myisam(value)",
            "INSERT INTO people (value) VALUES (mutate_myisam(1))",
            "DELETE FROM people WHERE mutate_myisam(value) = 1",
            (
                "REPLACE INTO people (id, value) "
                "SELECT id, mutate_myisam(value) FROM incoming"
            ),
        )

        for statement in statements:
            with self.subTest(statement=statement):
                package = prepare_statement(
                    statement,
                    source_dialect="mysql",
                    target_dialect="mysql",
                )
                self.assertTrue(package["success"], package)
                if not package["success"] or package["envelope_type"] != "prepared":
                    self.fail(str(package))
                self.assertIs(
                    package["analysis"]["contains_unresolved_function_calls"],
                    True,
                )

    def test_mysql_builtin_functions_are_not_mislabeled_as_unresolved(self) -> None:
        statements = (
            "UPDATE people SET value = ABS(value)",
            "UPDATE people SET value = COALESCE(value, 0)",
            "INSERT INTO people (created_at) VALUES (CURRENT_TIMESTAMP)",
            "DELETE FROM people WHERE CAST(value AS SIGNED) = 1",
            (
                "INSERT INTO people (id, value) VALUES (1, 2) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value)"
            ),
        )

        for statement in statements:
            with self.subTest(statement=statement):
                package = prepare_statement(
                    statement,
                    source_dialect="mysql",
                    target_dialect="mysql",
                )
                self.assertTrue(package["success"], package)
                if not package["success"] or package["envelope_type"] != "prepared":
                    self.fail(str(package))
                self.assertIs(
                    package["analysis"]["contains_unresolved_function_calls"],
                    False,
                )

    def test_lexical_function_decoys_do_not_create_ast_evidence(self) -> None:
        package = prepare_statement(
            "UPDATE people SET note = 'mutate_myisam()' /* evil() */",
            source_dialect="mysql",
            target_dialect="mysql",
        )

        self.assertTrue(package["success"], package)
        if not package["success"] or package["envelope_type"] != "prepared":
            self.fail(str(package))
        self.assertIs(
            package["analysis"]["contains_unresolved_function_calls"],
            False,
        )


if __name__ == "__main__":
    unittest.main()

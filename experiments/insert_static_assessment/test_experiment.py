from __future__ import annotations

import unittest

from experiments.insert_static_assessment.run_experiment import run_experiment


class InsertStaticAssessmentExperimentTests(unittest.TestCase):
    def test_every_adversarial_shape_retains_exact_static_facts(self) -> None:
        results = {result["name"]: result for result in run_experiment()}

        adversarial = results["adversarial_multirow_upsert"]
        self.assertEqual(adversarial["statement_type"], "INSERT")
        self.assertEqual(
            adversarial["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": None,
                    "table": "inventory.log",
                },
                "supplied_columns": ["select", "sku.code", "qty", "note"],
            },
        )

        self.assertEqual(
            results["mysql_set"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": "reporting",
                    "table": "inventory",
                },
                "supplied_columns": ["sku.code", "qty"],
            },
        )
        self.assertEqual(
            results["cte_insert_select"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": None,
                    "table": "inventory",
                },
                "supplied_columns": ["sku", "qty"],
            },
        )
        self.assertEqual(
            results["unknown_positional_columns"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": None,
                    "table": "inventory",
                },
                "supplied_columns": [],
            },
        )
        self.assertIsNone(results["replace_is_not_insert"]["insert"])


if __name__ == "__main__":
    unittest.main()

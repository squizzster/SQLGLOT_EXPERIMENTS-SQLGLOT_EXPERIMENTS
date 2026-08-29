from __future__ import annotations

import gzip
import json
import re
import unittest
from pathlib import Path
from typing import Any, cast

import sqlglot_experiments
from sqlglot_experiments import InputBindings, PreparedStatement, prepare_statement
from sqlglot_experiments.statement_fingerprinting import (
    FingerprintingError,
    fingerprint_statement,
)


class StatementFingerprintingTests(unittest.TestCase):
    def fingerprint(
        self,
        sql: str,
        *,
        source: str = "sqlite",
        target: str = "sqlite",
    ) -> str:
        return fingerprint_statement(
            sql,
            source_dialect=source,
            target_dialect=target,
        )

    def test_fingerprint_is_internal_and_is_sha256_hex(self) -> None:
        fingerprint = self.fingerprint("SELECT id FROM customers WHERE id = 1")

        self.assertIsNone(getattr(sqlglot_experiments, "fingerprint_statement", None))
        self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{64}", fingerprint))

    def test_hardcoded_and_placeholder_updates_share_a_fingerprint(self) -> None:
        hardcoded = self.fingerprint(
            "UPDATE customers SET forename = 'Mark' WHERE id = 1"
        )
        placeholder = self.fingerprint(
            "UPDATE customers SET forename = ? WHERE id = ?"
        )

        self.assertEqual(hardcoded, placeholder)

    def test_all_supported_statements_ignore_values(self) -> None:
        pairs = (
            (
                "SELECT id FROM customers WHERE category = 'sales'",
                "SELECT id FROM customers WHERE category = ?",
            ),
            (
                "INSERT INTO customers (forename, age) VALUES ('Mark', 42)",
                "INSERT INTO customers (forename, age) VALUES (?, ?)",
            ),
            (
                "UPDATE customers SET forename = 'Mark' WHERE id = 1",
                "UPDATE customers SET forename = ? WHERE id = ?",
            ),
            (
                "DELETE FROM customers WHERE category = 'sales'",
                "DELETE FROM customers WHERE category = ?",
            ),
        )

        for hardcoded, placeholder in pairs:
            with self.subTest(sql=hardcoded):
                self.assertEqual(
                    self.fingerprint(hardcoded),
                    self.fingerprint(placeholder),
                )

    def test_source_placeholder_spelling_and_identity_do_not_affect_shape(self) -> None:
        cases = (
            "SELECT id FROM customers WHERE a = ? AND b = ?",
            "SELECT id FROM customers WHERE a = :value AND b = :value",
            "SELECT id FROM customers WHERE a = ?2 AND b = ?1",
            "SELECT id FROM customers WHERE a = 10 AND b = 20",
        )

        self.assertEqual(len({self.fingerprint(sql) for sql in cases}), 1)

    def test_negative_boolean_null_projection_and_limit_values_are_ignored(
        self,
    ) -> None:
        hardcoded = self.fingerprint(
            "SELECT 'label', id FROM customers "
            "WHERE score > -1 AND active = TRUE AND deleted_at IS NULL LIMIT 5"
        )
        placeholder = self.fingerprint(
            "SELECT ?, id FROM customers "
            "WHERE score > ? AND active = ? AND deleted_at IS ? LIMIT ?"
        )

        self.assertEqual(hardcoded, placeholder)

    def test_structure_remains_significant(self) -> None:
        cases = (
            "SELECT id FROM customers WHERE a = 1 AND b = 2",
            "SELECT id FROM customers WHERE a = 1 OR b = 2",
            "SELECT id FROM customers WHERE a = 1",
            "SELECT id FROM archived_customers WHERE a = 1 AND b = 2",
            "DELETE FROM customers WHERE a = 1 AND b = 2",
        )

        self.assertEqual(len({self.fingerprint(sql) for sql in cases}), len(cases))

    def test_structural_numeric_roles_remain_significant(self) -> None:
        pairs = (
            (
                "sqlite",
                "SELECT a, b FROM t ORDER BY 1",
                "SELECT a, b FROM t ORDER BY 2",
            ),
            (
                "sqlite",
                "SELECT a, b FROM t GROUP BY 1",
                "SELECT a, b FROM t GROUP BY 2",
            ),
            (
                "sqlite",
                "SELECT CAST(a AS DECIMAL(10, 2)) FROM t",
                "SELECT CAST(a AS DECIMAL(18, 6)) FROM t",
            ),
            (
                "postgres",
                "SELECT DISTINCT ON (1) a, b FROM t",
                "SELECT DISTINCT ON (2) a, b FROM t",
            ),
        )

        for dialect, left, right in pairs:
            with self.subTest(dialect=dialect, left=left, right=right):
                self.assertNotEqual(
                    self.fingerprint(left, source=dialect, target=dialect),
                    self.fingerprint(right, source=dialect, target=dialect),
                )

    def test_sqlglot_native_placeholders_are_value_sites(self) -> None:
        cases = (
            (
                "mysql",
                "SELECT id FROM customers WHERE id = 42",
                "SELECT id FROM customers WHERE id = ?",
            ),
            (
                "bigquery",
                "SELECT id FROM customers WHERE id = 42",
                "SELECT id FROM customers WHERE id = @customer_id",
            ),
        )

        for dialect, hardcoded, placeholder in cases:
            with self.subTest(dialect=dialect):
                self.assertEqual(
                    self.fingerprint(hardcoded, source=dialect, target=dialect),
                    self.fingerprint(placeholder, source=dialect, target=dialect),
                )

    def test_value_site_count_remains_significant(self) -> None:
        self.assertNotEqual(
            self.fingerprint("SELECT id FROM customers WHERE id IN (1, 2)"),
            self.fingerprint("SELECT id FROM customers WHERE id IN (1, 2, 3)"),
        )

    def test_whitespace_case_comments_and_literal_values_do_not_affect_shape(
        self,
    ) -> None:
        left = self.fingerprint(
            "SELECT id FROM customers WHERE category = 'sales' -- first value"
        )
        right = self.fingerprint(
            "select id from customers where category = 'support'"
        )

        self.assertEqual(left, right)

    def test_dialect_route_is_part_of_the_fingerprint(self) -> None:
        sql = "SELECT id FROM customers WHERE category = 'sales'"

        self.assertNotEqual(
            self.fingerprint(sql, source="sqlite", target="sqlite"),
            self.fingerprint(sql, source="sqlite", target="postgres"),
        )

    def test_multiple_and_unsupported_statements_are_rejected(self) -> None:
        for sql in (
            "SELECT 1; SELECT 2",
            "CREATE TABLE customers (id INTEGER)",
        ):
            with self.subTest(sql=sql), self.assertRaises(FingerprintingError):
                self.fingerprint(sql)

    def test_every_single_nightmare_statement_fingerprints_and_converges(
        self,
    ) -> None:
        root = Path(__file__).parents[1]
        with gzip.open(
            root / "assets/sql_torture_pack/cases.json.gz",
            "rt",
            encoding="utf-8",
        ) as stream:
            external_cases: list[dict[str, Any]] = json.load(stream)["cases"]
        local_cases: list[dict[str, Any]] = json.loads(
            (root / "tests/fixtures/sql_torture_extensions.json").read_text()
        )["cases"]

        fingerprinted_count = 0
        prepared_match_count = 0
        rejected_names: set[str] = set()
        for case in (*external_cases, *local_cases):
            sql = str(case["sql"])
            try:
                original = self.fingerprint(sql)
            except FingerprintingError:
                rejected_names.add(str(case["name"]))
                continue

            fingerprinted_count += 1
            result = prepare_statement(
                sql,
                bindings=cast(InputBindings, case.get("params", [])),
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            if result["success"]:
                prepared = cast(PreparedStatement, result)
                self.assertEqual(original, prepared["sql_fingerprint"])
                self.assertEqual(original, self.fingerprint(prepared["sql"]))
                prepared_match_count += 1

        self.assertEqual(fingerprinted_count, 589)
        self.assertEqual(prepared_match_count, 587)
        self.assertEqual(
            rejected_names,
            {"binding.multiple_statements_rejected"},
        )


if __name__ == "__main__":
    unittest.main()

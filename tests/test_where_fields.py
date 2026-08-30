from __future__ import annotations

import unittest
from typing import cast

from sqlglot_experiments import PreparedStatement, WhereField, prepare_statement


def where_fields(
    sql: str,
    *,
    bindings: list[object] | None = None,
) -> list[WhereField]:
    result = prepare_statement(
        sql,
        bindings=bindings,
        source_dialect="sqlite",
        target_dialect="sqlite",
    )
    if not result["success"]:
        raise AssertionError(result["msg"])
    return cast(PreparedStatement, result)["where_fields"]


class WhereFieldsTests(unittest.TestCase):
    def test_unknown_database_uses_table_field_form(self) -> None:
        self.assertEqual(
            where_fields("SELECT * FROM people AS p WHERE p.id = 1"),
            ["people.id"],
        )

    def test_explicit_database_is_preserved_through_an_alias(self) -> None:
        self.assertEqual(
            where_fields(
                "SELECT * FROM main.people AS p "
                "WHERE p.id = 1 AND p.status = 'active'"
            ),
            ["main.people.id", "main.people.status"],
        )

    def test_unqualified_fields_resolve_against_one_physical_source(self) -> None:
        self.assertEqual(
            where_fields("SELECT * FROM people WHERE id = 1 AND status = 'active'"),
            ["people.id", "people.status"],
        )

    def test_unqualified_field_with_multiple_sources_has_unknown_table(self) -> None:
        self.assertEqual(
            where_fields(
                "SELECT * FROM people AS p JOIN orders AS o ON o.person_id = p.id "
                "WHERE status = 'active'"
            ),
            ["status"],
        )

    def test_nested_correlated_fields_resolve_inner_and_outer_aliases(self) -> None:
        self.assertEqual(
            where_fields(
                """
                SELECT * FROM customers AS c
                WHERE EXISTS (
                    SELECT 1 FROM orders AS o
                    WHERE o.customer_id = c.customer_id AND o.status = 'open'
                )
                """
            ),
            ["orders.customer_id", "customers.customer_id", "orders.status"],
        )

    def test_unqualified_field_in_correlatable_subquery_is_retained(self) -> None:
        self.assertEqual(
            where_fields(
                """
                SELECT * FROM customers AS c
                WHERE EXISTS (
                    SELECT 1 FROM orders AS o
                    WHERE customer_id = c.customer_id
                )
                """
            ),
            ["customer_id", "customers.customer_id"],
        )

    def test_field_without_any_source_is_retained(self) -> None:
        self.assertEqual(
            where_fields("SELECT 1 WHERE mystery = 1"),
            ["mystery"],
        )

    def test_derived_alias_field_is_retained_with_unknown_physical_table(self) -> None:
        self.assertEqual(
            where_fields(
                """
                SELECT *
                FROM (
                    SELECT p.id FROM people AS p WHERE p.active = 1
                ) AS filtered
                WHERE filtered.id = 1
                """
            ),
            ["id", "people.active"],
        )

    def test_repeated_physical_field_is_returned_once(self) -> None:
        self.assertEqual(
            where_fields("SELECT * FROM people AS p WHERE p.id = 1 OR p.id = 2"),
            ["people.id"],
        )

    def test_unquoted_alias_and_field_case_follow_sqlite_identity(self) -> None:
        self.assertEqual(
            where_fields(
                "SELECT * FROM people AS p WHERE p.id = 1 OR P.ID = 2"
            ),
            ["people.id"],
        )

    def test_quoted_dots_remain_distinct_from_qualification_dots(self) -> None:
        self.assertEqual(
            where_fields(
                'SELECT * FROM "a.b" AS x JOIN a.b AS y ON 1 = 1 '
                "WHERE x.c = 1 OR y.c = 2"
            ),
            ['"a.b".c', "a.b.c"],
        )

    def test_hardcoded_and_parameterized_forms_converge(self) -> None:
        hardcoded = where_fields("SELECT * FROM people AS p WHERE p.id = 1")
        parameterized = where_fields(
            "SELECT * FROM people AS p WHERE p.id = ?",
            bindings=[1],
        )

        self.assertEqual(hardcoded, parameterized)

    def test_qualified_update_and_delete_targets_are_resolved(self) -> None:
        cases = (
            "UPDATE people AS p SET active = 0 WHERE p.id = 1",
            "DELETE FROM people AS p WHERE p.id = 1",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertEqual(
                    where_fields(sql),
                    ["people.id"],
                )

    def test_unqualified_update_and_delete_targets_are_resolved(self) -> None:
        cases = (
            "UPDATE people SET active = 0 WHERE id = 1",
            "DELETE FROM people WHERE id = 1",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertEqual(
                    where_fields(sql),
                    ["people.id"],
                )

    def test_insert_select_where_is_also_inspected(self) -> None:
        self.assertEqual(
            where_fields(
                "INSERT INTO archive_people (id) "
                "SELECT p.id FROM people AS p WHERE p.active = 0"
            ),
            ["people.active"],
        )


if __name__ == "__main__":
    unittest.main()

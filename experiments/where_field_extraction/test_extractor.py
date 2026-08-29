from __future__ import annotations

import unittest

from .extractor import FieldReference, extract_where_fields
from .run_corpus import run_corpus


class WhereFieldExtractorTests(unittest.TestCase):
    def fields(self, sql: str, *, dialect: str = "sqlite") -> list[FieldReference]:
        result = extract_where_fields(sql, dialect=dialect)
        self.assertEqual(result["field_capture_mismatch_count"], 0)
        return [
            field
            for statement in result["statements"]
            for field in statement["fields"]
        ]

    def test_simple_select_extracts_an_inferred_table_and_field(self) -> None:
        self.assertEqual(
            self.fields("SELECT * FROM people WHERE id = 1"),
            [
                {
                    "statement_index": 0,
                    "where_index": 0,
                    "column_sql": "id",
                    "field": "id",
                    "written_catalog": None,
                    "written_database": None,
                    "written_table": None,
                    "catalog": None,
                    "database": None,
                    "table": "people",
                    "source_alias": "people",
                    "certainty": "inferred",
                    "resolution": "single_scope_source",
                }
            ],
        )

    def test_qualified_alias_resolves_to_database_table(self) -> None:
        fields = self.fields(
            "SELECT * FROM main.people AS p WHERE p.id = 1 AND p.status = 'open'"
        )

        self.assertEqual([field["field"] for field in fields], ["id", "status"])
        self.assertEqual({field["database"] for field in fields}, {"main"})
        self.assertEqual({field["table"] for field in fields}, {"people"})
        self.assertEqual({field["source_alias"] for field in fields}, {"p"})
        self.assertEqual({field["certainty"] for field in fields}, {"observed"})

    def test_nested_correlated_where_keeps_scope_ownership(self) -> None:
        fields = self.fields(
            """
            SELECT * FROM customers AS c
            WHERE EXISTS (
                SELECT 1 FROM orders AS o
                WHERE o.customer_id = c.customer_id AND o.status = 'open'
            )
            """
        )

        self.assertEqual(
            [(field["field"], field["table"]) for field in fields],
            [
                ("customer_id", "orders"),
                ("customer_id", "customers"),
                ("status", "orders"),
            ],
        )
        self.assertEqual({field["where_index"] for field in fields}, {1})
        self.assertIn(
            "correlated_qualified_source",
            {field["resolution"] for field in fields},
        )

    def test_ambiguous_unqualified_field_remains_unresolved(self) -> None:
        fields = self.fields(
            "SELECT * FROM people JOIN orders ON people.id = orders.person_id "
            "WHERE id = 1"
        )

        self.assertEqual(fields[0]["field"], "id")
        self.assertIsNone(fields[0]["table"])
        self.assertEqual(fields[0]["certainty"], "unresolved")
        self.assertEqual(fields[0]["resolution"], "ambiguous_scope_sources")

    def test_derived_source_does_not_invent_a_physical_table(self) -> None:
        fields = self.fields(
            "SELECT * FROM (SELECT id FROM people) AS p WHERE p.id = 1"
        )

        self.assertEqual(fields[0]["field"], "id")
        self.assertIsNone(fields[0]["table"])
        self.assertEqual(fields[0]["source_alias"], "p")
        self.assertEqual(fields[0]["certainty"], "unresolved")
        self.assertEqual(fields[0]["resolution"], "qualified_source_is_derived")

    def test_update_and_delete_use_the_dml_target_as_context(self) -> None:
        for sql in (
            "UPDATE people SET active = 0 WHERE id = 1",
            "DELETE FROM people WHERE id = 1",
        ):
            with self.subTest(sql=sql):
                fields = self.fields(sql)
                self.assertEqual(fields[0]["field"], "id")
                self.assertEqual(fields[0]["table"], "people")
                self.assertEqual(fields[0]["resolution"], "single_dml_source")

    def test_multiple_statements_are_each_audited(self) -> None:
        result = extract_where_fields(
            "SELECT * FROM a WHERE x = 1; DELETE FROM b WHERE y = 2",
            dialect="sqlite",
        )

        self.assertEqual(result["statement_count"], 2)
        self.assertEqual(result["where_clause_count"], 2)
        self.assertEqual(result["field_reference_count"], 2)
        self.assertEqual(result["field_capture_mismatch_count"], 0)

    def test_sqlite_numbered_parameters_are_normalized_before_parsing(self) -> None:
        fields = self.fields(
            "SELECT * FROM people WHERE tenant_id = ?2 AND person_id = ?1"
        )

        self.assertEqual([field["field"] for field in fields], ["tenant_id", "person_id"])


class WhereFieldCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_corpus()

    def test_complete_retained_corpus_is_parsed_and_audited(self) -> None:
        self.assertEqual(self.report["input"]["torture_case_count"], 590)
        self.assertEqual(self.report["input"]["retained_complex_case_count"], 1)
        self.assertEqual(
            self.report["summary"],
            {
                "parsed_case_count": 591,
                "parse_failure_count": 0,
                "statement_count": 592,
                "where_clause_count": 428,
                "field_reference_count": 818,
                "oracle_field_reference_count": 818,
                "field_capture_mismatch_count": 0,
                "scope_error_count": 0,
                "table_resolved_field_count": 744,
                "database_resolved_field_count": 0,
            },
        )

    def test_resolution_certainty_remains_explicit(self) -> None:
        self.assertEqual(
            self.report["certainty_counts"],
            {"inferred": 365, "observed": 379, "unresolved": 74},
        )
        self.assertEqual(
            self.report["statement_type_counts"],
            {"DELETE": 4, "INSERT": 16, "SELECT": 556, "UPDATE": 16},
        )


if __name__ == "__main__":
    unittest.main()

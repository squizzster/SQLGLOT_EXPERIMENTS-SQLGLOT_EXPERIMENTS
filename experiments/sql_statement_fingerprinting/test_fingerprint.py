from __future__ import annotations

import unittest

from .fingerprint import (
    UnsupportedStatementError,
    fingerprint_sql,
    fingerprint_versions,
)


class FingerprintExperimentTests(unittest.TestCase):
    def fp(self, sql: str, profile: str, read: str | None = None) -> str:
        return fingerprint_sql(sql, read=read, placeholder_profile=profile).sha256_hex

    def test_representative_driver_styles_share_an_unknown_shape(self) -> None:
        cases = (
            ("SELECT id FROM people WHERE a = ? AND b = ?", "qmark"),
            ("SELECT id FROM people WHERE a = %s AND b = %s", "format"),
            ("SELECT id FROM people WHERE a = :a AND b = :b", "named"),
            ("SELECT id FROM people WHERE a = %(a)s AND b = %(b)s", "pyformat"),
            ("SELECT id FROM people WHERE a = :1 AND b = :2", "numeric"),
            (
                "SELECT id FROM people WHERE a = $1 AND b = $2",
                "dollar_numeric",
            ),
            (
                "SELECT id FROM people WHERE a = ?1 AND b = ?2",
                "sqlite_numbered",
            ),
        )

        self.assertEqual(len({self.fp(sql, style) for sql, style in cases}), 1)

    def test_known_and_unknown_are_different_versions(self) -> None:
        versions = fingerprint_versions(
            "SELECT id FROM people WHERE category = ?",
            read="sqlite",
            placeholder_profile="qmark",
        )

        self.assertEqual(versions.dialect_known.certainty, "dialect-known")
        self.assertEqual(versions.engine_unknown.certainty, "engine-unknown")
        self.assertNotEqual(
            versions.dialect_known.sha256_hex,
            versions.engine_unknown.sha256_hex,
        )

    def test_same_prepared_insert_is_independent_of_bound_values(self) -> None:
        sql = "INSERT INTO people (forename) VALUES (?)"
        mark = self.fp(sql, "qmark", "sqlite")
        paul = self.fp(sql, "qmark", "sqlite")

        self.assertEqual(mark, paul)

    def test_select_insert_and_update_are_supported_and_distinct(self) -> None:
        results = (
            fingerprint_sql(
                "SELECT name FROM people WHERE id = ?", placeholder_profile="qmark"
            ),
            fingerprint_sql(
                "INSERT INTO people (name) VALUES (?)", placeholder_profile="qmark"
            ),
            fingerprint_sql(
                "UPDATE people SET name = ? WHERE id = ?",
                placeholder_profile="qmark",
            ),
        )

        self.assertEqual(
            {r.statement_kind for r in results}, {"SELECT", "INSERT", "UPDATE"}
        )
        self.assertEqual(len({r.sha256_hex for r in results}), 3)

    def test_qualification_remains_significant(self) -> None:
        left = self.fp(
            "SELECT crm.people.name FROM crm.people WHERE crm.people.id = %s",
            "format",
            "mysql",
        )
        right = self.fp(
            "SELECT archive.people.name FROM archive.people "
            "WHERE archive.people.id = %s",
            "format",
            "mysql",
        )

        self.assertNotEqual(left, right)

    def test_repeated_binding_is_part_of_the_shape(self) -> None:
        repeated = fingerprint_sql(
            "SELECT id FROM people WHERE a = :value OR b = :value",
            placeholder_profile="named",
        )
        independent = fingerprint_sql(
            "SELECT id FROM people WHERE a = ? OR b = ?",
            placeholder_profile="qmark",
        )

        self.assertEqual(repeated.binding_pattern, ("p1", "p1"))
        self.assertEqual(independent.binding_pattern, ("p1", "p2"))
        self.assertNotEqual(repeated.sha256_hex, independent.sha256_hex)

    def test_sqlite_native_markers_are_observed(self) -> None:
        result = fingerprint_sql(
            "SELECT * FROM t WHERE a = ?1 AND b = :name AND c = :name "
            "AND d = @other AND e = $last",
            read="sqlite",
            placeholder_profile="sqlite_native",
        )

        self.assertEqual(result.binding_pattern, ("p1", "p2", "p2", "p3", "p4"))

    def test_tokenizer_avoids_marker_text_in_strings_and_comments(self) -> None:
        result = fingerprint_sql(
            "SELECT '%s' FROM people -- %s\nWHERE id = %s",
            placeholder_profile="format",
        )

        self.assertEqual(result.source_bindings, ("%s",))

    def test_inline_literals_remain_significant(self) -> None:
        self.assertNotEqual(
            self.fp("INSERT INTO people (id, name) VALUES (?, 'Mark')", "qmark"),
            self.fp("INSERT INTO people (id, name) VALUES (?, 'Paul')", "qmark"),
        )

    def test_multiple_and_unsupported_statements_are_rejected(self) -> None:
        with self.assertRaises(UnsupportedStatementError):
            self.fp("SELECT id FROM a WHERE id = ?; SELECT id FROM b", "qmark")
        with self.assertRaises(UnsupportedStatementError):
            self.fp("DELETE FROM people WHERE id = ?", "qmark")


if __name__ == "__main__":
    unittest.main()

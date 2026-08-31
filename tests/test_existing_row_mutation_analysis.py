from __future__ import annotations

import unittest
from typing import cast

from sqlglot_experiments import PreparedStatement, prepare_statement


def prepared(sql: str, *, dialect: str) -> PreparedStatement:
    package = prepare_statement(
        sql,
        source_dialect=dialect,
        target_dialect=dialect,
    )
    if not package["success"] or package["envelope_type"] != "prepared":
        raise AssertionError(package)
    return cast(PreparedStatement, package)


def effect(
    table: str,
    *,
    updated_columns: list[str] | None,
    deletes_rows: bool,
    schema: str | None = None,
    catalog: str | None = None,
) -> dict[str, object]:
    return {
        "target": {"catalog": catalog, "schema": schema, "table": table},
        "updated_columns": updated_columns,
        "deletes_rows": deletes_rows,
    }


class ExistingRowMutationAnalysisTests(unittest.TestCase):
    def test_select_and_plain_insert_have_no_existing_row_effect(self) -> None:
        for sql, dialect in (
            ("SELECT * FROM people", "sqlite"),
            (
                "INSERT INTO people (forename, surname) VALUES ('A', 'B')",
                "sqlite",
            ),
        ):
            with self.subTest(sql=sql):
                self.assertEqual(
                    prepared(sql, dialect=dialect)["analysis"][
                        "existing_row_mutations"
                    ],
                    {"effects": [], "evidence_complete": True},
                )

    def test_update_reports_only_assignment_targets_not_where_fields(self) -> None:
        package = prepared(
            "UPDATE people AS p SET forename = 'Mark', active = 0 "
            "WHERE p.surname = 'Bloggs'",
            dialect="sqlite",
        )

        self.assertEqual(
            package["analysis"]["existing_row_mutations"],
            {
                "effects": [
                    effect(
                        "people",
                        updated_columns=["forename", "active"],
                        deletes_rows=False,
                    )
                ],
                "evidence_complete": True,
            },
        )

    def test_tuple_assignment_preserves_every_target_column(self) -> None:
        package = prepared(
            "UPDATE people SET (forename, surname) = ('A', 'B') WHERE id = 1",
            dialect="postgres",
        )

        self.assertEqual(
            package["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people",
                    updated_columns=["forename", "surname"],
                    deletes_rows=False,
                )
            ],
        )

    def test_mysql_joined_update_maps_qualified_assignments_to_each_table(self) -> None:
        package = prepared(
            "UPDATE people AS p JOIN extra AS e ON e.id = p.id "
            "SET p.surname = e.surname, e.value = 1",
            dialect="mysql",
        )

        self.assertEqual(
            package["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people",
                    updated_columns=["surname"],
                    deletes_rows=False,
                ),
                effect(
                    "extra",
                    updated_columns=["value"],
                    deletes_rows=False,
                ),
            ],
        )

    def test_delete_ignores_join_and_using_sources_as_deletion_targets(self) -> None:
        cases = (
            (
                "mysql",
                "DELETE p FROM people AS p JOIN stale AS s ON s.id = p.id",
            ),
            (
                "postgres",
                "DELETE FROM people AS p USING stale AS s WHERE s.id = p.id",
            ),
        )
        for dialect, sql in cases:
            with self.subTest(dialect=dialect):
                self.assertEqual(
                    prepared(sql, dialect=dialect)["analysis"][
                        "existing_row_mutations"
                    ]["effects"],
                    [effect("people", updated_columns=[], deletes_rows=True)],
                )

    def test_mysql_multi_target_delete_reports_every_deleted_table(self) -> None:
        package = prepared(
            "DELETE p, s FROM people AS p JOIN stale AS s ON s.id = p.id",
            dialect="mysql",
        )

        self.assertEqual(
            package["analysis"]["existing_row_mutations"]["effects"],
            [
                effect("people", updated_columns=[], deletes_rows=True),
                effect("stale", updated_columns=[], deletes_rows=True),
            ],
        )

    def test_insert_conflict_update_and_replace_cannot_hide_their_effects(self) -> None:
        update = prepared(
            "INSERT INTO people (forename, surname) VALUES ('A', 'B') "
            "ON CONFLICT (forename, surname) DO UPDATE "
            "SET surname = excluded.surname, active = 1",
            dialect="sqlite",
        )
        replace = prepared(
            "REPLACE INTO people (forename, surname) VALUES ('A', 'B')",
            dialect="sqlite",
        )

        self.assertEqual(
            update["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people",
                    updated_columns=["surname", "active"],
                    deletes_rows=False,
                )
            ],
        )
        self.assertEqual(
            replace["analysis"]["existing_row_mutations"]["effects"],
            [effect("people", updated_columns=[], deletes_rows=True)],
        )

    def test_vendor_conflict_and_replace_spellings_keep_the_same_effects(
        self,
    ) -> None:
        mysql_upsert = prepared(
            "INSERT INTO people (code, active) VALUES ('A', 1) "
            "ON DUPLICATE KEY UPDATE "
            "code = VALUES(code), active = VALUES(active)",
            dialect="mysql",
        )
        postgres_tuple = prepared(
            "INSERT INTO people (code, region) VALUES ('A', 'EU') "
            "ON CONFLICT (code) DO UPDATE SET "
            "(code, region) = (excluded.code, excluded.region)",
            dialect="postgres",
        )
        sqlite_replace = prepared(
            "INSERT OR REPLACE INTO people (id, code) VALUES (1, 'A')",
            dialect="sqlite",
        )

        self.assertEqual(
            mysql_upsert["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people",
                    updated_columns=["code", "active"],
                    deletes_rows=False,
                )
            ],
        )
        self.assertEqual(
            postgres_tuple["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people",
                    updated_columns=["code", "region"],
                    deletes_rows=False,
                )
            ],
        )
        self.assertEqual(
            sqlite_replace["analysis"]["existing_row_mutations"]["effects"],
            [effect("people", updated_columns=[], deletes_rows=True)],
        )

    def test_merge_combines_update_and_delete_actions_for_its_target(self) -> None:
        package = prepared(
            "MERGE INTO people AS p USING incoming AS i ON p.id = i.id "
            "WHEN MATCHED THEN UPDATE SET surname = i.surname "
            "WHEN MATCHED AND i.stale THEN DELETE",
            dialect="duckdb",
        )

        self.assertEqual(
            package["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people",
                    updated_columns=["surname"],
                    deletes_rows=True,
                )
            ],
        )

    def test_data_modifying_cte_effects_are_not_hidden_by_outer_family(self) -> None:
        package = prepared(
            "WITH changed AS ("
            "UPDATE protected_people SET surname = 'Changed' RETURNING id"
            ") INSERT INTO audit_rows (person_id) SELECT id FROM changed",
            dialect="postgres",
        )

        self.assertEqual(package["statement_type"], "INSERT")
        self.assertEqual(
            package["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "protected_people",
                    updated_columns=["surname"],
                    deletes_rows=False,
                )
            ],
        )

    def test_quoted_identifier_content_stays_structured(self) -> None:
        package = prepared(
            'UPDATE "odd.schema"."people.table" SET "surname.part" = \'Changed\'',
            dialect="postgres",
        )

        self.assertEqual(
            package["analysis"]["existing_row_mutations"]["effects"],
            [
                effect(
                    "people.table",
                    schema="odd.schema",
                    updated_columns=["surname.part"],
                    deletes_rows=False,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()

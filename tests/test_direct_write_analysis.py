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


def target(
    table: str,
    *,
    schema: str | None = None,
    catalog: str | None = None,
) -> dict[str, str | None]:
    return {"catalog": catalog, "schema": schema, "table": table}


class DirectWriteAnalysisTests(unittest.TestCase):
    def test_direct_write_target_matrix(self) -> None:
        cases = (
            ("sqlite", "SELECT * FROM people", []),
            ("sqlite", "INSERT INTO people VALUES (1)", [target("people")]),
            (
                "postgres",
                "INSERT INTO app.people (id) VALUES (1)",
                [target("people", schema="app")],
            ),
            (
                "postgres",
                (
                    "WITH made AS (INSERT INTO source_rows VALUES (1) RETURNING id) "
                    "INSERT INTO target_rows SELECT id FROM made"
                ),
                [target("target_rows"), target("source_rows")],
            ),
            (
                "postgres",
                (
                    "WITH made AS (INSERT INTO people VALUES (1) RETURNING id) "
                    "SELECT id FROM made"
                ),
                [target("people")],
            ),
            (
                "postgres",
                (
                    "WITH changed AS (UPDATE people SET active = FALSE RETURNING id) "
                    "SELECT id FROM changed"
                ),
                [target("people")],
            ),
            (
                "postgres",
                (
                    "WITH removed AS (DELETE FROM people RETURNING id) "
                    "SELECT id FROM removed"
                ),
                [target("people")],
            ),
            ("sqlite", "UPDATE people SET active = 0", [target("people")]),
            (
                "postgres",
                'UPDATE "odd.schema"."people.table" SET active = FALSE',
                [target("people.table", schema="odd.schema")],
            ),
            (
                "mysql",
                (
                    "UPDATE people p JOIN extra e ON e.id = p.id "
                    "SET p.active = 0, e.value = 1"
                ),
                [target("people"), target("extra")],
            ),
            ("sqlite", "DELETE FROM people", [target("people")]),
            (
                "postgres",
                "DELETE FROM people USING stale WHERE stale.id = people.id",
                [target("people")],
            ),
            (
                "mysql",
                "DELETE p, s FROM people p JOIN stale s ON s.id = p.id",
                [target("people"), target("stale")],
            ),
            (
                "sqlite",
                "REPLACE INTO people (id) VALUES (1)",
                [target("people")],
            ),
            (
                "sqlite",
                "INSERT OR REPLACE INTO people (id) VALUES (1)",
                [target("people")],
            ),
            (
                "mysql",
                (
                    "INSERT INTO people (id) VALUES (1) "
                    "ON DUPLICATE KEY UPDATE id = VALUES(id)"
                ),
                [target("people")],
            ),
            (
                "duckdb",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN NOT MATCHED THEN INSERT (id) VALUES (i.id)"
                ),
                [target("people")],
            ),
            (
                "duckdb",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN UPDATE SET active = FALSE"
                ),
                [target("people")],
            ),
            (
                "duckdb",
                (
                    "MERGE INTO people p USING incoming i ON p.id = i.id "
                    "WHEN MATCHED THEN DELETE "
                    "WHEN NOT MATCHED THEN INSERT (id) VALUES (i.id)"
                ),
                [target("people")],
            ),
            (
                "postgres",
                (
                    "WITH changed AS ("
                    "UPDATE source_rows SET active = FALSE RETURNING id"
                    "), made AS ("
                    "INSERT INTO audit_rows SELECT id FROM changed RETURNING id"
                    ") SELECT id FROM made"
                ),
                [target("source_rows"), target("audit_rows")],
            ),
            (
                "postgres",
                "INSERT INTO people SELECT id FROM source_rows",
                [target("people")],
            ),
            (
                "postgres",
                "DELETE FROM people USING source_rows WHERE source_rows.id = people.id",
                [target("people")],
            ),
        )

        for dialect, statement, expected_targets in cases:
            with self.subTest(dialect=dialect, statement=statement):
                self.assertEqual(
                    prepared(statement, dialect=dialect)["analysis"]["direct_writes"],
                    {
                        "targets": expected_targets,
                        "evidence_complete": True,
                    },
                )

    def test_twenty_assignment_unknown_cases_keep_structured_write_targets(
        self,
    ) -> None:
        """Assignment-policy uncertainty cannot contaminate target evidence."""

        cases = (
            (
                "postgres",
                "UPDATE ordinary_rows SET payload[1] = 7 WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE ordinary_rows SET payload[2] = payload[1] WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE ordinary_rows SET payload[1:2] = ARRAY[7, 8] WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE ordinary_rows SET matrix[1][2] = 9 WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE ordinary_rows SET payload[-1] = 7 WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE ordinary_rows SET payload[:2] = ARRAY[7, 8] WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                'UPDATE ordinary_rows SET "payload"[1] = 7 WHERE id = 1',
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE ordinary_rows AS o SET payload[1] = 7 WHERE o.id = 1",
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                "UPDATE app.ordinary_rows SET payload[1] = 7 WHERE id = 1",
                [target("ordinary_rows", schema="app")],
            ),
            (
                "postgres",
                'UPDATE "odd.schema"."ordinary.rows" SET payload[1] = 7 WHERE id = 1',
                [target("ordinary.rows", schema="odd.schema")],
            ),
            (
                "postgres",
                (
                    "WITH changed AS ("
                    "UPDATE ordinary_rows SET payload[1] = 7 RETURNING id"
                    ") SELECT id FROM changed"
                ),
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                (
                    "WITH changed AS ("
                    "UPDATE ordinary_rows SET payload[1] = 7 RETURNING id"
                    ") INSERT INTO audit_rows SELECT id FROM changed"
                ),
                [target("audit_rows"), target("ordinary_rows")],
            ),
            (
                "postgres",
                (
                    "UPDATE ordinary_rows "
                    "SET payload[1] = 7, note = 'changed' WHERE id = 1"
                ),
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                (
                    "UPDATE ordinary_rows SET payload[1] = 7 FROM source_rows "
                    "WHERE source_rows.id = ordinary_rows.id"
                ),
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                (
                    "INSERT INTO ordinary_rows (id, payload) VALUES (1, ARRAY[1]) "
                    "ON CONFLICT (id) DO UPDATE SET payload[1] = 7"
                ),
                [target("ordinary_rows")],
            ),
            (
                "postgres",
                (
                    "MERGE INTO ordinary_rows AS o USING incoming AS i ON o.id = i.id "
                    "WHEN MATCHED THEN UPDATE SET payload[1] = 7"
                ),
                [target("ordinary_rows")],
            ),
            (
                "duckdb",
                "UPDATE ordinary_rows SET payload.x = 7 WHERE id = 1",
                [target("ordinary_rows")],
            ),
            (
                "duckdb",
                "UPDATE ordinary_rows AS o SET payload.x = 7 WHERE o.id = 1",
                [target("ordinary_rows")],
            ),
            (
                "duckdb",
                (
                    "WITH changed AS ("
                    "UPDATE ordinary_rows SET payload.x = 7 RETURNING id"
                    ") SELECT id FROM changed"
                ),
                [target("ordinary_rows")],
            ),
            (
                "mysql",
                "UPDATE ordinary_rows SET payload.x = 7 WHERE id = 1",
                [target("ordinary_rows")],
            ),
        )

        self.assertEqual(len(cases), 20)
        for dialect, statement, expected_targets in cases:
            with self.subTest(dialect=dialect, statement=statement):
                analysis = prepared(statement, dialect=dialect)["analysis"]
                self.assertFalse(
                    analysis["existing_row_mutations"]["evidence_complete"]
                )
                self.assertEqual(
                    analysis["direct_writes"],
                    {
                        "targets": expected_targets,
                        "evidence_complete": True,
                    },
                )

    def test_mysql_multi_target_update_keeps_ambiguity_bounded(self) -> None:
        cases = (
            (
                "UPDATE people p JOIN extra e ON e.id = p.id SET mystery.active = 0",
                [target("people"), target("extra")],
                False,
            ),
            (
                "UPDATE people p JOIN extra e ON e.id = p.id SET p.active = 0",
                [target("people")],
                True,
            ),
            (
                (
                    "UPDATE people p JOIN extra e ON e.id = p.id "
                    "SET p.active = 0, e.value = 1"
                ),
                [target("people"), target("extra")],
                True,
            ),
        )

        for statement, expected_targets, expected_complete in cases:
            with self.subTest(statement=statement):
                self.assertEqual(
                    prepared(statement, dialect="mysql")["analysis"]["direct_writes"],
                    {
                        "targets": expected_targets,
                        "evidence_complete": expected_complete,
                    },
                )


if __name__ == "__main__":
    unittest.main()

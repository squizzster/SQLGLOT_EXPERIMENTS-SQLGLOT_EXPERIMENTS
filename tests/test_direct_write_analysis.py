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


if __name__ == "__main__":
    unittest.main()

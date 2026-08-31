"""Exercise authoritative INSERT target/column facts through the public API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypedDict, cast

from sqlglot_experiments import InsertAnalysis, PreparedStatement, prepare_statement


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    dialect: str
    sql: str


class CaseResult(TypedDict):
    name: str
    statement_type: str
    insert: InsertAnalysis | None


CASES = (
    Case(
        "adversarial_multirow_upsert",
        "sqlite",
        'INSERT INTO "inventory.log" ("select", "sku.code", qty, note) '
        "VALUES (1, 'A-1', 3, 'hostile''; DROP TABLE x; --'), "
        "(2, 'B-2', 1, 'new') "
        'ON CONFLICT ("sku.code") DO UPDATE SET qty = excluded.qty '
        'RETURNING "select", "sku.code"',
    ),
    Case(
        "mysql_set",
        "mysql",
        "INSERT INTO reporting.inventory SET `sku.code` = 'A-1', qty = 3",
    ),
    Case(
        "cte_insert_select",
        "postgres",
        "WITH incoming AS (SELECT 'A-1' AS sku, 3 AS qty) "
        "INSERT INTO inventory (sku, qty) SELECT * FROM incoming",
    ),
    Case(
        "unknown_positional_columns",
        "sqlite",
        "INSERT INTO inventory VALUES ('A-1', 3)",
    ),
    Case(
        "replace_is_not_insert",
        "sqlite",
        "REPLACE INTO inventory (sku, qty) VALUES ('A-1', 3)",
    ),
)


def run_experiment() -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in CASES:
        result = prepare_statement(
            case.sql,
            source_dialect=case.dialect,
            target_dialect=case.dialect,
        )
        if not result["success"] or result["envelope_type"] != "prepared":
            raise AssertionError((case.name, result))
        prepared = cast(PreparedStatement, result)
        results.append(
            {
                "name": case.name,
                "statement_type": prepared["statement_type"],
                "insert": prepared["analysis"]["insert"],
            }
        )
    return results


if __name__ == "__main__":
    print(json.dumps(run_experiment(), indent=2, sort_keys=True))

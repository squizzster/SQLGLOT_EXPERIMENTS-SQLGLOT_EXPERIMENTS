from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

from sqlglot_experiments import PreparedStatement, prepare_statement

DEFAULT_DATABASE = Path("runtime/sqlite_consumer.sqlite3")


class DemoCase(TypedDict):
    package: PreparedStatement
    rows: list[tuple[Any, ...]]


class DemoReport(TypedDict):
    hardcoded: DemoCase
    parameterized: DemoCase


def create_demo_database(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE IF EXISTS big_table")
        connection.execute(
            """
            CREATE TABLE big_table (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                value INTEGER NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO big_table (name, value, category, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("North", 10, "sales", "2026-08-27"),
                ("South", 20, "support", "2026-08-28"),
                ("West", 30, "sales", "2026-08-29"),
            ],
        )


def execute_package(
    connection: sqlite3.Connection,
    package: PreparedStatement,
) -> list[tuple[Any, ...]]:
    cursor = connection.execute(package["sql"], package["bindings"])
    return cursor.fetchall()


def run_demo(database: Path = DEFAULT_DATABASE) -> DemoReport:
    create_demo_database(database)
    hardcoded_package = prepare_statement(
        """
        SELECT id, name, value, category, created_at
        FROM big_table
        WHERE category = 'sales'
        """,
        source_dialect="sqlite",
        target_dialect="sqlite",
    )
    parameterized_package = prepare_statement(
        """
        SELECT id, name, value, category, created_at
        FROM big_table
        WHERE category = ?
        """,
        bindings=["sales"],
        source_dialect="sqlite",
        target_dialect="sqlite",
    )
    with sqlite3.connect(database) as connection:
        hardcoded_rows = execute_package(connection, hardcoded_package)
        parameterized_rows = execute_package(connection, parameterized_package)
    return {
        "hardcoded": {"package": hardcoded_package, "rows": hardcoded_rows},
        "parameterized": {
            "package": parameterized_package,
            "rows": parameterized_rows,
        },
    }


def main() -> None:
    print(json.dumps(run_demo(), indent=2))


if __name__ == "__main__":
    main()

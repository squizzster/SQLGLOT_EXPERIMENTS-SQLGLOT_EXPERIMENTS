"""Explicit syntax-dialect routes for extended non-standard statement families."""

from __future__ import annotations

REPLACE_DIALECTS = frozenset({"duckdb", "mysql", "sqlite"})

MERGE_DIALECTS = frozenset(
    {
        "bigquery",
        "databricks",
        "duckdb",
        "oracle",
        "postgres",
        "snowflake",
        "tsql",
    }
)

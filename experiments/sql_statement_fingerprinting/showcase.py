"""Representative evidence for the small fingerprint experiment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fingerprint import fingerprint_sql, fingerprint_versions


@dataclass(frozen=True, slots=True)
class Example:
    label: str
    dialect: str
    profile: str
    sql: str
    values: tuple[Any, ...]


EXAMPLES = (
    Example(
        "SQLite SELECT",
        "sqlite",
        "qmark",
        "SELECT id, name FROM people WHERE category = ?",
        ("books",),
    ),
    Example(
        "MySQL SELECT",
        "mysql",
        "format",
        "SELECT id, name FROM people WHERE category = %s",
        ("music",),
    ),
    Example(
        "PostgreSQL SELECT",
        "postgres",
        "dollar_numeric",
        "SELECT id, name FROM people WHERE category = $1",
        ("films",),
    ),
    Example(
        "SQLite INSERT Mark",
        "sqlite",
        "qmark",
        "INSERT INTO people (forename, surname) VALUES (?, ?)",
        ("Mark", "Smith"),
    ),
    Example(
        "SQLite INSERT Paul",
        "sqlite",
        "qmark",
        "INSERT INTO people (forename, surname) VALUES (?, ?)",
        ("Paul", "Jones"),
    ),
    Example(
        "PostgreSQL UPDATE",
        "postgres",
        "dollar_numeric",
        "UPDATE public.people SET forename = $1 WHERE person_id = $2",
        ("Ada", 10),
    ),
)


def main() -> None:
    print("SQLGlot statement-shape fingerprint experiment")
    print("Values are displayed, but never passed to fingerprint_sql().\n")

    for example in EXAMPLES:
        known = fingerprint_sql(
            example.sql,
            read=example.dialect,
            placeholder_profile=example.profile,
        )
        unknown = fingerprint_sql(
            example.sql,
            placeholder_profile=example.profile,
        )
        print(example.label)
        print(f"  values:         {example.values!r}")
        print(f"  bindings:       {unknown.binding_pattern}")
        print(f"  dialect-known:  {known.sha256_hex}")
        print(f"  engine-unknown: {unknown.sha256_hex}")
        print(f"  canonical:      {unknown.canonical_sql}\n")

    complex_sql = (
        Path(__file__).parents[2] / "assets/original_source/verified_sqlite_query.sql"
    ).read_text()
    complex_versions = fingerprint_versions(complex_sql, read="sqlite")
    print("Original 196-line complex SQLite SELECT")
    print(f"  dialect-known:  {complex_versions.dialect_known.sha256_hex}")
    print(f"  engine-unknown: {complex_versions.engine_unknown.sha256_hex}")


if __name__ == "__main__":
    main()

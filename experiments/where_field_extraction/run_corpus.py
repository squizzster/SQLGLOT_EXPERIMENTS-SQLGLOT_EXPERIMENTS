"""Run WHERE-field extraction across every retained adversarial SQL case."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlglot.errors import SqlglotError

from .extractor import ExtractionResult, FieldReference, extract_where_fields

PROJECT_ROOT = Path(__file__).parents[2]
EXTERNAL_CASES = PROJECT_ROOT / "assets/sql_torture_pack/cases.json.gz"
LOCAL_CASES = PROJECT_ROOT / "tests/fixtures/sql_torture_extensions.json"
COMPLEX_SQL = PROJECT_ROOT / "assets/original_source/verified_sqlite_query.sql"


def run_corpus() -> dict[str, Any]:
    cases = [
        *(('external', case) for case in _load_cases(EXTERNAL_CASES)),
        *(('local', case) for case in _load_cases(LOCAL_CASES)),
        (
            "retained_complex",
            {
                "name": "original.verified_sqlite_query",
                "sql": COMPLEX_SQL.read_text(encoding="utf-8"),
            },
        ),
    ]

    statement_types: Counter[str] = Counter()
    resolutions: Counter[str] = Counter()
    certainties: Counter[str] = Counter()
    source_counts: Counter[str] = Counter(source for source, _ in cases)
    parse_failures: list[dict[str, str]] = []
    unresolved_examples: list[dict[str, object]] = []
    totals: Counter[str] = Counter()

    for source_set, case in cases:
        try:
            extraction = extract_where_fields(case["sql"], dialect="sqlite")
        except SqlglotError as error:
            parse_failures.append(
                {
                    "source_set": source_set,
                    "name": case["name"],
                    "error_type": type(error).__name__,
                }
            )
            continue

        _add_extraction_totals(totals, extraction)
        for statement in extraction["statements"]:
            statement_types[statement["statement_type"]] += 1
            for field in statement["fields"]:
                resolutions[field["resolution"]] += 1
                certainties[field["certainty"]] += 1
                if field["certainty"] == "unresolved" and len(unresolved_examples) < 20:
                    unresolved_examples.append(
                        _example(source_set, case["name"], field)
                    )

    return {
        "experiment": "where-field-extraction/v1",
        "input": {
            "torture_case_count": source_counts["external"] + source_counts["local"],
            "retained_complex_case_count": source_counts["retained_complex"],
            "total_case_count": len(cases),
            "source_case_counts": dict(sorted(source_counts.items())),
            "scope": "primary case SQL plus the retained complex SELECT; setup SQL excluded",
        },
        "definition": {
            "field": "every SQLGlot Column node with a WHERE ancestor",
            "where_ownership": "the nearest WHERE ancestor owns each occurrence",
            "table_resolution": "AST scopes and aliases only; no schema or database metadata",
            "database_resolution": "reported only when present on the resolved AST table",
        },
        "summary": {
            "parsed_case_count": len(cases) - len(parse_failures),
            "parse_failure_count": len(parse_failures),
            "statement_count": totals["statement_count"],
            "where_clause_count": totals["where_clause_count"],
            "field_reference_count": totals["field_reference_count"],
            "oracle_field_reference_count": totals["oracle_field_reference_count"],
            "field_capture_mismatch_count": totals["field_capture_mismatch_count"],
            "scope_error_count": totals["scope_error_count"],
            "table_resolved_field_count": totals["table_resolved_field_count"],
            "database_resolved_field_count": totals["database_resolved_field_count"],
        },
        "statement_type_counts": dict(sorted(statement_types.items())),
        "certainty_counts": dict(sorted(certainties.items())),
        "resolution_counts": dict(sorted(resolutions.items())),
        "parse_failures": parse_failures,
        "unresolved_examples": unresolved_examples,
    }


def _load_cases(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)["cases"]
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def _add_extraction_totals(
    totals: Counter[str],
    extraction: ExtractionResult,
) -> None:
    for name in (
        "statement_count",
        "where_clause_count",
        "field_reference_count",
        "oracle_field_reference_count",
        "field_capture_mismatch_count",
        "scope_error_count",
    ):
        totals[name] += extraction[name]
    for statement in extraction["statements"]:
        for field in statement["fields"]:
            totals["table_resolved_field_count"] += field["table"] is not None
            totals["database_resolved_field_count"] += field["database"] is not None


def _example(
    source_set: str,
    case_name: str,
    field: FieldReference,
) -> dict[str, object]:
    return {
        "source_set": source_set,
        "case": case_name,
        "column_sql": field["column_sql"],
        "field": field["field"],
        "written_table": field["written_table"],
        "resolution": field["resolution"],
    }


def main() -> None:
    print(json.dumps(run_corpus(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

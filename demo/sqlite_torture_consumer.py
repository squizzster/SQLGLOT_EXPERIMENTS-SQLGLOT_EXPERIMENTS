from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from sqlglot_experiments import (
    BindingCountError,
    StatementPreparationError,
    prepare_statement,
)

PROJECT_ROOT = Path(__file__).parents[1]
TORTURE_ROOT = PROJECT_ROOT / "assets/sql_torture_pack"
DEFAULT_CASES = TORTURE_ROOT / "cases.json.gz"
DEFAULT_FIXTURE = TORTURE_ROOT / "fixture.sqlite"
DEFAULT_LOCAL_CASES = PROJECT_ROOT / "tests/fixtures/sql_torture_extensions.json"

EXPECTED_ASSET_HASHES = {
    "cases.json.gz": "9211b6f91d751e40b2a617d33535804a25ef0e4c51435f61be5b5ba367a11e4f",
    "fixture.sqlite": "dd7c8326c053eb9920d98508db65d638e1b0fdc32f4a86750eaa9484c2197dfe",
}
PASS_STATUSES = {
    "equivalent",
    "expected_engine_error",
    "expected_brick_rejection",
}


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$bytes_hex"}:
            return bytes.fromhex(value["$bytes_hex"])
        if set(value) == {"$int"}:
            return int(value["$int"])
        if set(value) == {"$float"}:
            return float(value["$float"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _value_type(value: object) -> str:
    return {
        type(None): "null",
        int: "integer",
        float: "real",
        str: "text",
        bytes: "blob",
        bool: "boolean",
    }.get(type(value), type(value).__name__)


def _values_equal(
    actual: object,
    expected: object,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, float):
        actual_float = cast(float, actual)
        return (
            math.isnan(actual_float)
            if math.isnan(expected)
            else math.isclose(
                actual_float,
                expected,
                rel_tol=relative_tolerance,
                abs_tol=absolute_tolerance,
            )
        )
    return actual == expected


def _rows_equal(
    actual: Sequence[object],
    expected: Sequence[object],
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    return len(actual) == len(expected) and all(
        _values_equal(
            actual_value,
            expected_value,
            relative_tolerance=relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        )
        for actual_value, expected_value in zip(actual, expected, strict=True)
    )


def _assert_rows(
    actual: list[tuple[object, ...]],
    expected: list[tuple[object, ...]],
    *,
    expected_types: list[list[str]],
    ordered: bool,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> None:
    actual_types = [[_value_type(value) for value in row] for row in actual]
    if actual_types != expected_types and ordered:
        raise AssertionError(
            f"row types differ: expected {expected_types[:8]!r}, "
            f"received {actual_types[:8]!r}"
        )
    if len(actual) != len(expected):
        raise AssertionError(
            f"row count differs: expected {len(expected)}, received {len(actual)}"
        )
    if ordered:
        for index, (actual_row, expected_row) in enumerate(
            zip(actual, expected, strict=True)
        ):
            if not _rows_equal(
                actual_row,
                expected_row,
                relative_tolerance=relative_tolerance,
                absolute_tolerance=absolute_tolerance,
            ):
                raise AssertionError(
                    f"row {index} differs: expected {expected_row!r}, "
                    f"received {actual_row!r}"
                )
        return

    edges = [
        [
            expected_index
            for expected_index, expected_row in enumerate(expected)
            if [_value_type(value) for value in actual_row]
            == expected_types[expected_index]
            and _rows_equal(
                actual_row,
                expected_row,
                relative_tolerance=relative_tolerance,
                absolute_tolerance=absolute_tolerance,
            )
        ]
        for actual_row in actual
    ]
    matched: dict[int, int] = {}

    def augment(actual_index: int, seen: set[int]) -> bool:
        for expected_index in edges[actual_index]:
            if expected_index in seen:
                continue
            seen.add(expected_index)
            if expected_index not in matched or augment(matched[expected_index], seen):
                matched[expected_index] = actual_index
                return True
        return False

    for actual_index, actual_row in enumerate(actual):
        if not augment(actual_index, set()):
            raise AssertionError(
                f"no multiplicity-preserving bag match for {actual_row!r}"
            )


def _error_details(error: Exception) -> dict[str, object]:
    return {
        "exception": type(error).__name__,
        "sqlite_errorname": getattr(error, "sqlite_errorname", None),
        "sqlite_errorcode": getattr(error, "sqlite_errorcode", None),
        "message": str(error),
    }


def _matches_expected_error(error: Exception, expected: Mapping[str, Any]) -> bool:
    expected_names = str(expected.get("sqlite_errorname", "")).split("|")
    actual_name = getattr(error, "sqlite_errorname", None)
    actual_code = getattr(error, "sqlite_errorcode", None)
    fallback_code = expected.get("sqlite_errorcode_fallback")
    name_matches = (
        expected_names == [""]
        or actual_name in expected_names
        or (fallback_code is not None and actual_code == fallback_code)
    )
    return (
        type(error).__name__ == expected["exception"]
        and name_matches
        and re.search(
            str(expected["message_regex"]),
            str(error),
            flags=re.IGNORECASE,
        )
        is not None
    )


def _preparation_failure_kind(error: Exception) -> str:
    if isinstance(error, BindingCountError):
        return "binding_count"
    if isinstance(error, StatementPreparationError) and str(error).startswith(
        "expected exactly one SQL statement"
    ):
        return "statement_count"
    if isinstance(error, StatementPreparationError):
        return "statement_preparation"
    return type(error).__name__


def _is_expected_brick_rejection(
    error: Exception,
    expected: Mapping[str, Any],
) -> bool:
    client_side_sqlite_error = (
        expected.get("exception") == "ProgrammingError"
        and not expected.get("sqlite_errorname")
        and expected.get("sqlite_errorcode_fallback") is None
    )
    return client_side_sqlite_error and _preparation_failure_kind(error) in {
        "binding_count",
        "statement_count",
    }


def _execute_setup(connection: sqlite3.Connection, case: Mapping[str, Any]) -> None:
    for statement in case.get("setup", []):
        cursor = connection.execute(statement["sql"], _decode(statement["params"]))
        if cursor.description is not None:
            cursor.fetchall()


def _run_case(
    connection: sqlite3.Connection,
    case: Mapping[str, Any],
    *,
    source_set: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    expected_error = case.get("expected_error")
    result: dict[str, Any] = {
        "name": case["name"],
        "source_set": source_set,
        "pair": case.get("pair"),
    }
    connection.execute("SAVEPOINT torture_case")
    deadline = time.monotonic() + timeout_seconds
    connection.set_progress_handler(
        lambda: int(time.monotonic() > deadline),
        10_000,
    )
    try:
        _execute_setup(connection, case)
        params = _decode(case.get("params", []))
        try:
            # Static cast only: raw mappings still reach the API unchanged.
            package = prepare_statement(
                str(case["sql"]),
                bindings=cast(Sequence[object], params),
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
        except Exception as error:  # noqa: BLE001 - preparation failures are evidence
            result["status"] = (
                "expected_brick_rejection"
                if expected_error is not None
                and _is_expected_brick_rejection(error, expected_error)
                else "prepare_failure"
            )
            result["failure_kind"] = _preparation_failure_kind(error)
            result["error"] = _error_details(error)
            return result

        result["package"] = package
        try:
            cursor = connection.execute(package["sql"], package["bindings"])
            actual_rows = [tuple(row) for row in cursor.fetchall()]
        except Exception as error:  # noqa: BLE001 - target failures are evidence
            if expected_error is not None and _matches_expected_error(
                error, expected_error
            ):
                result["status"] = "expected_engine_error"
                result["error"] = _error_details(error)
            else:
                result["status"] = (
                    "expected_error_changed"
                    if expected_error is not None
                    else "execution_failure"
                )
                result["error"] = _error_details(error)
            return result

        if expected_error is not None:
            result["status"] = "expected_error_lost"
            return result

        expected_columns = case.get("expected_columns")
        if expected_columns is not None:
            actual_columns = [column[0] for column in cursor.description or ()]
            if actual_columns != expected_columns:
                result["status"] = "column_mismatch"
                result["expected_columns"] = expected_columns
                result["actual_columns"] = actual_columns
                return result

        expected_rows = [tuple(row) for row in _decode(case.get("expected_rows", []))]
        expected_types = case.get("expected_types") or [
            [_value_type(value) for value in row] for row in expected_rows
        ]
        try:
            _assert_rows(
                actual_rows,
                expected_rows,
                expected_types=expected_types,
                ordered=case.get("comparison", "ordered") == "ordered",
                relative_tolerance=float(case.get("float_relative_tolerance", 1e-10)),
                absolute_tolerance=float(case.get("float_absolute_tolerance", 1e-9)),
            )
        except AssertionError as error:
            result["status"] = "result_mismatch"
            result["mismatch"] = str(error)
            result["actual_rows_head"] = actual_rows[:8]
            result["expected_rows_head"] = expected_rows[:8]
            return result

        result["status"] = "equivalent"
        result["row_count"] = len(actual_rows)
        return result
    except Exception as error:  # noqa: BLE001 - setup failures are evidence
        result["status"] = "setup_failure"
        result["error"] = _error_details(error)
        return result
    finally:
        connection.set_progress_handler(None, 0)
        try:
            connection.execute("ROLLBACK TO torture_case")
            connection.execute("RELEASE torture_case")
        except Exception as error:  # noqa: BLE001 - cleanup failures are evidence
            result["status"] = "cleanup_failure"
            result["cleanup_error"] = _error_details(error)


def _load_document(path: Path, *, expected_format: str) -> list[dict[str, Any]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != expected_format:
        raise ValueError(
            f"unexpected case format in {path}: {document.get('format')!r}"
        )
    return document["cases"]


def _verify_asset(path: Path, expected_hash: str) -> None:
    observed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed_hash != expected_hash:
        raise ValueError(
            f"asset checksum mismatch for {path}: "
            f"expected {expected_hash}, received {observed_hash}"
        )


def run_torture_suite(
    *,
    cases_path: Path = DEFAULT_CASES,
    fixture_path: Path = DEFAULT_FIXTURE,
    local_cases_path: Path | None = DEFAULT_LOCAL_CASES,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Run external and local SQL cases through the unchanged public API."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if cases_path == DEFAULT_CASES:
        _verify_asset(cases_path, EXPECTED_ASSET_HASHES["cases.json.gz"])
    if fixture_path == DEFAULT_FIXTURE:
        _verify_asset(fixture_path, EXPECTED_ASSET_HASHES["fixture.sqlite"])

    external_cases = _load_document(
        cases_path,
        expected_format="sql-torture-pack.cases.v1",
    )
    case_sets: list[tuple[str, list[dict[str, Any]]]] = [("external", external_cases)]
    if local_cases_path is not None:
        case_sets.append(
            (
                "local",
                _load_document(
                    local_cases_path,
                    expected_format="sqlglot-experiments.torture-extensions.v1",
                ),
            )
        )

    with tempfile.TemporaryDirectory(prefix="sqlglot-torture-") as directory:
        working_fixture = Path(directory) / "fixture.sqlite"
        shutil.copy2(fixture_path, working_fixture)
        connection = sqlite3.connect(
            working_fixture,
            isolation_level=None,
            timeout=0,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.execute("PRAGMA busy_timeout=0")
        try:
            results = [
                _run_case(
                    connection,
                    case,
                    source_set=source_set,
                    timeout_seconds=timeout_seconds,
                )
                for source_set, cases in case_sets
                for case in cases
            ]
        finally:
            connection.close()

    status_counts = Counter(result["status"] for result in results)
    source_status_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        source_status_counts[result["source_set"]][result["status"]] += 1
    packages = [result["package"] for result in results if "package" in result]
    failures = [result for result in results if result["status"] not in PASS_STATUSES]
    summary = {
        "scope": (
            "all primary case statements; setup statements are direct fixture control"
        ),
        "case_count": len(results),
        "source_case_counts": {
            source_set: len(cases) for source_set, cases in case_sets
        },
        "status_counts": dict(sorted(status_counts.items())),
        "source_status_counts": {
            source_set: dict(sorted(counts.items()))
            for source_set, counts in sorted(source_status_counts.items())
        },
        "package_count": len(packages),
        "correct_behavior_count": len(results) - len(failures),
        "genuine_failure_count": len(failures),
        "hardcoded_package_count": sum(
            package["analysis"]["hardcoded_value_count"] > 0 for package in packages
        ),
        "hardcoded_value_count": sum(
            package["analysis"]["hardcoded_value_count"] for package in packages
        ),
        "hardcoded_field_count": sum(
            package["analysis"]["hardcoded_field_count"] for package in packages
        ),
    }
    return {"summary": summary, "failures": failures, "results": results}


def _json_default(value: object) -> object:
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    raise TypeError(f"cannot encode {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay the retained SQL torture corpus through Brick 1."
    )
    parser.add_argument(
        "--all-results",
        action="store_true",
        help="include every per-case result instead of only failures",
    )
    arguments = parser.parse_args()
    report = run_torture_suite()
    visible_report = (
        report
        if arguments.all_results
        else {
            "summary": report["summary"],
            "failures": report["failures"],
        }
    )
    print(json.dumps(visible_report, indent=2, default=_json_default))
    raise SystemExit(int(report["summary"]["genuine_failure_count"] > 0))


if __name__ == "__main__":
    main()

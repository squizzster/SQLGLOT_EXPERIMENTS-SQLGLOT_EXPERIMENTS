from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from sqlglot_experiments import (
    InputBindings,
    PreparedStatement,
    prepare_statement,
    set_lru_cache_size,
    statement_api,
)


def prepared(
    sql: str,
    *,
    bindings: InputBindings | None = None,
    source_dialect: str = "sqlite",
    target_dialect: str = "sqlite",
) -> PreparedStatement:
    result = prepare_statement(
        sql,
        bindings=bindings,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    if not result["success"]:
        raise AssertionError(result["msg"])
    return cast(PreparedStatement, result)


class StatementStructureCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        result = set_lru_cache_size(128)
        self.assertEqual(
            result,
            {"success": True, "warnings": False, "msg": "success: ok"},
        )

    def tearDown(self) -> None:
        set_lru_cache_size(128)

    def test_current_values_are_applied_to_one_named_structure(self) -> None:
        sql = "SELECT * FROM people WHERE id = :person_id"

        first = prepared(sql, bindings={"person_id": 1})
        second = prepared(sql, bindings={"person_id": 500, "unused": "ignored"})

        self.assertEqual(first["bindings"], [1])
        self.assertEqual(second["bindings"], [500])
        self.assertEqual(first["sql"], second["sql"])
        self.assertEqual(first["sql_fingerprint"], second["sql_fingerprint"])
        info = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual((info.hits, info.misses, info.currsize), (1, 1, 1))

    def test_cached_route_preserves_reuse_literals_and_target_order(self) -> None:
        repeated_sql = (
            "SELECT * FROM jobs "
            "WHERE tenant = :value AND state = 'open' OR owner = :value"
        )
        prepared(repeated_sql, bindings={"value": 7})
        repeated = prepared(repeated_sql, bindings={"value": 99})

        limit_sql = "SELECT n FROM numbers WHERE kind = ? LIMIT ?, ?"
        prepared(limit_sql, bindings=["prime", 2, 3])
        reordered = prepared(limit_sql, bindings=["odd", 10, 20])

        self.assertEqual(repeated["bindings"], [99, "open", 99])
        self.assertEqual(reordered["bindings"], ["odd", 20, 10])
        info = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual((info.hits, info.misses, info.currsize), (2, 2, 2))

    def test_each_hit_returns_fresh_mutable_envelope_containers(self) -> None:
        sql = "SELECT * FROM people WHERE id = ?"
        first = prepared(sql, bindings=[1])
        first["dialect"][0] = "broken"
        first["bindings"][0] = "broken"
        first["where_fields"].append("invented.field")
        first["analysis"]["hardcoded_value_count"] = 999

        second = prepared(sql, bindings=[2])

        self.assertEqual(second["dialect"], ["sqlite", "sqlite"])
        self.assertEqual(second["bindings"], [2])
        self.assertEqual(second["where_fields"], ["people.id"])
        self.assertEqual(second["analysis"]["hardcoded_value_count"], 0)

    def test_cached_insert_analysis_returns_fresh_nested_containers(self) -> None:
        sql = "INSERT INTO people (lookup_code, payload) VALUES (?, ?)"
        first = prepared(sql, bindings=["A", 1])
        first_analysis = first["analysis"]["insert"]
        self.assertIsNotNone(first_analysis)
        assert first_analysis is not None
        first_analysis["target"]["table"] = "broken"
        first_analysis["supplied_columns"].append("invented")
        binding_rows = first_analysis["plain_values_binding_rows"]
        self.assertIsNotNone(binding_rows)
        assert binding_rows is not None
        binding_rows[0].append(999)

        second = prepared(sql, bindings=["B", 2])

        self.assertEqual(
            second["analysis"]["insert"],
            {
                "target": {
                    "catalog": None,
                    "schema": None,
                    "table": "people",
                },
                "supplied_columns": ["lookup_code", "payload"],
                "plain_values_binding_rows": [[0, 1]],
            },
        )

    def test_cached_mutation_analysis_returns_fresh_nested_containers(self) -> None:
        sql = "UPDATE people SET active = ? WHERE id = ?"
        first = prepared(sql, bindings=[False, 1])
        first_analysis = first["analysis"]["existing_row_mutations"]
        first_direct_writes = first["analysis"]["direct_writes"]
        first_analysis["evidence_complete"] = False
        first_analysis["effects"][0]["target"]["table"] = "broken"
        first_direct_writes["evidence_complete"] = False
        first_direct_writes["targets"][0]["table"] = "broken"
        updated_columns = first_analysis["effects"][0]["updated_columns"]
        self.assertIsNotNone(updated_columns)
        assert updated_columns is not None
        updated_columns.append("invented")

        second = prepared(sql, bindings=[True, 2])

        self.assertEqual(
            second["analysis"]["existing_row_mutations"],
            {
                "effects": [
                    {
                        "target": {
                            "catalog": None,
                            "schema": None,
                            "table": "people",
                        },
                        "updated_columns": ["active"],
                        "deletes_rows": False,
                    }
                ],
                "evidence_complete": True,
            },
        )
        self.assertEqual(
            second["analysis"]["direct_writes"],
            {
                "targets": [{"catalog": None, "schema": None, "table": "people"}],
                "evidence_complete": True,
            },
        )

    def test_normalized_dialects_share_one_structure(self) -> None:
        sql = "SELECT * FROM people WHERE id = ?"
        prepared(
            sql,
            bindings=[1],
            source_dialect=" SQLite ",
            target_dialect="SQLITE",
        )
        prepared(sql, bindings=[2])

        info = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual((info.hits, info.misses, info.currsize), (1, 1, 1))

    def test_default_cache_is_bounded_to_128_lru_entries(self) -> None:
        for index in range(129):
            prepared(
                f"SELECT * FROM people WHERE id = ? /* cache-{index} */",
                bindings=[index],
            )

        full = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(full.maxsize, 128)
        self.assertEqual((full.hits, full.misses, full.currsize), (0, 129, 128))

        prepared(
            "SELECT * FROM people WHERE id = ? /* cache-0 */",
            bindings=[999],
        )
        evicted = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(
            (evicted.hits, evicted.misses, evicted.currsize), (0, 130, 128)
        )

    def test_public_api_resizes_and_empties_this_process_cache(self) -> None:
        sql = "SELECT * FROM people WHERE id = ?"
        prepared(sql, bindings=[1])
        previous_cache = statement_api._prepare_statement_structure

        result = set_lru_cache_size(size=64)

        self.assertEqual(
            result,
            {"success": True, "warnings": False, "msg": "success: ok"},
        )
        self.assertIsNot(statement_api._prepare_statement_structure, previous_cache)
        self.assertEqual(previous_cache.cache_info().currsize, 0)
        resized = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(
            (resized.maxsize, resized.hits, resized.misses, resized.currsize),
            (64, 0, 0, 0),
        )

        prepared(sql, bindings=[2])
        populated = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(
            (populated.maxsize, populated.hits, populated.misses, populated.currsize),
            (64, 0, 1, 1),
        )

    def test_invalid_resize_calls_return_envelopes_without_changing_cache(self) -> None:
        prepared("SELECT * FROM people WHERE id = ?", bindings=[1])
        active_cache = statement_api._prepare_statement_structure
        expected = {
            "success": False,
            "warnings": False,
            "msg": "failure: lru cache size must be a positive integer",
        }

        for size in (0, -1, True, 1.5, "128"):
            with self.subTest(size=size):
                result = set_lru_cache_size(size)  # type: ignore[call-overload]
                self.assertEqual(result, expected)
                self.assertIs(
                    statement_api._prepare_statement_structure,
                    active_cache,
                )

        info = active_cache.cache_info()
        self.assertEqual(
            (info.maxsize, info.hits, info.misses, info.currsize),
            (128, 0, 1, 1),
        )

    def test_malformed_resize_calls_return_specific_fixed_envelopes(self) -> None:
        cases = (
            (
                (),
                {},
                "failure: lru cache size is required",
            ),
            (
                (64, 128),
                {},
                "failure: only size may be passed positionally",
            ),
            (
                (64,),
                {"size": 128},
                "failure: size was provided more than once",
            ),
            (
                (),
                {"maxsize": 64},
                "failure: unexpected argument: maxsize",
            ),
        )

        for args, kwargs, message in cases:
            with self.subTest(args=args, kwargs=kwargs):
                result = set_lru_cache_size(  # type: ignore[call-overload]
                    *args,
                    **kwargs,
                )
                self.assertEqual(
                    result,
                    {"success": False, "warnings": False, "msg": message},
                )
                self.assertEqual(
                    statement_api._prepare_statement_structure.cache_info().maxsize,
                    128,
                )

    def test_non_prepared_calls_do_not_reach_the_structure_cache(self) -> None:
        for _ in range(2):
            result = prepare_statement(
                "SELECT FROM",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            self.assertEqual(result["success"], False)

        parse_failure = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(
            (parse_failure.hits, parse_failure.misses, parse_failure.currsize),
            (0, 0, 0),
        )

        for _ in range(2):
            result = prepare_statement(
                "CREATE TABLE example (id INTEGER)",
                source_dialect="sqlite",
                target_dialect="sqlite",
            )
            self.assertEqual(result["success"], True)

        generic_acceptance = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(
            (
                generic_acceptance.hits,
                generic_acceptance.misses,
                generic_acceptance.currsize,
            ),
            (0, 0, 0),
        )

        result = prepare_statement(
            "SELECT * FROM people WHERE id = ?",
            bindings=[],
            source_dialect="sqlite",
            target_dialect="sqlite",
        )
        self.assertEqual(result["success"], False)
        binding_failure = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(
            (binding_failure.hits, binding_failure.misses, binding_failure.currsize),
            (0, 0, 0),
        )

    def test_concurrent_hits_keep_each_calls_values_isolated(self) -> None:
        sql = "SELECT * FROM people WHERE id = :person_id"
        prepared(sql, bindings={"person_id": 0})

        def call(value: int) -> int:
            result = prepared(sql, bindings={"person_id": value})
            return cast(int, result["bindings"][0])

        values = list(range(1, 65))
        with ThreadPoolExecutor(max_workers=8) as executor:
            observed = list(executor.map(call, values))

        self.assertEqual(observed, values)
        info = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual((info.hits, info.misses, info.currsize), (64, 1, 1))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from sqlglot_experiments import (
    InputBindings,
    PreparedStatement,
    prepare_statement,
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
        statement_api._prepare_statement_structure.cache_clear()

    def tearDown(self) -> None:
        statement_api._prepare_statement_structure.cache_clear()

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

    def test_cache_is_bounded_to_256_lru_entries(self) -> None:
        for index in range(257):
            prepared(
                f"SELECT * FROM people WHERE id = ? /* cache-{index} */",
                bindings=[index],
            )

        full = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual(full.maxsize, 256)
        self.assertEqual((full.hits, full.misses, full.currsize), (0, 257, 256))

        prepared(
            "SELECT * FROM people WHERE id = ? /* cache-0 */",
            bindings=[999],
        )
        evicted = statement_api._prepare_statement_structure.cache_info()
        self.assertEqual((evicted.hits, evicted.misses, evicted.currsize), (0, 258, 256))

    def test_invalid_calls_do_not_leave_cached_structures(self) -> None:
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
            (0, 2, 0),
        )

        statement_api._prepare_statement_structure.cache_clear()
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

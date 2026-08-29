# Verified SQLite SQL bundle

This bundle contains a SQLite-native rewrite of the supplied PostgreSQL-style query and a reproducible test harness.

## Files

- `verified_sqlite_query.sql` — SQLite query.
- `test_fixture.sql` — minimal schema, indexes, and sample data.
- `verify_query.py` — runs the fixture/query in an in-memory SQLite database and asserts key behavior.
- `verification_output.txt` — captured output from the verification run in this environment.

## Run

```bash
python verify_query.py
```

## What is verified

- Recursive category hierarchy CTE.
- SQLite `json_extract()` JSON shredding.
- `unixepoch()` timestamp arithmetic.
- `LAG()` + cumulative `SUM()` sessionization.
- Numeric `RANGE BETWEEN 604800 PRECEDING AND CURRENT ROW` rolling 7-day average.
- `ROW_NUMBER()` replacement for the original LATERAL top-product lookup.
- Anti-semi join with `NOT EXISTS`.
- Full manual equivalent of `ROLLUP(user_id, session_id, category_path, top_product_name)` using `UNION ALL`.
- Fraud-blacklisted users are excluded.
- July lookback data contributes to early-August 7-day averages.

## Deliberate choices

1. Sessions are numbered from 1.
2. A new session starts only when inactivity is **greater than** 30 minutes, matching the original `> 30 minutes` condition. Change `> 1800` to `>= 1800` if exactly 30 minutes should split a session.
3. `LEFT JOIN` keeps events from categories that have no product. Change it to `JOIN` if you want the row-dropping behavior of PostgreSQL `CROSS JOIN LATERAL`.
4. `sales_rank DESC` is preserved from the supplied query. If rank 1 means "best", change it to `ASC`.
5. The query reads from `2026-07-25` so August rows have a complete 7-day lookback, then filters reporting output to August.
6. ISO-like timestamps are stored as consistently formatted text so the range predicates remain index-friendly.

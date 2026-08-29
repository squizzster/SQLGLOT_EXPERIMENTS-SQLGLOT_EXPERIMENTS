from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
schema_sql = (HERE / "test_fixture.sql").read_text()
query_sql = (HERE / "verified_sqlite_query.sql").read_text()

con = sqlite3.connect(":memory:")
con.executescript(schema_sql)

print("SQLite version:", sqlite3.sqlite_version)
print("JSON1 smoke test:", con.execute("SELECT json_extract('{\"x\": 7}', '$.x')").fetchone()[0])
print("unixepoch smoke test:", con.execute("SELECT unixepoch('2026-08-01 00:00:00')").fetchone()[0])

rows = con.execute(query_sql).fetchall()
cols = [d[0] for d in con.execute(query_sql).description]

print("\nColumns:")
print(cols)
print("\nRows:")
for row in rows:
    print(row)

# Basic assertions proving key behavior.
assert all(r[0] != 999 for r in rows if r[0] is not None), "Blacklisted user leaked into output"
assert any(r[0] is None and r[1] is None for r in rows), "Grand-total rollup row missing"
assert any(r[0] == 10 and r[1] == 2 for r in rows), "30+ minute session split not observed"

# First August event for user 10 should include July 30 in its rolling 7-day average:
# (50 + 100) / 2 = 75.0, and the first detail row's peak can only rise from there.
raw = con.execute("""
WITH parsed AS (
  SELECT user_id, event_time,
         CAST(json_extract(payload, '$.cart_value') AS NUMERIC) cart_value,
         unixepoch(event_time) event_epoch
  FROM clickstream_events
  WHERE event_time >= '2026-07-25' AND event_time < '2026-09-01' AND user_id = 10
)
SELECT event_time,
       AVG(cart_value) OVER (
          PARTITION BY user_id ORDER BY event_epoch
          RANGE BETWEEN 604800 PRECEDING AND CURRENT ROW
       )
FROM parsed
ORDER BY event_epoch
""").fetchall()
assert raw[1][1] == 75.0, raw
print("\nAssertions: PASS")

print("\nEXPLAIN QUERY PLAN (selected lines):")
for p in con.execute("EXPLAIN QUERY PLAN " + query_sql):
    detail = p[-1]
    if "clickstream_events" in detail or "idx_clickstream_event_time" in detail or "SEARCH e" in detail:
        print(p)

"""Exercise SQLGlot's public APIs without adding SQL interpretation logic."""

from pathlib import Path
from pprint import pprint

import sqlglot
from sqlglot.lineage import lineage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SQL_PATH = PROJECT_ROOT / "assets/original_source/verified_sqlite_query.sql"

source_sql = SOURCE_SQL_PATH.read_text()

parsed_expression = sqlglot.parse_one(source_sql, read="sqlite")

peak_rolling_spend_lineage = lineage(
    "peak_rolling_spend",
    parsed_expression,
    dialect="sqlite",
)

postgres_sql = sqlglot.transpile(
    source_sql,
    read="sqlite",
    write="postgres",
    pretty=True,
)[0]


if __name__ == "__main__":
    print("\nSQLGlot Expression.dump()\n")
    pprint(parsed_expression.dump(), sort_dicts=False)

    print("\nSQLGlot lineage Node.to_html()\n")
    print(peak_rolling_spend_lineage.to_html())

    print("\nSQLGlot transpile()\n")
    print(postgres_sql)

from __future__ import annotations

import re
import typing as t

from sqlglot import Dialect, ErrorLevel, exp, generator
from sqlglot.dialects.bigquery import BigQuery
from sqlglot.dialects.databricks import Databricks
from sqlglot.dialects.duckdb import DuckDB
from sqlglot.dialects.mysql import MySQL
from sqlglot.dialects.oracle import Oracle
from sqlglot.dialects.postgres import Postgres
from sqlglot.dialects.snowflake import Snowflake
from sqlglot.dialects.sqlite import SQLite
from sqlglot.dialects.tsql import TSQL
from sqlglot.errors import TokenError, UnsupportedError
from sqlglot.generators.duckdb import DuckDBGenerator
from sqlglot.generators.mysql import MySQLGenerator
from sqlglot.generators.postgres import PostgresGenerator
from sqlglot.generators.snowflake import SnowflakeGenerator
from sqlglot.generators.sqlite import SQLiteGenerator
from sqlglot.generators.tsql import TSQLGenerator
from sqlglot.tokenizer_core import Token, TokenType

from sqlglot_experiments.statement_classification import is_replace_statement
from sqlglot_experiments.statement_dialect_policy import (
    MERGE_DIALECTS,
    REPLACE_DIALECTS,
)

_MYSQL_REPLACE_OPTIMIZER_HINT = re.compile(
    r"\A(?:\s|--[^\n]*(?:\n|\Z)|\#[^\n]*(?:\n|\Z)|/\*(?!\+)[\s\S]*?\*/)*"
    r"REPLACE\s*/\*\+",
    re.IGNORECASE,
)


class MySQLReplaceOptimizerHintError(TokenError):
    """A REPLACE optimizer hint would lose its required SQL position."""


class _ReplaceParserMixin:
    def _parse_replace_statement(self: t.Any) -> exp.Insert:
        statement = self._parse_insert()
        if not isinstance(statement, exp.Insert):
            self.raise_error("REPLACE must contain one INSERT-family statement")
            return exp.Insert()
        if statement.args.get("alternative"):
            self.raise_error("REPLACE cannot contain an OR alternative")
        statement.set("alternative", "REPLACE")
        return statement


class _SQLiteParserMixin(_ReplaceParserMixin):
    def _parse_replace_statement(self: t.Any) -> exp.Insert:
        if not self._match(TokenType.INTO, advance=False):
            self.raise_error("SQLite REPLACE requires INTO")
        depth = 0
        has_top_level_set = False
        for token in self._tokens[self._index :]:
            if token.token_type is TokenType.L_PAREN:
                depth += 1
            elif token.token_type is TokenType.R_PAREN:
                depth = max(0, depth - 1)
            elif depth == 0 and token.token_type is TokenType.SET:
                has_top_level_set = True
                break
        statement = super()._parse_replace_statement()
        if has_top_level_set and isinstance(statement.expression, exp.Values):
            statement.meta["invalid_sqlite_replace_set"] = True
        return statement

    def _parse_value(self: t.Any, values: bool = True) -> exp.Tuple | None:
        if self._match_text_seq("ROW", advance=False):
            self.raise_error("SQLite VALUES does not support ROW constructors")
        return t.cast(t.Any, super())._parse_value(values=values)


class _MySQLParserMixin(_ReplaceParserMixin):
    def _parse_replace_statement(self: t.Any) -> exp.Insert:
        modifier: str | None = None
        if self._match_texts(("LOW_PRIORITY", "DELAYED")):
            modifier = self._prev.text.upper()
        depth = 0
        has_top_level_set = False
        for token in self._tokens[self._index :]:
            if token.token_type is TokenType.L_PAREN:
                depth += 1
            elif token.token_type is TokenType.R_PAREN:
                depth = max(0, depth - 1)
            elif depth == 0 and token.token_type is TokenType.SET:
                has_top_level_set = True
                break
        statement = super()._parse_replace_statement()
        if modifier:
            statement.meta["replace_modifier"] = modifier
        if has_top_level_set and isinstance(statement.expression, exp.Values):
            statement.meta["replace_set"] = True
            if statement.expression.args.get("alias"):
                statement.meta["invalid_mysql_replace_set"] = True
        return statement

    def _parse_derived_table_values(self: t.Any) -> exp.Values | None:
        if not self._match_text_seq("VALUE"):
            return t.cast(t.Any, super())._parse_derived_table_values()
        return self.expression(
            exp.Values(expressions=self._parse_csv(self._parse_value))
        )

    def _parse_value(self: t.Any, values: bool = True) -> exp.Tuple | None:
        self._match_text_seq("ROW")
        return t.cast(t.Any, super())._parse_value(values=values)


class _MergeWhenParserMixin:
    def _parse_merge_insert_extension(self: t.Any) -> exp.Expr | None:
        if self._match_text_seq("DEFAULT", "VALUES"):
            statement = self.expression(exp.Insert(default=True))
            statement.meta["merge_syntax"] = "merge_default_values"
            return statement
        return None

    def _parse_merge_update_extension(self: t.Any) -> exp.Expr | None:
        return None

    def _parse_when_matched(self: t.Any) -> exp.Whens:
        whens: list[exp.Expr] = []
        while self._match(TokenType.WHEN):
            matched = not self._match(TokenType.NOT)
            if not self._match_text_seq("MATCHED"):
                self.raise_error("Expected MATCHED in MERGE action")
            by_target = self._match_text_seq("BY", "TARGET")
            source = False if by_target else self._match_text_seq("BY", "SOURCE")
            condition = self._parse_disjunction() if self._match(TokenType.AND) else None
            if not self._match(TokenType.THEN):
                self.raise_error("Expected THEN in MERGE action")

            then: exp.Expr | None
            if self._match(TokenType.INSERT):
                then = self._parse_merge_insert_extension()
                if then is None:
                    star = self._parse_star()
                    if star:
                        then = self.expression(exp.Insert(this=star))
                    else:
                        then = self.expression(
                            exp.Insert(
                                this=(
                                    exp.var("ROW")
                                    if self._match_text_seq("ROW")
                                    else self._parse_value(values=False)
                                ),
                                expression=(
                                    self._match_text_seq("VALUES")
                                    and self._parse_value()
                                ),
                                where=self._parse_where(),
                            )
                        )
            elif self._match(TokenType.UPDATE):
                then = self._parse_merge_update_extension()
                if then is None:
                    expressions = self._parse_star()
                    if expressions:
                        then = self.expression(exp.Update(expressions=expressions))
                    else:
                        then = self.expression(
                            exp.Update(
                                expressions=(
                                    self._match(TokenType.SET)
                                    and self._parse_csv(self._parse_equality)
                                ),
                                where=self._parse_where(),
                            )
                        )
            elif self._match(TokenType.DELETE):
                then = self.expression(exp.Var(this=self._prev.text))
            else:
                then = self._parse_var_from_options(self.CONFLICT_ACTIONS)

            when = self.expression(
                exp.When(
                    matched=matched,
                    source=source,
                    condition=condition,
                    then=then,
                )
            )
            if by_target:
                when.meta["merge_by_target"] = True
            whens.append(when)
        return self.expression(exp.Whens(expressions=whens))


class _DuckDBMergeParserMixin(_MergeWhenParserMixin):
    def _parse_merge_insert_extension(self: t.Any) -> exp.Expr | None:
        if not self._match_text_seq("BY", "NAME"):
            return super()._parse_merge_insert_extension()
        statement = self.expression(exp.Insert(this=exp.Star(), by_name=True))
        statement.meta["merge_syntax"] = "duckdb_insert_by_name"
        return statement


class _SnowflakeMergeParserMixin(_MergeWhenParserMixin):
    def _parse_merge_insert_extension(self: t.Any) -> exp.Expr | None:
        if not self._match_text_seq("ALL", "BY", "NAME"):
            return super()._parse_merge_insert_extension()
        statement = self.expression(exp.Insert(this=exp.Star(), by_name=True))
        statement.meta["merge_syntax"] = "snowflake_all_by_name"
        return statement

    def _parse_merge_update_extension(self: t.Any) -> exp.Expr | None:
        if not self._match_text_seq("ALL", "BY", "NAME"):
            return None
        statement = self.expression(exp.Update(expressions=exp.Star()))
        statement.meta["merge_syntax"] = "snowflake_all_by_name"
        return statement


class _SourceSQLite(SQLite):
    class Tokenizer(SQLite.Tokenizer):
        KEYWORDS: t.ClassVar = {
            **SQLite.Tokenizer.KEYWORDS,
            "BLOB": TokenType.BLOB,
        }
        COMMANDS = SQLite.Tokenizer.COMMANDS - {TokenType.REPLACE}

    class Parser(_SQLiteParserMixin, SQLite.Parser):
        STATEMENT_PARSERS: t.ClassVar = {
            **SQLite.Parser.STATEMENT_PARSERS,
            TokenType.REPLACE: lambda self: self._parse_replace_statement(),
        }


class _PreparedMySQLGenerator(MySQLGenerator):
    def insert_sql(self, expression: exp.Insert) -> str:
        if not is_replace_statement(expression):
            return super().insert_sql(expression)
        if expression.meta.get("replace_set"):
            return self._replace_set_sql(expression)

        statement = expression.copy()
        root_with = self.sql(statement, "with_")
        statement.set("with_", None)
        statement.set("alternative", None)
        insert_sql = super().insert_sql(statement)
        if root_with:
            if not isinstance(statement.expression, exp.Query):
                raise UnsupportedError(
                    "MySQL REPLACE can place WITH only on its SELECT source"
                )
            source_sql = self.sql(statement, "expression")
            if not source_sql or source_sql not in insert_sql:
                raise UnsupportedError(
                    "MySQL REPLACE can place WITH only on its SELECT source"
                )
            insert_sql = insert_sql.replace(
                source_sql,
                f"{root_with} {source_sql}",
                1,
            )
        if not insert_sql.startswith("INSERT"):
            raise UnsupportedError("cannot render the MySQL REPLACE statement")

        modifier = expression.meta.get("replace_modifier")
        replace = f"REPLACE {modifier}" if modifier else "REPLACE"
        return f"{replace}{insert_sql[6:]}"

    def _replace_set_sql(self, expression: exp.Insert) -> str:
        target = expression.this
        values = expression.expression
        if not isinstance(target, exp.Schema) or not isinstance(values, exp.Values):
            raise UnsupportedError("cannot render the MySQL REPLACE SET statement")
        if len(values.expressions) != 1 or not isinstance(
            values.expressions[0],
            exp.Tuple,
        ):
            raise UnsupportedError("cannot render the MySQL REPLACE SET statement")

        columns = target.expressions
        row = values.expressions[0]
        if not columns or len(columns) != len(row.expressions):
            raise UnsupportedError("cannot render the MySQL REPLACE SET statement")

        modifier = expression.meta.get("replace_modifier")
        replace = f"REPLACE {modifier}" if modifier else "REPLACE"
        table_sql = self.sql(target, "this")
        partition = self.sql(expression, "partition")
        partition = f" {partition}" if partition else ""
        assignments = ", ".join(
            f"{self.sql(column)} = {self.sql(value)}"
            for column, value in zip(columns, row.expressions, strict=True)
        )
        return f"{replace} INTO {table_sql}{partition} SET {assignments}"


class _PreparedMySQL(MySQL):
    class Tokenizer(MySQL.Tokenizer):
        COMMANDS = MySQL.Tokenizer.COMMANDS - {TokenType.REPLACE}

        def tokenize(self, sql: str) -> list[Token]:
            if _MYSQL_REPLACE_OPTIMIZER_HINT.match(sql):
                raise MySQLReplaceOptimizerHintError(
                    "MySQL REPLACE optimizer hints cannot be preserved"
                )
            return super().tokenize(sql)

    class Parser(_MySQLParserMixin, MySQL.Parser):
        STATEMENT_PARSERS: t.ClassVar = {
            **MySQL.Parser.STATEMENT_PARSERS,
            TokenType.REPLACE: lambda self: self._parse_replace_statement(),
        }

    Generator = _PreparedMySQLGenerator


def _merge_when_header(generator: generator.Generator, expression: exp.When) -> str:
    matched = "MATCHED" if expression.args["matched"] else "NOT MATCHED"
    source = (
        " BY SOURCE"
        if generator.MATCHED_BY_SOURCE and expression.args.get("source")
        else ""
    )
    condition = generator.sql(expression, "condition")
    condition = f" AND {condition}" if condition else ""
    return f"WHEN {matched}{source}{condition} THEN "


class _PreparedDuckDBGenerator(DuckDBGenerator):
    def when_sql(self, expression: exp.When) -> str:
        action = expression.args.get("then")
        if (
            isinstance(action, exp.Insert)
            and action.meta.get("merge_syntax") == "duckdb_insert_by_name"
        ):
            return f"{_merge_when_header(self, expression)}INSERT BY NAME"
        return super().when_sql(expression)


class _PreparedDuckDB(DuckDB):
    class Parser(_DuckDBMergeParserMixin, DuckDB.Parser):
        pass

    Generator = _PreparedDuckDBGenerator


class _PreparedSnowflakeGenerator(SnowflakeGenerator):
    def when_sql(self, expression: exp.When) -> str:
        action = expression.args.get("then")
        if (
            isinstance(action, (exp.Insert, exp.Update))
            and action.meta.get("merge_syntax") == "snowflake_all_by_name"
        ):
            action_sql = (
                "INSERT ALL BY NAME"
                if isinstance(action, exp.Insert)
                else "UPDATE ALL BY NAME"
            )
            return f"{_merge_when_header(self, expression)}{action_sql}"
        return super().when_sql(expression)


class _PreparedSnowflake(Snowflake):
    class Parser(_SnowflakeMergeParserMixin, Snowflake.Parser):
        pass

    Generator = _PreparedSnowflakeGenerator


class _PreparedBigQuery(BigQuery):
    class Parser(_MergeWhenParserMixin, BigQuery.Parser):
        pass


class _PreparedDatabricks(Databricks):
    class Parser(_MergeWhenParserMixin, Databricks.Parser):
        pass


class _PreparedOracle(Oracle):
    class Parser(_MergeWhenParserMixin, Oracle.Parser):
        pass


class _PreparedPostgresGenerator(PostgresGenerator):
    def when_sql(self, expression: exp.When) -> str:
        action = expression.args.get("then")
        if isinstance(action, exp.Insert) and action.args.get("default"):
            return f"{_merge_when_header(self, expression)}INSERT DEFAULT VALUES"
        return super().when_sql(expression)


class _PreparedPostgres(Postgres):
    class Parser(_MergeWhenParserMixin, Postgres.Parser):
        pass

    Generator = _PreparedPostgresGenerator


class _PreparedTSQLGenerator(TSQLGenerator):
    def merge_sql(self, expression: exp.Merge) -> str:
        return f"{super().merge_sql(expression)};"


class _PreparedTSQL(TSQL):
    class Parser(_MergeWhenParserMixin, TSQL.Parser):
        pass

    Generator = _PreparedTSQLGenerator


class _ExecutionSQLiteGenerator(SQLiteGenerator):
    TYPE_MAPPING: t.ClassVar = {
        **SQLiteGenerator.TYPE_MAPPING,
        exp.DType.DECIMAL: "NUMERIC",
    }

    def not_sql(self, expression: exp.Not) -> str:
        predicate = expression.this.unnest()
        if isinstance(predicate, exp.Is):
            predicate = predicate.copy()
            predicate.set("negate", not predicate.args.get("negate"))
            return self.sql(predicate)
        return super().not_sql(expression)


class _ExecutionSQLite(SQLite):
    Generator = _ExecutionSQLiteGenerator


class _SameDialectSQLiteGenerator(_ExecutionSQLiteGenerator):
    TYPE_MAPPING: t.ClassVar = {
        **generator.Generator.TYPE_MAPPING,
        exp.DType.BLOB: "BLOB",
        exp.DType.DECIMAL: "NUMERIC",
    }

    def cast_sql(self, expression: exp.Cast, safe_prefix: str | None = None) -> str:
        return generator.Generator.cast_sql(self, expression, safe_prefix)


class _SameDialectSQLite(_SourceSQLite):
    Generator = _SameDialectSQLiteGenerator


def parsing_dialect(
    *,
    source_dialect: str,
    target_dialect: str,
) -> str | type[Dialect]:
    """Return the declared source adapter with target-relevant AST fidelity."""
    if source_dialect == "sqlite":
        return _SourceSQLite
    if source_dialect == "mysql":
        return _PreparedMySQL
    if source_dialect == "duckdb":
        return _PreparedDuckDB
    if source_dialect == "snowflake":
        return _PreparedSnowflake
    if source_dialect == "bigquery":
        return _PreparedBigQuery
    if source_dialect == "databricks":
        return _PreparedDatabricks
    if source_dialect == "oracle":
        return _PreparedOracle
    if source_dialect == "postgres":
        return _PreparedPostgres
    if source_dialect == "tsql":
        return _PreparedTSQL
    return source_dialect


def tokenize_preparation_sql(sql: str, *, dialect: str) -> list[Token]:
    """Tokenize with the same statement adapters used by preparation parsing."""
    adapted = parsing_dialect(
        source_dialect=dialect,
        target_dialect=dialect,
    )
    return Dialect.get_or_raise(adapted).tokenize(sql)


def _generation_dialect(
    *,
    source_dialect: str,
    target_dialect: str,
) -> str | type[Dialect]:
    if source_dialect == target_dialect == "sqlite":
        return _SameDialectSQLite
    if target_dialect == "sqlite":
        return _ExecutionSQLite
    if target_dialect == "mysql":
        return _PreparedMySQL
    if target_dialect == "duckdb":
        return _PreparedDuckDB
    if target_dialect == "snowflake":
        return _PreparedSnowflake
    if target_dialect == "bigquery":
        return _PreparedBigQuery
    if target_dialect == "databricks":
        return _PreparedDatabricks
    if target_dialect == "oracle":
        return _PreparedOracle
    if target_dialect == "postgres":
        return _PreparedPostgres
    if target_dialect == "tsql":
        return _PreparedTSQL
    return target_dialect


def _merge_extension_dialect(statement: exp.Expr) -> str | None:
    for node in statement.walk():
        if isinstance(node, (exp.Insert, exp.Update)):
            syntax = node.meta.get("merge_syntax")
            if syntax == "duckdb_insert_by_name":
                return "duckdb"
            if syntax == "snowflake_all_by_name":
                return "snowflake"
    return None


def _replace_nodes(statement: exp.Expr) -> list[exp.Insert]:
    return [
        node
        for node in statement.walk()
        if isinstance(node, exp.Insert) and is_replace_statement(node)
    ]


def _merge_nodes(statement: exp.Expr) -> list[exp.Merge]:
    return [node for node in statement.walk() if isinstance(node, exp.Merge)]


def _require_replace_target_compatibility(
    statements: list[exp.Insert],
    *,
    source_dialect: str,
    target_dialect: str,
) -> None:
    if target_dialect not in REPLACE_DIALECTS:
        raise UnsupportedError(
            f"{target_dialect} has no configured REPLACE statement rendering"
        )
    for statement in statements:
        if source_dialect != target_dialect and (
            statement.meta.get("replace_modifier")
            or statement.meta.get("replace_set")
            or statement.args.get("partition")
            or statement.args.get("source")
        ):
            raise UnsupportedError(
                "dialect-specific REPLACE clause cannot be rendered for the target"
            )
        if target_dialect == "mysql" and (
            statement.args.get("returning") or statement.args.get("default")
        ):
            raise UnsupportedError(
                "MySQL cannot render this REPLACE statement form"
            )


def generate_target_sql(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
    comments: bool = True,
) -> str:
    """Render through the adapters for the explicit source and target."""
    replace_statements = _replace_nodes(statement)
    if replace_statements:
        _require_replace_target_compatibility(
            replace_statements,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    merge_statements = _merge_nodes(statement)
    if merge_statements:
        if target_dialect not in MERGE_DIALECTS:
            raise UnsupportedError(
                f"{target_dialect} has no configured MERGE statement rendering"
            )
        extension_dialect = _merge_extension_dialect(statement)
        if extension_dialect and target_dialect != extension_dialect:
            raise UnsupportedError(
                "dialect-specific MERGE action cannot be rendered for the target"
            )

    dialect = _generation_dialect(
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    return statement.sql(
        dialect=dialect,
        comments=comments,
        unsupported_level=ErrorLevel.RAISE,
    )

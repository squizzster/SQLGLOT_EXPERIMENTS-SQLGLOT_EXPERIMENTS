from __future__ import annotations

import typing as t

from sqlglot import ErrorLevel, exp, generator
from sqlglot.dialects.sqlite import SQLite
from sqlglot.generators.sqlite import SQLiteGenerator
from sqlglot.tokenizer_core import TokenType


class _SourceSQLite(SQLite):
    class Tokenizer(SQLite.Tokenizer):
        KEYWORDS: t.ClassVar = {
            **SQLite.Tokenizer.KEYWORDS,
            "BLOB": TokenType.BLOB,
        }


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
) -> str | type[SQLite]:
    """Return the declared source adapter with target-relevant AST fidelity."""
    if source_dialect == "sqlite":
        return _SourceSQLite
    return source_dialect


def generate_target_sql(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
    comments: bool = True,
) -> str:
    """Render through the adapters for the explicit source and target."""
    dialect: str | type[SQLite]
    if source_dialect == target_dialect == "sqlite":
        dialect = _SameDialectSQLite
    elif target_dialect == "sqlite":
        dialect = _ExecutionSQLite
    else:
        dialect = target_dialect
    return statement.sql(
        dialect=dialect,
        comments=comments,
        unsupported_level=ErrorLevel.RAISE,
    )

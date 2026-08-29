from sqlglot_experiments.statement_api import (
    Analysis,
    Binding,
    BindingCountError,
    InputBindings,
    PreparedStatement,
    StatementPreparationError,
    StatementType,
    UnsupportedStatementError,
    prepare_statement,
)

__all__ = [
    "Analysis",
    "Binding",
    "BindingCountError",
    "InputBindings",
    "PreparedStatement",
    "StatementPreparationError",
    "StatementType",
    "UnsupportedStatementError",
    "prepare_statement",
]


def main() -> None:
    print("Run `uv run python demo/sqlite_consumer.py` for the SQLite demo.")

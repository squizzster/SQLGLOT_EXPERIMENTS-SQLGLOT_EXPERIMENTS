from sqlglot_experiments.api_envelope import (
    ApiEnvelope,
    ApiFailureEnvelope,
    ApiSuccessEnvelope,
)
from sqlglot_experiments.statement_api import (
    Analysis,
    Binding,
    InputBindings,
    PreparationResult,
    PreparedStatement,
    StatementType,
    prepare_statement,
)

__all__ = [
    "Analysis",
    "ApiEnvelope",
    "ApiFailureEnvelope",
    "ApiSuccessEnvelope",
    "Binding",
    "InputBindings",
    "PreparationResult",
    "PreparedStatement",
    "StatementType",
    "prepare_statement",
]


def main() -> None:
    print("Run `uv run python demo/sqlite_consumer.py` for the SQLite demo.")

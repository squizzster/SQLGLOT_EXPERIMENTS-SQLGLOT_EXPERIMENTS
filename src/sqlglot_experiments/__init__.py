from sqlglot_experiments.api_envelope import (
    ApiEnvelope,
    ApiFailureEnvelope,
    ApiSuccessEnvelope,
)
from sqlglot_experiments.statement_api import (
    AcceptedStatement,
    Analysis,
    Binding,
    InputBindings,
    InsertAnalysis,
    InsertTarget,
    PreparationFailure,
    PreparationResult,
    PreparedStatement,
    StatementType,
    prepare_statement,
    set_lru_cache_size,
)
from sqlglot_experiments.where_fields import WhereField

__all__ = [
    "AcceptedStatement",
    "Analysis",
    "ApiEnvelope",
    "ApiFailureEnvelope",
    "ApiSuccessEnvelope",
    "Binding",
    "InputBindings",
    "InsertAnalysis",
    "InsertTarget",
    "PreparationFailure",
    "PreparationResult",
    "PreparedStatement",
    "StatementType",
    "WhereField",
    "prepare_statement",
    "set_lru_cache_size",
]


def main() -> None:
    print("Run `uv run python demo/sqlite_consumer.py` for the SQLite demo.")

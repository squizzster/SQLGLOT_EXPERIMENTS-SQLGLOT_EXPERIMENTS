from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from typing import Literal, TypedDict, cast, overload

from sqlglot import Dialect, exp, parse
from sqlglot.errors import ParseError, SqlglotError, TokenError, UnsupportedError
from sqlglot.tokenizer_core import TokenType

from sqlglot_experiments.api_envelope import (
    ApiEnvelope,
    ApiFailureEnvelope,
    ApiSuccessEnvelope,
    failure_envelope,
    success_envelope,
)
from sqlglot_experiments.dialect_adapters import (
    MySQLReplaceOptimizerHintError,
    generate_target_sql,
    parsing_dialect,
    tokenize_preparation_sql,
)
from sqlglot_experiments.source_parameters import (
    Binding,
    InputBindings,
    ParameterOccurrence,
    ParameterPlan,
    ParameterPlanningError,
    plan_source_parameters,
    source_parameter_structure,
)
from sqlglot_experiments.statement_classification import (
    StatementClassificationError,
    StatementType,
    extended_statement_type,
    is_replace_statement,
    require_extended_statement_type,
    statement_semantic_signature,
)
from sqlglot_experiments.statement_fingerprinting import (
    FingerprintingError,
    fingerprint_statement,
)
from sqlglot_experiments.where_fields import (
    WhereField,
    extract_where_fields,
)

_DEFAULT_LRU_CACHE_SIZE = 128
_GENERIC_FINGERPRINT_ALGORITHM = "sqlglot-experiments/generic-source-fingerprint/v1"


class StatementTarget(TypedDict):
    catalog: str | None
    schema: str | None
    table: str


class InsertAnalysis(TypedDict):
    target: StatementTarget
    supplied_columns: list[str]
    plain_values_binding_rows: list[list[int]] | None


class ExistingRowMutationEffect(TypedDict):
    target: StatementTarget
    updated_columns: list[str] | None
    deletes_rows: bool


class ExistingRowMutationAnalysis(TypedDict):
    effects: list[ExistingRowMutationEffect]
    evidence_complete: bool


class DirectWriteAnalysis(TypedDict):
    targets: list[StatementTarget]
    evidence_complete: bool


class Analysis(TypedDict):
    hardcoded_value_count: int
    hardcoded_field_count: int
    returns_rows: bool
    insert: InsertAnalysis | None
    direct_writes: DirectWriteAnalysis
    existing_row_mutations: ExistingRowMutationAnalysis


class PreparedStatement(ApiSuccessEnvelope):
    envelope_type: Literal["prepared"]
    sql_fingerprint: str
    dialect: list[str]
    statement_type: StatementType
    sql: str
    bindings: list[Binding]
    where_fields: list[WhereField]
    analysis: Analysis


class AcceptedStatement(ApiSuccessEnvelope):
    envelope_type: Literal["accepted"]
    sql_fingerprint: str


class PreparationFailure(ApiFailureEnvelope):
    envelope_type: Literal["failure"]


PreparationResult = PreparedStatement | AcceptedStatement | PreparationFailure


class StatementPreparationError(ValueError):
    """The SQL cannot produce one complete execution package."""


class BindingCountError(StatementPreparationError):
    """Caller bindings cannot resolve every required source parameter slot."""


class LruCacheConfigurationError(ValueError):
    """The requested process-local LRU configuration is invalid."""


class _Candidate(TypedDict):
    node: exp.Expr
    value: Binding
    field_keys: set[tuple[str, ...]]


@dataclass(frozen=True)
class _CallerBindingReference:
    slot_number: int


@dataclass(frozen=True)
class _PreparedStructure:
    warnings: bool
    msg: str
    sql_fingerprint: str
    dialect: tuple[str, str]
    statement_type: StatementType
    sql: str
    binding_route: tuple[Binding, ...]
    where_fields: tuple[WhereField, ...]
    hardcoded_value_count: int
    hardcoded_field_count: int
    returns_rows: bool
    insert_analysis: _InsertAnalysis | None
    direct_write_analysis: _DirectWriteAnalysis
    existing_row_mutation_analysis: _ExistingRowMutationAnalysis


@dataclass(frozen=True)
class _StatementTarget:
    catalog: str | None
    schema: str | None
    table: str


@dataclass(frozen=True)
class _InsertAnalysis:
    target: _StatementTarget
    supplied_columns: tuple[str, ...]
    plain_values_binding_rows: tuple[tuple[int, ...], ...] | None


@dataclass(frozen=True)
class _ExistingRowMutationEffect:
    target: _StatementTarget
    updated_columns: tuple[str, ...] | None
    deletes_rows: bool


@dataclass(frozen=True)
class _ExistingRowMutationAnalysis:
    effects: tuple[_ExistingRowMutationEffect, ...]
    evidence_complete: bool


@dataclass(frozen=True)
class _DirectWriteAnalysis:
    targets: tuple[_StatementTarget, ...]
    evidence_complete: bool


_DIRECT_PREDICATES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
)


@overload
def prepare_statement(
    sql: str,
    *,
    bindings: InputBindings | None = None,
    source_dialect: str,
    target_dialect: str,
) -> PreparationResult: ...


@overload
def prepare_statement(
    sql: str | None = None,
    *,
    bindings: InputBindings | None = None,
    source_dialect: str | None = None,
    target_dialect: str | None = None,
) -> PreparationResult: ...


def prepare_statement(*args: object, **kwargs: object) -> PreparationResult:
    """Return the fixed public envelope for every recognised call outcome."""
    try:
        sql, bindings, source_dialect, target_dialect = _validate_public_call(
            args,
            kwargs,
        )
        source_dialect = _require_dialect(source_dialect, role="source")
        target_dialect = _require_dialect(target_dialect, role="target")
        source_ast = _parse_source_statement(
            sql,
            source_dialect=source_dialect,
        )
        if _extended_statement_type(source_ast) is None:
            return _accept_statement(
                sql,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
        return _prepare_statement(
            sql,
            bindings=bindings,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    except (
        StatementPreparationError,
        StatementClassificationError,
        FingerprintingError,
        SqlglotError,
    ) as error:
        return _preparation_failure(_failure_reason(error))


@overload
def set_lru_cache_size(size: int) -> ApiEnvelope: ...


@overload
def set_lru_cache_size(size: int | None = None) -> ApiEnvelope: ...


def set_lru_cache_size(*args: object, **kwargs: object) -> ApiEnvelope:
    """Set and empty this process's prepared-statement structure LRU."""
    try:
        size = _validate_lru_cache_call(args, kwargs)
    except LruCacheConfigurationError as error:
        return failure_envelope(str(error))

    _replace_statement_structure_cache(size)
    return success_envelope()


def _validate_lru_cache_call(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> int:
    if len(args) > 1:
        raise LruCacheConfigurationError("only size may be passed positionally")

    unexpected_arguments = sorted(set(kwargs) - {"size"})
    if unexpected_arguments:
        label = "argument" if len(unexpected_arguments) == 1 else "arguments"
        names = ", ".join(unexpected_arguments)
        raise LruCacheConfigurationError(f"unexpected {label}: {names}")

    if args and "size" in kwargs:
        raise LruCacheConfigurationError("size was provided more than once")

    size = args[0] if args else kwargs.get("size")
    if size is None:
        raise LruCacheConfigurationError("lru cache size is required")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise LruCacheConfigurationError("lru cache size must be a positive integer")
    return size


def _validate_public_call(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[str, InputBindings | None, str, str]:
    if len(args) > 1:
        raise StatementPreparationError("only sql may be passed positionally")

    supported_arguments = {"sql", "bindings", "source_dialect", "target_dialect"}
    unexpected_arguments = sorted(set(kwargs) - supported_arguments)
    if unexpected_arguments:
        label = "argument" if len(unexpected_arguments) == 1 else "arguments"
        names = ", ".join(unexpected_arguments)
        raise StatementPreparationError(f"unexpected {label}: {names}")

    if args and "sql" in kwargs:
        raise StatementPreparationError("sql was provided more than once")

    sql = args[0] if args else kwargs.get("sql")
    bindings = kwargs.get("bindings")
    source_dialect = kwargs.get("source_dialect")
    target_dialect = kwargs.get("target_dialect")

    if sql is None:
        raise StatementPreparationError("sql is required")
    if not isinstance(sql, str):
        raise StatementPreparationError("sql must be a string")
    if not sql.strip():
        raise StatementPreparationError("sql is required")

    source_dialect = _require_public_dialect(source_dialect, role="source")
    target_dialect = _require_public_dialect(target_dialect, role="target")

    if bindings is not None and (
        isinstance(bindings, (str, bytes, bytearray, memoryview))
        or not isinstance(bindings, (Mapping, Sequence))
    ):
        raise BindingCountError("bindings must be a sequence or mapping of values")

    return (
        sql,
        cast(InputBindings | None, bindings),
        source_dialect,
        target_dialect,
    )


def _parse_source_statement(
    sql: str,
    *,
    source_dialect: str,
) -> exp.Expr:
    """Parse once for routing without resolving or transforming bindings."""
    try:
        _, occurrences = source_parameter_structure(
            sql,
            source_dialect=source_dialect,
            target_dialect=source_dialect,
        )
    except ParameterPlanningError as error:
        raise BindingCountError(str(error)) from error

    tagged_sql = _tag_parameter_occurrences(sql, occurrences=occurrences)
    return _parse_single_statement(
        tagged_sql,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )


def _tag_parameter_occurrences(
    sql: str,
    *,
    occurrences: tuple[ParameterOccurrence, ...],
) -> str:
    """Give source placeholders parser-safe identities for AST routing only."""
    marker_prefix = _unused_marker_prefix(sql, kind="routing")
    parts: list[str] = []
    cursor = 0
    for index, occurrence in enumerate(occurrences):
        parts.extend((sql[cursor : occurrence.start], f":{marker_prefix}{index}"))
        cursor = occurrence.end + 1
    parts.append(sql[cursor:])
    return "".join(parts)


def _accept_statement(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> AcceptedStatement:
    payload = json.dumps(
        {
            "algorithm": _GENERIC_FINGERPRINT_ALGORITHM,
            "source_dialect": source_dialect,
            "sql": sql,
            "target_dialect": target_dialect,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    status = success_envelope()
    return {
        **status,
        "envelope_type": "accepted",
        "sql_fingerprint": sha256(payload.encode()).hexdigest(),
    }


def _preparation_failure(reason: str) -> PreparationFailure:
    return {
        **failure_envelope(reason),
        "envelope_type": "failure",
    }


def _require_public_dialect(dialect: object, *, role: str) -> str:
    if dialect is None:
        raise StatementPreparationError(f"{role} dialect is required")
    if not isinstance(dialect, str):
        raise StatementPreparationError(f"{role} dialect must be a string")
    if not dialect.strip():
        raise StatementPreparationError(f"{role} dialect is required")
    return dialect


def _prepare_statement(
    sql: str,
    *,
    bindings: InputBindings | None,
    source_dialect: str,
    target_dialect: str,
) -> PreparedStatement:
    """Return a fresh envelope from a cached or newly built SQL structure."""
    source_dialect = _require_dialect(source_dialect, role="source")
    target_dialect = _require_dialect(target_dialect, role="target")
    try:
        parameter_plan = plan_source_parameters(
            sql,
            bindings=bindings,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    except ParameterPlanningError as error:
        raise BindingCountError(str(error)) from error

    structure = _prepare_statement_structure(
        sql,
        source_dialect,
        target_dialect,
        tuple(slot.bind_name or f"#{slot.number}" for slot in parameter_plan.slots),
    )
    return _materialize_prepared_statement(
        structure,
        parameter_plan=parameter_plan,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


def _build_statement_structure(
    sql: str,
    source_dialect: str,
    target_dialect: str,
    binding_names: tuple[str, ...],
) -> _PreparedStructure:
    """Build one immutable, process-local structure without caller values."""
    slots, occurrences = source_parameter_structure(
        sql,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    observed_binding_names = tuple(
        slot.bind_name or f"#{slot.number}" for slot in slots
    )
    if observed_binding_names != binding_names:
        raise RuntimeError("binding structure changed during statement preparation")

    parameter_plan = ParameterPlan(
        slots=slots,
        occurrences=occurrences,
        occurrence_values=tuple(
            _CallerBindingReference(occurrence.slot_number)
            for occurrence in occurrences
        ),
    )
    source_sql, marker_values = _tag_source_placeholders(
        sql,
        parameter_plan=parameter_plan,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

    source_ast = _parse_single_statement(
        source_sql,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    statement_type = _statement_type(
        source_ast,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    where_fields = extract_where_fields(
        source_ast,
        source_dialect=source_dialect,
    )
    _require_owned_placeholders(
        source_ast,
        marker_values=marker_values,
        source_dialect=source_dialect,
        target_dialect=source_dialect,
    )
    prepared_ast, marker_values, field_keys, hardcoded_value_count = (
        _mark_hardcoded_values(
            source_ast,
            marker_values=marker_values,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
    )
    binding_markers = _binding_markers_in_target_order(
        prepared_ast,
        marker_values=marker_values,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    merged_bindings = [marker_values[marker] for marker in binding_markers]
    plain_values_binding_rows = _plain_insert_value_binding_rows(
        prepared_ast,
        statement_type=statement_type,
        binding_markers=binding_markers,
        source_uses_values_syntax=_source_uses_insert_values_syntax(
            sql,
            source_dialect=source_dialect,
        ),
    )
    _make_placeholders_anonymous(
        prepared_ast,
        marker_values=marker_values,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    _require_complete_ast(
        prepared_ast,
        bindings=list(marker_values.values()),
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    target_sql = _generate_sql(
        prepared_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    target_ast = _parse_single_statement(
        target_sql,
        source_dialect=target_dialect,
        target_dialect=target_dialect,
    )
    target_statement_type = _statement_type(
        target_ast,
        source_dialect=target_dialect,
        target_dialect=target_dialect,
    )
    if target_statement_type != statement_type:
        raise StatementPreparationError(
            "target rendering changed the SQL statement type"
        )
    if statement_semantic_signature(target_ast) != statement_semantic_signature(
        prepared_ast
    ):
        raise StatementPreparationError(
            "target rendering changed the SQL statement semantics"
        )
    _require_complete_ast(
        target_ast,
        bindings=merged_bindings,
        source_dialect=target_dialect,
        target_dialect=target_dialect,
    )
    insert_analysis = _extract_insert_analysis(
        target_ast,
        statement_type=target_statement_type,
        plain_values_binding_rows=plain_values_binding_rows,
    )
    existing_row_mutation_analysis = _extract_existing_row_mutation_analysis(target_ast)
    direct_write_analysis = _extract_direct_write_analysis(
        target_ast,
        existing_row_mutation_analysis=existing_row_mutation_analysis,
    )
    sql_fingerprint = fingerprint_statement(
        sql,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

    status = success_envelope(
        warning=(
            _hardcoded_warning(hardcoded_value_count)
            if hardcoded_value_count > 0
            else None
        )
    )
    return _PreparedStructure(
        warnings=status["warnings"],
        msg=status["msg"],
        sql_fingerprint=sql_fingerprint,
        dialect=(source_dialect, target_dialect),
        statement_type=statement_type,
        sql=target_sql,
        binding_route=tuple(merged_bindings),
        where_fields=tuple(where_fields),
        hardcoded_value_count=hardcoded_value_count,
        hardcoded_field_count=len(field_keys),
        returns_rows=(
            isinstance(target_ast, exp.Query) or bool(target_ast.args.get("returning"))
        ),
        insert_analysis=insert_analysis,
        direct_write_analysis=direct_write_analysis,
        existing_row_mutation_analysis=existing_row_mutation_analysis,
    )


_prepare_statement_structure = lru_cache(maxsize=_DEFAULT_LRU_CACHE_SIZE)(
    _build_statement_structure
)


def _replace_statement_structure_cache(size: int) -> None:
    global _prepare_statement_structure

    previous_cache = _prepare_statement_structure
    _prepare_statement_structure = lru_cache(maxsize=size)(_build_statement_structure)
    previous_cache.cache_clear()


def _materialize_prepared_statement(
    structure: _PreparedStructure,
    *,
    parameter_plan: ParameterPlan,
    source_dialect: str,
    target_dialect: str,
) -> PreparedStatement:
    slot_values: dict[int, Binding] = {}
    for occurrence, value in zip(
        parameter_plan.occurrences,
        parameter_plan.occurrence_values,
        strict=True,
    ):
        slot_values.setdefault(occurrence.slot_number, value)

    converted_slot_values: dict[int, Binding] = {}
    bindings: list[Binding] = []
    for item in structure.binding_route:
        if not isinstance(item, _CallerBindingReference):
            bindings.append(item)
            continue

        slot_number = item.slot_number
        if slot_number not in slot_values:
            raise RuntimeError("cached binding route references an absent source slot")
        if slot_number not in converted_slot_values:
            converted_slot_values[slot_number] = _target_binding_value(
                slot_values[slot_number],
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
        bindings.append(converted_slot_values[slot_number])

    return {
        "success": True,
        "warnings": structure.warnings,
        "msg": structure.msg,
        "envelope_type": "prepared",
        "sql_fingerprint": structure.sql_fingerprint,
        "dialect": list(structure.dialect),
        "statement_type": structure.statement_type,
        "sql": structure.sql,
        "bindings": bindings,
        "where_fields": list(structure.where_fields),
        "analysis": {
            "hardcoded_value_count": structure.hardcoded_value_count,
            "hardcoded_field_count": structure.hardcoded_field_count,
            "returns_rows": structure.returns_rows,
            "insert": _materialize_insert_analysis(structure.insert_analysis),
            "direct_writes": _materialize_direct_write_analysis(
                structure.direct_write_analysis
            ),
            "existing_row_mutations": _materialize_existing_row_mutation_analysis(
                structure.existing_row_mutation_analysis
            ),
        },
    }


def _extract_insert_analysis(
    statement: exp.Expr,
    *,
    statement_type: StatementType,
    plain_values_binding_rows: tuple[tuple[int, ...], ...] | None,
) -> _InsertAnalysis | None:
    """Extract only target-owned facts proven by one prepared INSERT AST."""

    if statement_type != "INSERT" or not isinstance(statement, exp.Insert):
        return None

    target_expression = statement.this
    if isinstance(target_expression, exp.Schema):
        table_expression = target_expression.this
        column_expressions = target_expression.expressions
    else:
        table_expression = target_expression
        column_expressions = []

    if not isinstance(table_expression, exp.Table) or not table_expression.name:
        return None
    if not column_expressions:
        target_alias = table_expression.args.get("alias")
        if isinstance(target_alias, exp.TableAlias):
            column_expressions = target_alias.columns
    if any(
        not isinstance(column, exp.Identifier) or not column.name
        for column in column_expressions
    ):
        return None

    return _InsertAnalysis(
        target=_StatementTarget(
            catalog=table_expression.catalog or None,
            schema=table_expression.db or None,
            table=table_expression.name,
        ),
        supplied_columns=tuple(column.name for column in column_expressions),
        plain_values_binding_rows=plain_values_binding_rows,
    )


def _materialize_insert_analysis(
    analysis: _InsertAnalysis | None,
) -> InsertAnalysis | None:
    if analysis is None:
        return None
    return {
        "target": {
            "catalog": analysis.target.catalog,
            "schema": analysis.target.schema,
            "table": analysis.target.table,
        },
        "supplied_columns": list(analysis.supplied_columns),
        "plain_values_binding_rows": (
            [list(row) for row in analysis.plain_values_binding_rows]
            if analysis.plain_values_binding_rows is not None
            else None
        ),
    }


def _extract_existing_row_mutation_analysis(
    statement: exp.Expr,
) -> _ExistingRowMutationAnalysis:
    """Extract every direct AST-visible UPDATE, DELETE, or replacement effect."""

    raw_effects: list[_ExistingRowMutationEffect] = []
    evidence_complete = True
    for node in statement.walk():
        if isinstance(node, exp.Update):
            if _is_merge_action(node):
                continue
            effects, complete = _standalone_update_effects(node)
        elif isinstance(node, exp.Delete):
            if _is_merge_action(node):
                continue
            effects, complete = _standalone_delete_effects(node)
        elif isinstance(node, exp.Merge):
            effects, complete = _merge_existing_row_effects(node)
        elif isinstance(node, exp.Insert):
            if _is_merge_action(node):
                continue
            effects, complete = _insert_existing_row_effects(node)
        else:
            continue
        raw_effects.extend(effects)
        evidence_complete = evidence_complete and complete

    return _ExistingRowMutationAnalysis(
        effects=_merge_existing_row_effects_by_target(raw_effects),
        evidence_complete=evidence_complete,
    )


def _extract_direct_write_analysis(
    statement: exp.Expr,
    *,
    existing_row_mutation_analysis: _ExistingRowMutationAnalysis,
) -> _DirectWriteAnalysis:
    """Extract every direct AST-visible relation receiving a write."""

    targets = [effect.target for effect in existing_row_mutation_analysis.effects]
    evidence_complete = existing_row_mutation_analysis.evidence_complete
    for node in statement.walk():
        target: _StatementTarget | None = None
        if isinstance(node, exp.Insert) and not _is_merge_action(node):
            target = _statement_target(_insert_target_table(node))
        elif isinstance(node, exp.Merge):
            target = _statement_target(node.this)
        else:
            continue
        if target is None:
            evidence_complete = False
        elif target not in targets:
            targets.append(target)
    return _DirectWriteAnalysis(
        targets=tuple(targets),
        evidence_complete=evidence_complete,
    )


def _standalone_update_effects(
    statement: exp.Update,
) -> tuple[tuple[_ExistingRowMutationEffect, ...], bool]:
    base_table, targets_by_name = _update_target_tables(statement)
    if base_table is None:
        return (), False

    candidate_targets = tuple(dict.fromkeys(targets_by_name.values()))
    columns_by_target: dict[_StatementTarget, list[str] | None] = {}
    expressions = statement.args.get("expressions")
    if not isinstance(expressions, list) or not expressions:
        return (
            tuple(
                _ExistingRowMutationEffect(target, None, False)
                for target in candidate_targets
            ),
            False,
        )

    evidence_complete = True
    for expression in expressions:
        if not isinstance(expression, exp.EQ):
            evidence_complete = False
            for target in candidate_targets:
                columns_by_target[target] = None
            continue
        columns = _assignment_target_columns(expression.this)
        if columns is None:
            evidence_complete = False
            for target in candidate_targets:
                columns_by_target[target] = None
            continue
        for column in columns:
            target = (
                targets_by_name.get(column.table)
                if column.table
                else _statement_target(base_table)
            )
            if target is None:
                evidence_complete = False
                for candidate in candidate_targets:
                    columns_by_target[candidate] = None
                continue
            _append_updated_column(columns_by_target, target, column.name)

    return (
        tuple(
            _ExistingRowMutationEffect(
                target=target,
                updated_columns=(tuple(columns) if columns is not None else None),
                deletes_rows=False,
            )
            for target, columns in columns_by_target.items()
        ),
        evidence_complete,
    )


def _standalone_delete_effects(
    statement: exp.Delete,
) -> tuple[tuple[_ExistingRowMutationEffect, ...], bool]:
    base_table, targets_by_name = _delete_target_tables(statement)
    if base_table is None:
        return (), False

    raw_targets = statement.args.get("tables")
    if not isinstance(raw_targets, list) or not raw_targets:
        target = _statement_target(base_table)
        if target is None:
            return (), False
        return (_ExistingRowMutationEffect(target, (), True),), True

    effects: list[_ExistingRowMutationEffect] = []
    evidence_complete = True
    for raw_target in raw_targets:
        target: _StatementTarget | None = None
        if isinstance(raw_target, exp.Table):
            target = targets_by_name.get(raw_target.name)
            if target is None:
                target = _statement_target(raw_target)
        if target is None:
            evidence_complete = False
            continue
        effects.append(_ExistingRowMutationEffect(target, (), True))
    return tuple(effects), evidence_complete


def _insert_existing_row_effects(
    statement: exp.Insert,
) -> tuple[tuple[_ExistingRowMutationEffect, ...], bool]:
    deletes_rows = is_replace_statement(statement) or bool(
        statement.args.get("overwrite")
    )
    updated_columns: tuple[str, ...] | None = ()
    conflict = statement.args.get("conflict")
    if isinstance(conflict, exp.OnConflict):
        action = conflict.args.get("action")
        action_name = action.name.upper() if isinstance(action, exp.Var) else ""
        if "UPDATE" in action_name:
            updated_columns = _assignment_column_names(conflict.args.get("expressions"))
        elif action_name not in {"", "DO NOTHING", "NOTHING"}:
            return (), False

    if not deletes_rows and updated_columns == ():
        return (), True
    target_table = _insert_target_table(statement)
    target = _statement_target(target_table)
    if target is None:
        return (), False
    return (
        _ExistingRowMutationEffect(
            target=target,
            updated_columns=updated_columns,
            deletes_rows=deletes_rows,
        ),
    ), updated_columns is not None


def _merge_existing_row_effects(
    statement: exp.Merge,
) -> tuple[tuple[_ExistingRowMutationEffect, ...], bool]:
    updated_columns: list[str] | None = []
    deletes_rows = False
    evidence_complete = True
    whens = statement.args.get("whens")
    if not isinstance(whens, exp.Whens):
        return (), False

    for when in whens.expressions:
        if not isinstance(when, exp.When):
            evidence_complete = False
            continue
        action = when.args.get("then")
        if isinstance(action, exp.Update):
            action_columns = _assignment_column_names(action.args.get("expressions"))
            if action_columns is None:
                updated_columns = None
                evidence_complete = False
            elif updated_columns is not None:
                updated_columns.extend(action_columns)
        elif isinstance(action, exp.Delete) or (
            isinstance(action, exp.Var) and action.name.upper() == "DELETE"
        ):
            deletes_rows = True

    if not deletes_rows and updated_columns == []:
        return (), evidence_complete
    target = _statement_target(statement.this)
    if target is None:
        return (), False
    return (
        _ExistingRowMutationEffect(
            target=target,
            updated_columns=(
                tuple(updated_columns) if updated_columns is not None else None
            ),
            deletes_rows=deletes_rows,
        ),
    ), evidence_complete


def _update_target_tables(
    statement: exp.Update,
) -> tuple[exp.Table | None, dict[str, _StatementTarget]]:
    target = statement.this
    if not isinstance(target, exp.Table):
        return None, {}
    return target, _direct_table_targets(target)


def _delete_target_tables(
    statement: exp.Delete,
) -> tuple[exp.Table | None, dict[str, _StatementTarget]]:
    target = statement.this
    if not isinstance(target, exp.Table):
        return None, {}
    return target, _direct_table_targets(target)


def _direct_table_targets(base_table: exp.Table) -> dict[str, _StatementTarget]:
    tables = [base_table]
    joins = base_table.args.get("joins")
    if isinstance(joins, list):
        tables.extend(
            join.this
            for join in joins
            if isinstance(join, exp.Join) and isinstance(join.this, exp.Table)
        )

    targets: dict[str, _StatementTarget] = {}
    for table in tables:
        target = _statement_target(table)
        if target is None:
            continue
        targets.setdefault(table.name, target)
        if table.alias_or_name:
            targets.setdefault(table.alias_or_name, target)
    return targets


def _insert_target_table(statement: exp.Insert) -> exp.Table | None:
    target = statement.this
    if isinstance(target, exp.Schema):
        target = target.this
    return target if isinstance(target, exp.Table) else None


def _statement_target(expression: object) -> _StatementTarget | None:
    if not isinstance(expression, exp.Table) or not expression.name:
        return None
    return _StatementTarget(
        catalog=expression.catalog or None,
        schema=expression.db or None,
        table=expression.name,
    )


def _assignment_target_columns(expression: exp.Expr) -> tuple[exp.Column, ...] | None:
    if isinstance(expression, exp.Column) and expression.name:
        return (expression,)
    if isinstance(expression, exp.Tuple):
        columns = tuple(expression.expressions)
        if columns and all(
            isinstance(column, exp.Column) and column.name for column in columns
        ):
            return cast(tuple[exp.Column, ...], columns)
    return None


def _assignment_column_names(expressions: object) -> tuple[str, ...] | None:
    if not isinstance(expressions, list) or not expressions:
        return None
    columns: list[str] = []
    for expression in expressions:
        if not isinstance(expression, exp.EQ):
            return None
        targets = _assignment_target_columns(expression.this)
        if targets is None:
            return None
        columns.extend(column.name for column in targets)
    return tuple(columns)


def _append_updated_column(
    columns_by_target: dict[_StatementTarget, list[str] | None],
    target: _StatementTarget,
    column_name: str,
) -> None:
    columns = columns_by_target.setdefault(target, [])
    if columns is not None:
        columns.append(column_name)


def _is_merge_action(statement: exp.Expr) -> bool:
    return isinstance(statement.parent, exp.When) and isinstance(
        statement.parent.find_ancestor(exp.Merge), exp.Merge
    )


def _merge_existing_row_effects_by_target(
    effects: list[_ExistingRowMutationEffect],
) -> tuple[_ExistingRowMutationEffect, ...]:
    target_order: list[_StatementTarget] = []
    columns_by_target: dict[_StatementTarget, list[str] | None] = {}
    deletes_by_target: dict[_StatementTarget, bool] = {}

    for effect in effects:
        if effect.target not in columns_by_target:
            target_order.append(effect.target)
            columns_by_target[effect.target] = []
            deletes_by_target[effect.target] = False
        target_columns = columns_by_target[effect.target]
        if effect.updated_columns is None:
            columns_by_target[effect.target] = None
        elif target_columns is not None:
            target_columns.extend(effect.updated_columns)
        deletes_by_target[effect.target] = (
            deletes_by_target[effect.target] or effect.deletes_rows
        )

    merged: list[_ExistingRowMutationEffect] = []
    for target in target_order:
        target_columns = columns_by_target[target]
        merged.append(
            _ExistingRowMutationEffect(
                target=target,
                updated_columns=(
                    tuple(target_columns) if target_columns is not None else None
                ),
                deletes_rows=deletes_by_target[target],
            )
        )
    return tuple(merged)


def _materialize_existing_row_mutation_analysis(
    analysis: _ExistingRowMutationAnalysis,
) -> ExistingRowMutationAnalysis:
    return {
        "effects": [
            {
                "target": {
                    "catalog": effect.target.catalog,
                    "schema": effect.target.schema,
                    "table": effect.target.table,
                },
                "updated_columns": (
                    list(effect.updated_columns)
                    if effect.updated_columns is not None
                    else None
                ),
                "deletes_rows": effect.deletes_rows,
            }
            for effect in analysis.effects
        ],
        "evidence_complete": analysis.evidence_complete,
    }


def _materialize_direct_write_analysis(
    analysis: _DirectWriteAnalysis,
) -> DirectWriteAnalysis:
    return {
        "targets": [
            {
                "catalog": target.catalog,
                "schema": target.schema,
                "table": target.table,
            }
            for target in analysis.targets
        ],
        "evidence_complete": analysis.evidence_complete,
    }


def _hardcoded_warning(count: int) -> str:
    value_word = "value" if count == 1 else "values"
    placeholder_word = "placeholder" if count == 1 else "placeholders"
    return f"replaced {count} hardcoded {value_word} with {placeholder_word}"


def _failure_reason(
    error: (
        StatementPreparationError
        | StatementClassificationError
        | FingerprintingError
        | SqlglotError
    ),
) -> str:
    if isinstance(error, BindingCountError):
        reason = f"bindings: {error}"
    elif isinstance(error, ParseError):
        reason = "invalid SQL syntax"
    elif isinstance(error, MySQLReplaceOptimizerHintError):
        reason = str(error)
    elif isinstance(error, TokenError):
        reason = "invalid SQL tokens"
    elif isinstance(error, UnsupportedError):
        reason = "target dialect cannot render the statement"
    elif isinstance(
        error,
        (StatementPreparationError, StatementClassificationError, FingerprintingError),
    ):
        reason = str(error)
    else:
        reason = "SQLGlot processing failed"
    return reason


def _require_dialect(dialect: str, *, role: str) -> str:
    if not dialect or not dialect.strip():
        raise StatementPreparationError(f"{role}_dialect must be explicit")
    normalized = dialect.strip().lower()
    try:
        Dialect.get_or_raise(normalized)
    except ValueError as error:
        raise StatementPreparationError(
            f"unsupported {role} dialect: {normalized}"
        ) from error
    return normalized


def _parse_single_statement(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> exp.Expr:
    read_dialect = parsing_dialect(
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    statements = [
        cast(exp.Expr, statement)
        for statement in parse(sql, read=read_dialect)
        if statement
    ]
    if len(statements) != 1:
        raise StatementPreparationError(
            f"expected exactly one SQL statement, received {len(statements)}"
        )
    return statements[0]


def _statement_type(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> StatementType:
    return require_extended_statement_type(statement, dialect=source_dialect)


def _extended_statement_type(statement: exp.Expr) -> StatementType | None:
    return extended_statement_type(statement)


def _tag_source_placeholders(
    sql: str,
    *,
    parameter_plan: ParameterPlan,
    source_dialect: str,
    target_dialect: str,
) -> tuple[str, dict[str, Binding]]:
    marker_prefix = _unused_marker_prefix(sql, kind="input")
    marker_values: dict[str, Binding] = {}
    target_slot_values: dict[int, Binding] = {}
    parts: list[str] = []
    cursor = 0
    for index, (occurrence, value) in enumerate(
        zip(
            parameter_plan.occurrences,
            parameter_plan.occurrence_values,
            strict=True,
        )
    ):
        marker = f"{marker_prefix}{index}"
        if occurrence.slot_number not in target_slot_values:
            target_slot_values[occurrence.slot_number] = _target_binding_value(
                value,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
        marker_values[marker] = target_slot_values[occurrence.slot_number]
        parts.extend((sql[cursor : occurrence.start], f":{marker}"))
        cursor = occurrence.end + 1
    parts.append(sql[cursor:])
    return "".join(parts), marker_values


def _unused_marker_prefix(sql: str, *, kind: str) -> str:
    marker_prefix = f"__sqlglot_experiments_{kind}_"
    while marker_prefix in sql:
        marker_prefix = f"_{marker_prefix}"
    return marker_prefix


def _require_owned_placeholders(
    statement: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> None:
    for raw_node in statement.walk():
        node = cast(exp.Expr, raw_node)
        if isinstance(node, exp.Parameter):
            raise StatementPreparationError("unsupported source placeholder form")
        if isinstance(node, exp.Placeholder):
            marker = _placeholder_name(node)
            if marker not in marker_values:
                raise StatementPreparationError("unsupported source placeholder form")


def _mark_hardcoded_values(
    source_ast: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> tuple[exp.Expr, dict[str, Binding], set[tuple[str, ...]], int]:
    target_ast = source_ast.copy()
    candidates = _find_candidates(
        target_ast,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    marker_values = marker_values.copy()
    marker_prefix = _unused_marker_prefix(
        " ".join(
            (
                _generate_sql(
                    target_ast,
                    source_dialect=source_dialect,
                    target_dialect=source_dialect,
                ),
                *marker_values,
            )
        ),
        kind="literal",
    )
    field_keys: set[tuple[str, ...]] = set()
    for index, candidate in enumerate(candidates):
        marker = f"{marker_prefix}{index}"
        marker_values[marker] = candidate["value"]
        field_keys.update(candidate["field_keys"])
        candidate["node"].replace(exp.Placeholder(this=marker))

    return target_ast, marker_values, field_keys, len(candidates)


def _binding_markers_in_target_order(
    statement: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> tuple[str, ...]:
    marked_sql = _generate_sql(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    marker_order = [
        token.text
        for token in tokenize_preparation_sql(
            marked_sql,
            dialect=target_dialect,
        )
        if token.text in marker_values
    ]
    if set(marker_order) != set(marker_values) or len(marker_order) != len(
        marker_values
    ):
        raise StatementPreparationError("target rendering lost a binding marker")
    return tuple(marker_order)


def _plain_insert_value_binding_rows(
    statement: exp.Expr,
    *,
    statement_type: StatementType,
    binding_markers: tuple[str, ...],
    source_uses_values_syntax: bool,
) -> tuple[tuple[int, ...], ...] | None:
    """Map direct plain VALUES cells to authoritative returned binding indexes."""

    if (
        statement_type != "INSERT"
        or not source_uses_values_syntax
        or not isinstance(statement, exp.Insert)
    ):
        return None
    if any(
        value
        for name, value in statement.args.items()
        if name not in {"this", "expression"}
    ):
        return None

    values_expression = statement.expression
    if not isinstance(values_expression, exp.Values) or any(
        value for name, value in values_expression.args.items() if name != "expressions"
    ):
        return None

    binding_indexes = {marker: index for index, marker in enumerate(binding_markers)}
    rows: list[tuple[int, ...]] = []
    for row in values_expression.expressions:
        if not isinstance(row, exp.Tuple) or any(
            value for name, value in row.args.items() if name != "expressions"
        ):
            return None
        binding_row: list[int] = []
        for value_expression in row.expressions:
            if not isinstance(value_expression, exp.Placeholder):
                return None
            marker = _placeholder_name(value_expression)
            if marker is None or marker not in binding_indexes:
                return None
            binding_row.append(binding_indexes[marker])
        rows.append(tuple(binding_row))

    return tuple(rows) if rows else None


def _source_uses_insert_values_syntax(
    sql: str,
    *,
    source_dialect: str,
) -> bool:
    """Require an actual top-level source VALUES token after the root INSERT."""

    depth = 0
    root_insert_seen = False
    for token in tokenize_preparation_sql(sql, dialect=source_dialect):
        if token.token_type is TokenType.L_PAREN:
            depth += 1
            continue
        if token.token_type is TokenType.R_PAREN:
            depth = max(0, depth - 1)
            continue
        if depth != 0:
            continue
        if not root_insert_seen:
            root_insert_seen = token.token_type is TokenType.INSERT
            continue
        if token.token_type is TokenType.VALUES:
            return True
    return False


def _make_placeholders_anonymous(
    statement: exp.Expr,
    *,
    marker_values: dict[str, Binding],
    source_dialect: str,
    target_dialect: str,
) -> None:
    for raw_node in list(statement.walk()):
        node = cast(exp.Expr, raw_node)
        if (
            isinstance(node, exp.Placeholder)
            and _placeholder_name(node) in marker_values
        ):
            node.replace(exp.Placeholder())


def _require_complete_ast(
    statement: exp.Expr,
    *,
    bindings: list[Binding],
    source_dialect: str,
    target_dialect: str,
) -> None:
    placeholder_count = sum(
        isinstance(node, (exp.Placeholder, exp.Parameter)) for node in statement.walk()
    )
    if placeholder_count != len(bindings):
        raise StatementPreparationError(
            "target SQL placeholder count does not match returned bindings"
        )


def _placeholder_name(placeholder: exp.Placeholder) -> str | None:
    name = placeholder.this
    if isinstance(name, exp.Identifier):
        return name.name
    return name if isinstance(name, str) else None


def _target_binding_value(
    value: Binding,
    *,
    source_dialect: str,
    target_dialect: str,
) -> Binding:
    if target_dialect == "sqlite" and isinstance(value, Decimal):
        try:
            return float(value)
        except (OverflowError, ValueError) as error:
            raise StatementPreparationError(
                "binding cannot be represented by the SQLite target"
            ) from error
    return value


def _find_candidates(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for raw_node in statement.walk(bfs=False):
        node = cast(exp.Expr, raw_node)
        is_bindable, value = _binding_value(
            node,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        if not is_bindable:
            continue

        field_keys = _associated_field_keys(
            node,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        if field_keys is not None:
            candidates.append(
                {
                    "node": node,
                    "value": value,
                    "field_keys": field_keys,
                }
            )
    return candidates


def _binding_value(
    node: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> tuple[bool, Binding]:
    if isinstance(node, (exp.Literal, exp.Boolean, exp.Null)) or (
        isinstance(node, exp.Neg)
        and isinstance(node.this, exp.Literal)
        and not node.this.is_string
    ):
        value = node.to_py()
    else:
        return False, None

    return True, _target_binding_value(
        value,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )


def _associated_field_keys(
    node: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]] | None:
    parent = node.parent
    if isinstance(parent, _DIRECT_PREDICATES):
        if node is parent.this:
            keys = _column_keys(
                parent.expression,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
            return keys or None
        if node is parent.expression:
            keys = _column_keys(
                parent.this,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )
            return keys or None

    if isinstance(parent, exp.Between) and node.arg_key in {"low", "high"}:
        keys = _column_keys(
            parent.this,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        return keys or None

    if isinstance(parent, exp.In) and node.arg_key == "expressions":
        keys = _column_keys(
            parent.this,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )
        return keys or None

    if isinstance(parent, exp.Tuple) and node.arg_key == "expressions":
        return _insert_field_key(
            parent,
            node.index,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )

    return None


def _column_keys(
    expression: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]]:
    return {
        tuple(identifier.name for identifier in column.parts)
        for column in expression.find_all(exp.Column)
    }


def _insert_field_key(
    row: exp.Tuple,
    position: int | None,
    *,
    source_dialect: str,
    target_dialect: str,
) -> set[tuple[str, ...]] | None:
    if position is None:
        return None

    insert: exp.Insert | None = None
    merge_target: exp.Table | None = None
    if isinstance(row.parent, exp.Values) and isinstance(row.parent.parent, exp.Insert):
        insert = row.parent.parent
    elif isinstance(row.parent, exp.Insert) and row is row.parent.expression:
        candidate = row.parent
        merge = candidate.find_ancestor(exp.Merge)
        if isinstance(merge, exp.Merge) and isinstance(merge.this, exp.Table):
            insert = candidate
            merge_target = merge.this
    if insert is None:
        return None

    table_key: tuple[str, ...] = ()
    columns: list[exp.Expr] = []
    if merge_target is not None and isinstance(insert.this, exp.Tuple):
        columns = insert.this.expressions
        table_key = tuple(identifier.name for identifier in merge_target.parts)
    elif isinstance(insert.this, exp.Schema):
        columns = insert.this.expressions
        if isinstance(insert.this.this, exp.Table):
            table_key = tuple(identifier.name for identifier in insert.this.this.parts)
    elif isinstance(insert.this, exp.Table):
        table_key = tuple(identifier.name for identifier in insert.this.parts)

    if position >= len(columns):
        return set()
    return {(*table_key, columns[position].name)}


def _generate_sql(
    statement: exp.Expr,
    *,
    source_dialect: str,
    target_dialect: str,
) -> str:
    return generate_target_sql(
        statement,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlglot import Dialect
from sqlglot.tokenizer_core import Token, TokenType

Binding = object
InputBindings = Sequence[Binding] | Mapping[str, Binding]


class ParameterPlanningError(ValueError):
    """Source bindings cannot be resolved into deterministic parameter slots."""


@dataclass(frozen=True)
class ParameterSlot:
    number: int
    bind_name: str | None


@dataclass(frozen=True)
class ParameterOccurrence:
    start: int
    end: int
    spelling: str
    slot_number: int


@dataclass(frozen=True)
class ParameterPlan:
    slots: tuple[ParameterSlot, ...]
    occurrences: tuple[ParameterOccurrence, ...]
    occurrence_values: tuple[Binding, ...]


@dataclass(frozen=True)
class _LexicalParameter:
    start: int
    end: int
    spelling: str
    kind: str


def plan_source_parameters(
    sql: str,
    *,
    bindings: InputBindings | None,
    source_dialect: str,
    target_dialect: str,
) -> ParameterPlan:
    """Resolve source placeholders and bindings into reusable logical slots."""
    slots, occurrences = source_parameter_structure(
        sql,
        source_dialect=source_dialect,
        target_dialect=target_dialect,
    )
    slot_values = _resolve_slot_values(
        slots,
        bindings=bindings,
        source_dialect=source_dialect,
    )
    return ParameterPlan(
        slots=slots,
        occurrences=occurrences,
        occurrence_values=tuple(
            slot_values[occurrence.slot_number] for occurrence in occurrences
        ),
    )


def source_parameter_structure(
    sql: str,
    *,
    source_dialect: str,
    target_dialect: str,
) -> tuple[tuple[ParameterSlot, ...], tuple[ParameterOccurrence, ...]]:
    """Return source parameter slots and occurrences without resolving values."""
    lexical_parameters = _lexical_parameters(
        sql,
        source_dialect=source_dialect,
    )
    return _allocate_slots(
        lexical_parameters,
        source_dialect=source_dialect,
    )


def _lexical_parameters(
    sql: str,
    *,
    source_dialect: str,
) -> tuple[_LexicalParameter, ...]:
    tokens = Dialect.get_or_raise(source_dialect).tokenize(sql)
    if source_dialect == "sqlite":
        return _sqlite_parameters(
            sql,
            tokens=tokens,
        )
    if source_dialect == "postgres":
        return _postgres_parameters(
            sql,
            tokens=tokens,
        )
    return ()


def _sqlite_parameters(
    sql: str,
    *,
    tokens: list[Token],
) -> tuple[_LexicalParameter, ...]:
    parameters: list[_LexicalParameter] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if token.token_type is TokenType.PLACEHOLDER:
            if (
                following is not None
                and following.start == token.end + 1
                and following.token_type is TokenType.NUMBER
                and following.text.isdigit()
            ):
                parameters.append(
                    _lexical_parameter(
                        sql,
                        start=token.start,
                        end=following.end,
                        kind="numbered_qmark",
                    )
                )
                index += 2
                continue
            parameters.append(
                _lexical_parameter(
                    sql,
                    start=token.start,
                    end=token.end,
                    kind="anonymous",
                )
            )
        elif token.text in {":", "@"} and _is_contiguous_name(token, following):
            assert following is not None
            parameters.append(
                _lexical_parameter(
                    sql,
                    start=token.start,
                    end=following.end,
                    kind="named",
                )
            )
            index += 2
            continue
        elif token.token_type is TokenType.VAR and token.text.startswith("$"):
            body = token.text[1:]
            if _is_parameter_name(body):
                parameters.append(
                    _lexical_parameter(
                        sql,
                        start=token.start,
                        end=token.end,
                        kind="named",
                    )
                )
        index += 1
    return tuple(parameters)


def _postgres_parameters(
    sql: str,
    *,
    tokens: list[Token],
) -> tuple[_LexicalParameter, ...]:
    parameters: list[_LexicalParameter] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if (
            token.text == "$"
            and following is not None
            and following.start == token.end + 1
            and following.token_type is TokenType.NUMBER
            and following.text.isdigit()
        ):
            parameters.append(
                _lexical_parameter(
                    sql,
                    start=token.start,
                    end=following.end,
                    kind="numbered_dollar",
                )
            )
            index += 2
            continue
        index += 1
    return tuple(parameters)


def _lexical_parameter(
    sql: str,
    *,
    start: int,
    end: int,
    kind: str,
) -> _LexicalParameter:
    return _LexicalParameter(
        start=start,
        end=end,
        spelling=sql[start : end + 1],
        kind=kind,
    )


def _is_contiguous_name(token: Token, following: Token | None) -> bool:
    return (
        following is not None
        and following.start == token.end + 1
        and _is_parameter_name(following.text)
    )


def _is_parameter_name(text: str) -> bool:
    return bool(text) and all(
        character == "_" or character.isalnum() for character in text
    )


def _allocate_slots(
    parameters: tuple[_LexicalParameter, ...],
    *,
    source_dialect: str,
) -> tuple[tuple[ParameterSlot, ...], tuple[ParameterOccurrence, ...]]:
    if source_dialect == "sqlite":
        return _allocate_sqlite_slots(parameters)
    if source_dialect == "postgres":
        return _allocate_postgres_slots(parameters)
    return (), ()


def _allocate_sqlite_slots(
    parameters: tuple[_LexicalParameter, ...],
) -> tuple[tuple[ParameterSlot, ...], tuple[ParameterOccurrence, ...]]:
    maximum_slot = 0
    slot_names: dict[int, str | None] = {}
    named_slots: dict[str, int] = {}
    occurrences: list[ParameterOccurrence] = []

    for parameter in parameters:
        if parameter.kind == "numbered_qmark":
            slot_number = int(parameter.spelling[1:])
            if slot_number < 1:
                raise ParameterPlanningError("SQLite parameter slots start at 1")
            maximum_slot = max(maximum_slot, slot_number)
            if slot_names.get(slot_number) is None:
                slot_names[slot_number] = parameter.spelling
        elif parameter.kind == "named":
            existing_slot = named_slots.get(parameter.spelling)
            if existing_slot is None:
                maximum_slot += 1
                existing_slot = maximum_slot
                named_slots[parameter.spelling] = existing_slot
                slot_names[existing_slot] = parameter.spelling
            slot_number = existing_slot
        else:
            maximum_slot += 1
            slot_number = maximum_slot
            slot_names[slot_number] = None

        occurrences.append(
            ParameterOccurrence(
                start=parameter.start,
                end=parameter.end,
                spelling=parameter.spelling,
                slot_number=slot_number,
            )
        )

    slots = tuple(
        ParameterSlot(number=number, bind_name=slot_names.get(number))
        for number in range(1, maximum_slot + 1)
    )
    return slots, tuple(occurrences)


def _allocate_postgres_slots(
    parameters: tuple[_LexicalParameter, ...],
) -> tuple[tuple[ParameterSlot, ...], tuple[ParameterOccurrence, ...]]:
    maximum_slot = 0
    occurrences: list[ParameterOccurrence] = []
    for parameter in parameters:
        slot_number = int(parameter.spelling[1:])
        if slot_number < 1:
            raise ParameterPlanningError("PostgreSQL parameter slots start at 1")
        maximum_slot = max(maximum_slot, slot_number)
        occurrences.append(
            ParameterOccurrence(
                start=parameter.start,
                end=parameter.end,
                spelling=parameter.spelling,
                slot_number=slot_number,
            )
        )
    slots = tuple(
        ParameterSlot(number=number, bind_name=f"${number}")
        for number in range(1, maximum_slot + 1)
    )
    return slots, tuple(occurrences)


def _resolve_slot_values(
    slots: tuple[ParameterSlot, ...],
    *,
    bindings: InputBindings | None,
    source_dialect: str,
) -> dict[int, Binding]:
    supplied: InputBindings = () if bindings is None else bindings
    if isinstance(supplied, Mapping):
        return _resolve_mapping(
            slots,
            bindings=supplied,
            source_dialect=source_dialect,
        )
    if isinstance(supplied, (str, bytes, bytearray, memoryview)):
        raise ParameterPlanningError("bindings must be a sequence or mapping of values")

    try:
        values = list(supplied)
    except TypeError as error:
        raise ParameterPlanningError(
            "bindings must be a sequence or mapping of values"
        ) from error
    if len(values) != len(slots):
        binding_word = "binding" if len(slots) == 1 else "bindings"
        raise ParameterPlanningError(
            f"statement requires {len(slots)} caller {binding_word}; "
            f"received {len(values)}"
        )
    return {slot.number: values[slot.number - 1] for slot in slots}


def _resolve_mapping(
    slots: tuple[ParameterSlot, ...],
    *,
    bindings: Mapping[str, Binding],
    source_dialect: str,
) -> dict[int, Binding]:
    if source_dialect != "sqlite":
        raise ParameterPlanningError(
            f"{source_dialect} source bindings must be an ordered sequence"
        )

    values: dict[int, Binding] = {}
    for slot in slots:
        if slot.bind_name is None:
            raise ParameterPlanningError(
                "a mapping cannot bind an anonymous SQLite parameter slot"
            )
        key = slot.bind_name[1:]
        if key not in bindings:
            raise ParameterPlanningError(
                f"missing caller binding for SQLite parameter {slot.bind_name}"
            )
        values[slot.number] = bindings[key]
    return values

"""Pure fact application and decision generation are intentionally distinct.

Reducer functions must use only their immutable event/state arguments. All
causal mutations, including RNG consumption and timer requests, belong to fact
application. Producers return facts; they cannot publish mutable strategy state.
Domain modules register concrete reducers/producers when their tasks are built.
"""

from __future__ import annotations

import json
import math
import re
import typing
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import Enum, IntEnum
from functools import cache
from types import UnionType
from typing import Any, TypeAliasType, cast, get_args, get_origin, get_type_hints

from quantdesk.core.checkpoint import EngineState
from quantdesk.core.clock import TimerRequest
from quantdesk.core.events import PAYLOAD_TYPES, Envelope, canonical_bytes
from quantdesk.core.types import EventPayload
from quantdesk.persistence.event_store import (
    EconomicAliasUpdate,
    LedgerTransaction,
    OutboxInstruction,
    ProjectionUpdate,
)


def require_immutable(value: object) -> None:
    """Reject shallow-frozen callback results containing mutable nested effects."""
    if value is None or isinstance(value, (str, bytes, bool, int, float, Decimal)):
        return
    if type(value) is tuple:
        for item in value:
            require_immutable(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        parameters = getattr(type(value), "__dataclass_params__", None)
        if parameters is not None and parameters.frozen:
            for field in fields(value):
                require_immutable(getattr(value, field.name))
            return
    raise TypeError("causal state and effects must be deeply immutable values")


@cache
def _payload_hints(payload_type: type[object]) -> dict[str, Any]:
    return get_type_hints(payload_type)


def _decode_value(value: object, annotation: object) -> object:
    """Decode the registered immutable type tree without lossy scalar coercions."""
    if isinstance(annotation, TypeAliasType):
        return _decode_value(value, annotation.__value__)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (UnionType, typing.Union):
        for member in args:
            try:
                return _decode_value(value, member)
            except (TypeError, ValueError, InvalidOperation):
                continue
        raise ValueError("value does not match its declared union")
    if annotation is type(None) and value is None:
        return None
    if annotation in (str, bool) and type(value) is annotation:
        return value
    if annotation is int:
        if type(value) is int:
            return value
        # canonical_bytes encodes only unsafe JSON integers as decimal strings.
        if isinstance(value, str) and re.fullmatch(r"-?[1-9][0-9]*", value):
            integer = int(value)
            if abs(integer) > 9_007_199_254_740_991:
                return integer
        raise ValueError("expected an exact integer")
    if annotation is float and type(value) in (int, float):
        number = float(cast(int | float, value))
        if math.isfinite(number):
            return number
        raise ValueError("expected a finite numeric value")
    if annotation is Decimal:
        if type(value) is not str:
            raise ValueError("Decimal must be an exact string")
        decimal = Decimal(value)
        if not decimal.is_finite():
            raise ValueError("Decimal must be finite")
        return decimal
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not str:
            raise ValueError("expected a declared enum string")
        try:
            return annotation(value)
        except ValueError as exc:
            raise ValueError("unknown enum value") from exc
    if origin is tuple:
        if type(value) is not list:
            raise ValueError("expected a tuple encoded as a JSON array")
        items = cast(list[object], value)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode_value(item, args[0]) for item in items)
        if len(items) != len(args):
            raise ValueError("tuple has the wrong number of fields")
        return tuple(
            _decode_value(item, expected) for item, expected in zip(items, args, strict=True)
        )
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, dict) or set(value) != {
            field.name for field in fields(annotation)
        }:
            raise ValueError("object does not match the registered dataclass fields")
        hints = _payload_hints(annotation)
        decoded = {name: _decode_value(value[name], expected) for name, expected in hints.items()}
        # Invoke each registered constructor, including nested BookLevel and __post_init__.
        return annotation(**decoded)
    raise ValueError("value does not match its declared type")


def decode_payload(event: Envelope) -> EventPayload:
    try:
        payload_type = PAYLOAD_TYPES.resolve(event.event_type)
        raw = json.loads(event.payload)
        if canonical_bytes(raw) != event.payload:
            raise ValueError("noncanonical JSON encoding")
        decoded = _decode_value(raw, payload_type)
        if canonical_bytes(decoded) != event.payload:
            raise ValueError("noncanonical typed payload encoding")
        return cast(EventPayload, decoded)
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError(f"invalid {event.event_type} payload: {exc}") from exc


class Stage(IntEnum):
    VALIDATE = 10
    BOOK_ACCOUNT_OMS = 20
    FEATURES = 30
    EXITS = 40
    STRATEGIES = 50
    ARBITRATION = 60
    RISK = 70
    INSTRUCTIONS = 80
    READ_MODELS = 90


@dataclass(frozen=True, slots=True)
class EventDraft:
    event_type: str
    payload: bytes
    schema_version: int = 1
    instrument_id: str | None = None


@dataclass(frozen=True, slots=True)
class Reduction:
    state: EngineState
    ledger_transactions: tuple[LedgerTransaction, ...] = ()
    outbox_instructions: tuple[OutboxInstruction, ...] = ()
    projection_updates: tuple[ProjectionUpdate, ...] = ()
    timers: tuple[TimerRequest, ...] = ()
    cancel_timers: tuple[str, ...] = ()
    economic_aliases: tuple[EconomicAliasUpdate, ...] = ()


@dataclass(frozen=True, slots=True)
class FactReducer:
    reducer_id: str
    stage: Stage
    apply: Callable[[Envelope, EngineState], Reduction]


@dataclass(frozen=True, slots=True)
class DecisionProducer:
    producer_id: str
    stage: Stage
    decide: Callable[[Envelope, EngineState], tuple[EventDraft, ...]]


def apply_engine_fact(event: Envelope, state: EngineState) -> EngineState:
    """Apply already-authorized activation/operational facts in both replay paths.

    This is not an authorization or promotion policy. The command/risk/registry
    components must decide whether to emit these facts before this application.
    """
    payload = json.loads(event.payload)
    if event.event_type == "ConfigActivated":
        if int(payload["activation_seq"]) != event.engine_seq:
            raise ValueError("config activation must name its causal event boundary")
        prior = payload["prior_config_hash"]
        if prior != state.config_hash and not (
            prior is None and state.config_hash == "unconfigured"
        ):
            raise ValueError("config activation prior hash does not match causal state")
        return replace(state, config_hash=payload["config_hash"])
    if event.event_type == "ModelActivated":
        if int(payload["activation_seq"]) != event.engine_seq:
            raise ValueError("model activation must name its causal event boundary")
        models = dict(state.model_hashes)
        strategy = payload["strategy_id"]
        if models.get(strategy) != payload["prior_model_hash"]:
            raise ValueError("model activation prior hash does not match causal state")
        models[strategy] = payload["model_hash"]
        return replace(state, model_hashes=tuple(sorted(models.items())))
    if event.event_type == "RiskLatchChanged":
        prior_payload = state.get("risk_latches", payload["latch_id"])
        changed = state.put("risk_latches", payload["latch_id"], event.payload)
        return replace(changed, risk_epoch=state.risk_epoch + (prior_payload != event.payload))
    if event.event_type == "ClockAdjusted":
        if int(payload["next_available_ns"]) > event.available_ns:
            raise ValueError("clock anchor cannot be available after its recorded event")
        return state.put("clock_anchors", "current", event.payload)
    return state

"""Pure fact application and decision generation are intentionally distinct.

Reducer functions must use only their immutable event/state arguments. All
causal mutations, including RNG consumption and timer requests, belong to fact
application. Producers return facts; they cannot publish mutable strategy state.
Domain modules register concrete reducers/producers when their tasks are built.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal
from enum import IntEnum

from quantdesk.core.checkpoint import EngineState
from quantdesk.core.clock import TimerRequest
from quantdesk.core.events import Envelope
from quantdesk.persistence.event_store import LedgerTransaction, OutboxInstruction, ProjectionUpdate


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

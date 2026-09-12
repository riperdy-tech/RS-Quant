"""Pure protection decisions; the account writer authorizes every resulting action.

Contingent stop coverage is distinct from immediately executable close reserves.
These recommendations cannot submit orders or claim that an exit filled.
"""

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import cast

from quantdesk.core.checkpoint import EngineState
from quantdesk.core.events import (
    PAYLOAD_TYPES,
    Envelope,
    ExecutionReport,
    ProtectionReport,
    RiskLatchChanged,
    canonical_bytes,
)
from quantdesk.core.reducers import (
    DecisionProducer,
    EventDraft,
    FactReducer,
    Reduction,
    Stage,
    _decode_value,
    decode_payload,
)
from quantdesk.core.types import EventPayload, Side
from quantdesk.execution.order_state import TERMINAL, OMSState
from quantdesk.persistence.event_store import ProjectionUpdate
from quantdesk.portfolio.events import FinancialEventObserved
from quantdesk.portfolio.ledger import LedgerState


@dataclass(frozen=True, slots=True)
class NativeProtection:
    native_id: str
    group_id: str
    quantity_lots: int
    trigger_basis: str
    trigger_value: str
    status: str
    safe_reduce: bool
    kind: str = "SL"
    instrument_id: str | None = None
    position_side: str | None = None
    reduce_only: bool | None = None

    def __post_init__(self) -> None:
        if not self.native_id or type(self.quantity_lots) is not int or self.quantity_lots < 0:
            raise ValueError("invalid protection identity or quantity")
        if not Decimal(self.trigger_value).is_finite() or Decimal(self.trigger_value) <= 0:
            raise ValueError("invalid protection trigger")


@dataclass(frozen=True, slots=True)
class ProtectionState:
    group_id: str
    instrument_id: str
    desired_lots: int
    trigger_basis: str
    trigger_value: str
    status: str = "PENDING"
    failure_started_ns: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.group_id
            or not self.instrument_id
            or type(self.desired_lots) is not int
            or self.desired_lots < 0
        ):
            raise ValueError("invalid desired protection identity/quantity")
        if (
            self.trigger_basis not in {"MARK", "LAST"}
            or not Decimal(self.trigger_value).is_finite()
            or Decimal(self.trigger_value) <= 0
        ):
            raise ValueError("unverified protection trigger")


@dataclass(frozen=True, slots=True)
class ProtectionDecision:
    state: ProtectionState
    actions: tuple[str, ...]
    covered_lots: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ProtectionReview(EventPayload):
    group_id: str
    native_orders: tuple[NativeProtection, ...]
    observed_ns: int
    verified_flat: bool
    capability_verified: bool
    restoration_supported: bool


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ProtectionActionsRequired(EventPayload):
    group_id: str
    instrument_id: str
    desired_lots: int
    actions: tuple[str, ...]
    observed_ns: int


@dataclass(frozen=True, slots=True)
class ProtectionOwnership:
    instrument_id: str
    group_id: str | None
    signed_lots: int
    execution_ids: tuple[str, ...]


def protection_ownership(state: EngineState) -> tuple[ProtectionOwnership, ...]:
    return cast(
        tuple[ProtectionOwnership, ...],
        _decode_value(
            json.loads(state.get("oms", "protection-ownership-v1", b"[]")),
            tuple[ProtectionOwnership, ...],
        ),
    )


def _execution_ownership(
    event: Envelope, payload: ExecutionReport, state: EngineState
) -> EngineState:
    instrument = cast(str, event.instrument_id)
    orders = OMSState.from_bytes(state.get("oms", "state-v1"))
    execution = next(
        e
        for e in orders.executions
        if e.instrument_id == instrument and payload.native_execution_id in e.native_ids
    )
    owners = {owner.instrument_id: owner for owner in protection_ownership(state)}
    prior = owners.get(instrument, ProtectionOwnership(instrument, None, 0, ()))
    identities = tuple(sorted(set((*prior.execution_ids, *execution.native_ids))))
    if set(prior.execution_ids).intersection(execution.native_ids):
        # Corroboration/aliases cannot restart a historical position cycle.
        owners[instrument] = replace(prior, execution_ids=identities)
    else:
        position = LedgerState.from_bytes(state.get("ledger", "state-v1")).position(instrument)
        delta = execution.lots * (1 if execution.side == Side.BUY else -1)
        before = position.signed_lots - delta
        order = next(
            (
                o.instruction
                for o in orders.orders
                if o.instruction.client_order_id == execution.client_order_id
            ),
            None,
        )
        candidate = (
            order.protection_group_id
            if order and not order.reduce_only and order.side == execution.side
            else None
        )
        group = prior.group_id
        if prior.signed_lots != before:
            group = None  # no invented ownership for an unobserved opening balance
        elif not position.signed_lots:
            group = None
        elif not before or before * position.signed_lots < 0:
            group = candidate
        elif before * delta > 0 and candidate != group:
            # Multiple independent stop groups in one exposure cycle are not
            # silently assigned the whole position; retain an explicit block.
            group = None
        owners[instrument] = ProtectionOwnership(
            instrument, group, position.signed_lots, identities
        )
    return state.put(
        "oms",
        "protection-ownership-v1",
        canonical_bytes(tuple(owners[key] for key in sorted(owners))),
    )


def protection_reducer(restoration_budget_ns: int = 2_000_000_000) -> FactReducer:
    manager = ProtectionManager(restoration_budget_ns)

    def apply(event: Envelope, state: EngineState) -> Reduction:
        payload = decode_payload(event)
        if isinstance(payload, FinancialEventObserved):
            payload = payload.financial_payload
        if isinstance(payload, ExecutionReport):
            return Reduction(_execution_ownership(event, payload, state))
        if not isinstance(payload, ProtectionReview):
            return Reduction(state)
        orders = OMSState.from_bytes(state.get("oms", "state-v1"))
        owned = [
            o.instruction
            for o in orders.orders
            if o.instruction.protection_group_id == payload.group_id
            and not o.instruction.reduce_only
        ]
        if (
            len(owned) != 1
            or owned[0].native_trigger_value is None
            or owned[0].native_trigger_basis is None
        ):
            raise ValueError("protection review has no unique committed stop plan")
        instruction = owned[0]
        data = state.get("protection", payload.group_id, b"")
        prior = (
            cast(ProtectionDecision, _decode_value(json.loads(data), ProtectionDecision)).state
            if data
            else ProtectionState(
                payload.group_id,
                instruction.instrument_id,
                0,
                cast(str, instruction.native_trigger_basis),
                str(instruction.native_trigger_value),
            )
        )
        portfolio = LedgerState.from_bytes(state.get("ledger", "state-v1"))
        position = portfolio.position(instruction.instrument_id).signed_lots
        ownership = next(
            (
                owner
                for owner in protection_ownership(state)
                if owner.instrument_id == instruction.instrument_id
            ),
            None,
        )
        owned_lots = (
            position
            if ownership
            and ownership.group_id == payload.group_id
            and ownership.signed_lots == position
            else 0
        )
        own_legs = tuple(
            native
            for native in payload.native_orders
            if native.group_id == payload.group_id and native.status == "pending"
        )
        decision = manager.evaluate(
            prior,
            payload.native_orders,
            owned_lots=owned_lots,
            observed_ns=payload.observed_ns,
            verified_flat=payload.verified_flat,
            capability_verified=payload.capability_verified,
        )
        if position and (
            ownership is None or ownership.group_id is None or ownership.signed_lots != position
        ):
            decision = ProtectionDecision(
                replace(prior, desired_lots=0, status="OWNERSHIP_UNVERIFIED"),
                ("LATCH_ENTRIES", "RECONCILE"),
                0,
            )
        elif position and not owned_lots:
            decision = ProtectionDecision(
                replace(
                    prior,
                    desired_lots=0,
                    status="INACTIVE_PENDING_PROTECTION" if own_legs else "INACTIVE",
                ),
                ("LATCH_ENTRIES", "RECONCILE") if own_legs else (),
                0,
            )
        elif (
            not position
            and not own_legs
            and orders.order(instruction.client_order_id).lifecycle in TERMINAL
            and not orders.order(instruction.client_order_id).accounted_fill_lots
        ):
            decision = ProtectionDecision(replace(prior, desired_lots=0, status="INACTIVE"), (), 0)
        if not payload.restoration_supported and any(
            a in decision.actions for a in ("RESTORE_STOP", "RESIZE_STOP")
        ):
            decision = replace(
                decision,
                state=replace(decision.state, status="EXIT_REQUIRED"),
                actions=("LATCH_ENTRIES", "CANCEL_ENTRY_REMAINDER", "BOUNDED_REDUCE_ONLY_EXIT"),
            )
        data = canonical_bytes(decision)
        return Reduction(
            state.put("protection", payload.group_id, data),
            projection_updates=(ProjectionUpdate("protection_groups", payload.group_id, data),),
        )

    return FactReducer("protection-v1", Stage.FEATURES, apply)


def protection_producer() -> DecisionProducer:
    def decide(event: Envelope, state: EngineState) -> tuple[EventDraft, ...]:
        payload = decode_payload(event)
        if not isinstance(payload, ProtectionReview):
            return ()
        decision = cast(
            ProtectionDecision,
            _decode_value(
                json.loads(state.get("protection", payload.group_id)), ProtectionDecision
            ),
        )
        report = ProtectionReport(
            payload.group_id,
            decision.state.status,
            decision.covered_lots,
            payload.observed_ns,
            ",".join(decision.actions) or None,
        )
        facts: list[EventPayload] = [report]
        if decision.actions:
            facts.extend(
                (
                    RiskLatchChanged(
                        f"protection:{payload.group_id}",
                        "instrument",
                        True,
                        decision.state.status,
                        "protection-v1",
                    ),
                    ProtectionActionsRequired(
                        payload.group_id,
                        decision.state.instrument_id,
                        decision.state.desired_lots,
                        decision.actions,
                        payload.observed_ns,
                    ),
                )
            )
        return tuple(
            EventDraft(
                type(fact).__name__,
                canonical_bytes(fact),
                instrument_id=decision.state.instrument_id,
            )
            for fact in facts
        )

    return DecisionProducer("protection-v1", Stage.EXITS, decide)


class ProtectionManager:
    def __init__(self, restoration_budget_ns: int = 2_000_000_000) -> None:
        if type(restoration_budget_ns) is not int or restoration_budget_ns <= 0:
            raise ValueError("protection restoration budget must be positive")
        self.budget = restoration_budget_ns

    def evaluate(
        self,
        state: ProtectionState,
        orders: tuple[NativeProtection, ...],
        *,
        owned_lots: int,
        observed_ns: int,
        verified_flat: bool,
        capability_verified: bool,
    ) -> ProtectionDecision:
        if type(owned_lots) is not int or type(observed_ns) is not int or observed_ns < 0:
            raise ValueError("exact position and timestamp required")
        legs = tuple(o for o in orders if o.group_id == state.group_id and o.status == "pending")
        stops = tuple(
            o
            for o in legs
            if o.kind == "SL"
            and o.safe_reduce
            and o.instrument_id == state.instrument_id
            and o.position_side == ("LONG" if owned_lots > 0 else "SHORT")
            and o.reduce_only is True
            and o.trigger_basis == state.trigger_basis
            and Decimal(o.trigger_value) == Decimal(state.trigger_value)
        )
        # Multiple matching legs can be replacements or duplicates. Do not sum
        # their sizes or include TP legs to manufacture sufficient stop coverage.
        covered = max((o.quantity_lots for o in stops), default=0)
        quantity = abs(owned_lots)
        state = replace(state, desired_lots=quantity)
        if quantity == 0:
            if not verified_flat:
                return ProtectionDecision(
                    replace(state, status="AWAITING_FLAT"), ("LATCH_ENTRIES", "RECONCILE"), covered
                )
            return ProtectionDecision(
                replace(state, status="FLAT", failure_started_ns=None),
                ("CANCEL_STOP", "RECONCILE") if legs else (),
                covered,
            )
        if not capability_verified:
            return ProtectionDecision(
                replace(state, status="BLOCKED"),
                ("LATCH_ENTRIES", "CANCEL_ENTRY_REMAINDER", "BOUNDED_REDUCE_ONLY_EXIT"),
                covered,
            )
        if covered == quantity and len(stops) == 1:
            return ProtectionDecision(
                replace(state, status="PROTECTED", failure_started_ns=None), (), covered
            )
        if covered > quantity and len(stops) == 1:
            return ProtectionDecision(
                replace(state, status="RESIZING"), ("LATCH_ENTRIES", "RESIZE_STOP"), covered
            )
        started = state.failure_started_ns if state.failure_started_ns is not None else observed_ns
        if observed_ns < started:
            raise ValueError("protection clock moved backward")
        expired = observed_ns - started >= self.budget
        return ProtectionDecision(
            replace(
                state,
                status="EXIT_REQUIRED" if expired else "RESTORING",
                failure_started_ns=started,
            ),
            (
                "LATCH_ENTRIES",
                "CANCEL_ENTRY_REMAINDER",
                "BOUNDED_REDUCE_ONLY_EXIT" if expired else "RESTORE_STOP",
            ),
            covered,
        )

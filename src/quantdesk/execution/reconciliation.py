"""Bounded account-writer recovery. Snapshots are evidence, never ledger setters.

Only normalized economic inputs use the existing execution/accounting reducer.
Recovery keeps the entry latch set even after convergence; operator/risk resume
is a separate authority. The durable cursor is a committed fact, not a socket
callback or a REST pagination token.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol, cast

from quantdesk.core.checkpoint import EngineState
from quantdesk.core.engine import Engine
from quantdesk.core.events import (
    PAYLOAD_TYPES,
    AccountSnapshotObserved,
    CashTransfer,
    Envelope,
    IncomingEvent,
    OrderInstruction,
    RiskLatchChanged,
    canonical_bytes,
)
from quantdesk.core.reducers import FactReducer, Reduction, Stage, _decode_value, decode_payload
from quantdesk.core.types import EventPayload
from quantdesk.execution.order_state import TERMINAL, Knowledge, OMSState
from quantdesk.execution.protection import ProtectionReview, protection_ownership
from quantdesk.persistence.event_store import ProjectionUpdate
from quantdesk.portfolio.events import FinancialEventObserved
from quantdesk.portfolio.ledger import LedgerState
from quantdesk.portfolio.reconciliation import Reconciler as PortfolioComparison


@dataclass(frozen=True, slots=True)
class RecoveryWindow:
    facts: tuple[IncomingEvent, ...]
    snapshot_event: IncomingEvent
    snapshot: AccountSnapshotObserved
    open_client_ids: tuple[str, ...]
    protected_instruments: tuple[str, ...]
    reasons: tuple[str, ...]
    start_receive_seq: int
    end_receive_seq: int
    through_ms: int
    observation_ids: tuple[str, ...]
    fingerprint: str


class RecoveryBoundary(Protocol):
    async def connect_private(self) -> None: ...
    def drain_private(self) -> tuple[IncomingEvent, ...]: ...
    def private_healthy(self) -> bool: ...
    async def fetch_recovery_window(
        self, start_ms: int, end_ms: int, original_orders: tuple[OrderInstruction, ...]
    ) -> RecoveryWindow: ...


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RecoveryProgress(EventPayload):
    status: str
    cursor_ms: int | None
    observation_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    observations: int

    @property
    def converged(self) -> bool:
        return self.status == "CONVERGED"


type ReconciliationReport = RecoveryProgress


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OrderContractObserved(EventPayload):
    client_order_id: str
    venue_order_id: str | None
    instruction_id: str | None
    observed: tuple[tuple[str, str | int | bool | None], ...]
    expected: tuple[tuple[str, str | int | bool | None], ...]
    discrepancies: tuple[str, ...]


def order_contract_terms(instruction: OrderInstruction) -> dict[str, str | int | bool | None]:
    return {
        "instrument_id": instruction.instrument_id,
        "quantity_lots": instruction.quantity_lots,
        "price_ticks": instruction.price_ticks,
        "side": instruction.side.value,
        "order_type": instruction.order_type.value,
        "time_in_force": instruction.time_in_force.value,
        "reduce_only": instruction.reduce_only,
        "margin_mode": "isolated",
        "position_mode": "one_way",
    }


def recovery_reducer() -> FactReducer:
    def apply(event: Envelope, state: EngineState) -> Reduction:
        value = decode_payload(event)
        if isinstance(value, OrderContractObserved):
            orders = OMSState.from_bytes(state.get("oms", "state-v1"))
            order = next(
                (
                    order
                    for order in orders.orders
                    if order.instruction.client_order_id == value.client_order_id
                ),
                None,
            )
            expected = order_contract_terms(order.instruction) if order else {}
            discrepancies = set(value.discrepancies)
            if order is None:
                discrepancies.add("UNKNOWN_CLIENT")
            else:
                observed = dict(value.observed)
                discrepancies.update(
                    key
                    for key, expected_value in expected.items()
                    if observed.get(key) != expected_value
                )
                if value.instruction_id not in {None, order.instruction.instruction_id} or (
                    value.expected and dict(value.expected) != expected
                ):
                    discrepancies.add("UNKNOWN_CONTRACT_REVISION")
                if (
                    order.venue_order_id is not None
                    and value.venue_order_id != order.venue_order_id
                ):
                    discrepancies.add("VENUE_ORDER_ID")
            evidence = replace(
                value,
                expected=tuple(sorted(expected.items())),
                discrepancies=tuple(sorted(discrepancies)),
            )
            prior = json.loads(state.get("oms", "order-contract-conflicts-v1", b"[]"))
            if discrepancies:
                prior = sorted(set((*prior, value.client_order_id)))
            return Reduction(
                state.put("oms", "order-contract-conflicts-v1", canonical_bytes(prior)),
                projection_updates=(
                    ProjectionUpdate(
                        "reconciliation_runs", event.event_id, canonical_bytes(evidence)
                    ),
                ),
            )
        if not isinstance(value, RecoveryProgress):
            return Reduction(state)
        if value.status not in {"RECONCILING", "BLOCKED", "CONVERGED"}:
            raise ValueError("invalid recovery state")
        prior = state.get("oms", "recovery-v1", b"")
        prior_cursor = json.loads(prior)["cursor_ms"] if prior else None
        if prior_cursor is not None and (value.cursor_ms is None or value.cursor_ms < prior_cursor):
            raise ValueError("durable recovery cursor cannot regress")
        if value.status != "CONVERGED" and value.cursor_ms != prior_cursor:
            raise ValueError("only convergence may advance recovery cursor")
        data = canonical_bytes(value)
        return Reduction(
            state.put("oms", "recovery-v1", data),
            projection_updates=(ProjectionUpdate("reconciliation_runs", event.event_id, data),),
        )

    return FactReducer("recovery-v1", Stage.BOOK_ACCOUNT_OMS, apply)


class Reconciler:
    def __init__(
        self,
        engine: Engine,
        boundary: RecoveryBoundary,
        make_input: Callable[[EventPayload], IncomingEvent],
        *,
        clock: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_ns: int = 30_000_000_000,
        overlap_ms: int = 60_000,
        settlement_rounding_unit: Decimal = Decimal("0.00000001"),
    ) -> None:
        if not 1_000_000_000 <= timeout_ns <= 30_000_000_000 or overlap_ms < 1:
            raise ValueError("invalid bounded recovery policy")
        self.engine, self.boundary, self.make_input = engine, boundary, make_input
        self.clock, self.sleep, self.timeout_ns, self.overlap_ms = (
            clock,
            sleep,
            timeout_ns,
            overlap_ms,
        )
        self.rounding = settlement_rounding_unit

    def commit(self, event: IncomingEvent) -> None:
        # Delayed buffered facts retain exchange/receipt clocks, but become
        # available only now, in this single writer's actual processing order.
        event = replace(event, available_ns=max(event.available_ns, self.engine.state.available_ns))
        self.engine.commit(self.engine.process(event))

    def record(self, payload: EventPayload) -> None:
        self.commit(self.make_input(payload))

    def portfolio(self) -> LedgerState:
        return LedgerState.from_bytes(self.engine.state.get("ledger", "state-v1"))

    def orders(self) -> OMSState:
        return OMSState.from_bytes(self.engine.state.get("oms", "state-v1"))

    async def run(
        self, *, start_ms: int | None = None, opening: bool = False
    ) -> ReconciliationReport:
        began = self.clock()
        old = self.engine.state.get("oms", "recovery-v1", b"")
        cursor = cast(int | None, json.loads(old)["cursor_ms"]) if old else None
        self.record(
            RiskLatchChanged("reconciliation", "account", True, "RECOVERING", "recovery-v1")
        )
        self.record(RecoveryProgress("RECONCILING", cursor, (), (), 0))
        if opening and (self.portfolio().transactions or self.orders().orders):
            # Restart may already contain the audited baseline; never book it again.
            opening = False
        since = cursor if cursor is not None else start_ms
        if since is None and not opening:
            result = RecoveryProgress("BLOCKED", cursor, (), ("HISTORY_START_UNAVAILABLE",), 0)
            self.record(result)
            return result
        since = began // 1_000_000 if opening else max(0, cast(int, since) - self.overlap_ms)
        observation_ids: list[str] = []
        previous: str | None = None
        stable_at = 0
        observations = 0
        reasons: tuple[str, ...] = ()
        try:
            async with asyncio.timeout(self.timeout_ns / 1_000_000_000):
                await self.boundary.connect_private()
            while self.clock() - began < self.timeout_ns:
                end_ms = self.clock() // 1_000_000
                remaining = max(1, self.timeout_ns - (self.clock() - began))
                async with asyncio.timeout(remaining / 1_000_000_000):
                    window = await self.boundary.fetch_recovery_window(
                        since, end_ms, tuple(o.instruction for o in self.orders().orders)
                    )
                observations += 1
                observation_ids.extend(window.observation_ids)
                issues = set(window.reasons)
                if self.clock() - began > self.timeout_ns:
                    issues.add("CONVERGENCE_TIMEOUT")
                for event in (*window.facts, *self.boundary.drain_private()):
                    self.commit(event)
                    if event.event_type == "DataGap":
                        issues.add("PRIVATE_STREAM_GAP")
                self.commit(window.snapshot_event)
                if not self.boundary.private_healthy():
                    issues.add("PRIVATE_STREAM_UNHEALTHY")
                portfolio, orders = self.portfolio(), self.orders()
                if json.loads(self.engine.state.get("oms", "order-contract-conflicts-v1", b"[]")):
                    issues.add("ORDER_CONTRACT_CONFLICT")
                for _, projection in self.engine.state.protection:
                    protection = json.loads(projection)["state"]
                    if protection["desired_lots"] and protection["status"] != "PROTECTED":
                        issues.add("PROTECTION_UNVERIFIED")
                    if protection["status"] in {
                        "OWNERSHIP_UNVERIFIED",
                        "INACTIVE_PENDING_PROTECTION",
                    }:
                        issues.add("PROTECTION_OWNERSHIP_UNVERIFIED")
                for position in portfolio.positions:
                    if position.signed_lots and not any(
                        owner.instrument_id == position.instrument_id
                        and owner.group_id
                        and owner.signed_lots == position.signed_lots
                        for owner in protection_ownership(self.engine.state)
                    ):
                        issues.add("PROTECTION_OWNERSHIP_UNVERIFIED")
                known = {o.instruction.client_order_id for o in orders.orders}
                if set(window.open_client_ids) - known:
                    issues.add("FOREIGN_ORDER")
                active = {
                    order.instruction.client_order_id
                    for order in orders.orders
                    if order.transport_started and order.lifecycle not in TERMINAL
                }
                if active != set(window.open_client_ids):
                    issues.add("ACTIVE_ORDER_SET_MISMATCH")
                if any(o.knowledge != Knowledge.CONFIRMED for o in orders.orders):
                    issues.add("MISSING_EXECUTION_HISTORY")
                if any(e.client_order_id not in known for e in orders.executions):
                    issues.add("FOREIGN_EXECUTION")
                issues.update(orders.incidents)
                if any(
                    lots and instrument not in window.protected_instruments
                    for instrument, lots in window.snapshot.positions
                ):
                    issues.add("PROTECTION_UNVERIFIED")
                if opening:
                    if portfolio.transactions or orders.orders:
                        issues.add("OPENING_ACTIVITY_REQUIRES_REVIEW")
                    if any(lots for _, lots in window.snapshot.positions) or window.open_client_ids:
                        issues.add("OPENING_NOT_FLAT")
                    if (
                        len(window.snapshot.wallet_balances) != 1
                        or window.snapshot.wallet_balances[0][0] != "USDT"
                        or window.snapshot.wallet_balances[0][1] < 0
                    ):
                        issues.add("OPENING_COLLATERAL_UNSUPPORTED")
                else:
                    comparison = PortfolioComparison.compare(
                        portfolio,
                        window.snapshot,
                        window.start_receive_seq,
                        window.end_receive_seq,
                        self.rounding,
                    )
                    self.record(comparison.observation)
                    for discrepancy in comparison.position_discrepancies:
                        self.record(discrepancy)
                    issues.update(comparison.observation.discrepancies)
                reasons = tuple(sorted(issues))
                if (
                    not issues
                    and previous == window.fingerprint
                    and self.clock() - stable_at >= 1_000_000_000
                ):
                    # Only matching, stable REST/ledger evidence can confirm
                    # flat. Existing native stops still require cleanup; do not
                    # advance the account cursor while those orders remain.
                    stale = False
                    if not window.open_client_ids:
                        for fact in window.facts:
                            if fact.event_type != "ProtectionReview":
                                continue
                            review = cast(
                                ProtectionReview,
                                _decode_value(json.loads(fact.payload), ProtectionReview),
                            )
                            if not portfolio.position(cast(str, fact.instrument_id)).signed_lots:
                                self.commit(
                                    replace(
                                        fact,
                                        payload=canonical_bytes(
                                            replace(review, verified_flat=True)
                                        ),
                                    )
                                )
                                stale |= any(
                                    native.group_id == review.group_id
                                    and native.status == "pending"
                                    for native in review.native_orders
                                )
                    if stale:
                        result = RecoveryProgress(
                            "BLOCKED",
                            cursor,
                            tuple(observation_ids),
                            ("STALE_PROTECTION",),
                            observations,
                        )
                        self.record(result)
                        return result
                    if opening:
                        amount = window.snapshot.wallet_balances[0][1]
                        if amount:
                            self.record(
                                FinancialEventObserved(
                                    CashTransfer(
                                        "opening-flat-v1", amount, "USDT", "IN", self.clock()
                                    ),
                                    (),
                                    tuple(observation_ids),
                                    False,
                                )
                            )
                    result = RecoveryProgress(
                        "CONVERGED", window.through_ms, tuple(observation_ids), (), observations
                    )
                    self.record(result)
                    return result
                previous = window.fingerprint if not issues else None
                stable_at = self.clock()
                await self.sleep(1)
            reasons = tuple(sorted(set((*reasons, "CONVERGENCE_TIMEOUT"))))
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            # Payload contents/credentials never enter incident messages.
            reasons = tuple(sorted(set((*reasons, f"RECOVERY_INPUT_FAILURE:{type(exc).__name__}"))))
        result = RecoveryProgress("BLOCKED", cursor, tuple(observation_ids), reasons, observations)
        self.record(result)
        return result

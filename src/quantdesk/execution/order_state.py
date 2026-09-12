"""Independent lifecycle, pending action, knowledge, and economic evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import cast

from quantdesk.core.events import OrderInstruction, canonical_bytes
from quantdesk.core.reducers import _decode_value
from quantdesk.core.types import Side
from quantdesk.execution.intents import GatewayInstruction


class Lifecycle(StrEnum):
    CREATED = "CREATED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PendingAction(StrEnum):
    NONE = "NONE"
    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    AMEND = "AMEND"


class Knowledge(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNCERTAIN = "UNCERTAIN"
    RECONCILING = "RECONCILING"


TERMINAL = frozenset({Lifecycle.FILLED, Lifecycle.CANCELED, Lifecycle.REJECTED, Lifecycle.EXPIRED})


@dataclass(frozen=True, slots=True)
class OrderState:
    instruction: OrderInstruction
    cash_reserve: Decimal
    fee_reserve: Decimal
    venue_order_id: str | None = None
    lifecycle: Lifecycle = Lifecycle.CREATED
    pending: PendingAction = PendingAction.SUBMIT
    knowledge: Knowledge = Knowledge.CONFIRMED
    reported_fill_lots: int = 0
    accounted_fill_lots: int = 0
    revision: int = 1
    transport_started: bool = False

    @property
    def remaining_lots(self) -> int:
        return max(0, self.instruction.quantity_lots - self.accounted_fill_lots)

    @property
    def reserved_lots(self) -> int:
        if self.lifecycle not in TERMINAL:
            return max(self.remaining_lots, self.unresolved_fill_lots)
        return self.unresolved_fill_lots

    @property
    def unresolved_fill_lots(self) -> int:
        expected = self.reported_fill_lots
        if self.lifecycle == Lifecycle.FILLED:
            expected = max(expected, self.instruction.quantity_lots)
        return max(0, expected - self.accounted_fill_lots)

    @property
    def canceled_remainder_lots(self) -> int:
        if self.lifecycle not in {Lifecycle.CANCELED, Lifecycle.EXPIRED, Lifecycle.REJECTED}:
            return 0
        return max(
            0,
            self.instruction.quantity_lots - max(self.reported_fill_lots, self.accounted_fill_lots),
        )


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    instrument_id: str
    native_ids: tuple[str, ...]
    client_order_id: str | None
    venue_order_id: str | None
    side: Side
    lots: int
    signature: str


@dataclass(frozen=True, slots=True)
class CommandState:
    instruction: GatewayInstruction
    status: str = "PENDING"
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class OMSState:
    venue: str
    environment: str
    account: str
    orders: tuple[OrderState, ...] = ()
    executions: tuple[ExecutionIdentity, ...] = ()
    commands: tuple[CommandState, ...] = ()
    incidents: tuple[str, ...] = ()
    symbol_owners: tuple[tuple[str, str], ...] = ()

    def order(self, client_order_id: str) -> OrderState:
        return next(o for o in self.orders if o.instruction.client_order_id == client_order_id)

    @property
    def unmatched_executions(self) -> tuple[ExecutionIdentity, ...]:
        return tuple(
            e
            for e in self.executions
            if e.client_order_id is None
            or not any(o.instruction.client_order_id == e.client_order_id for o in self.orders)
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> OMSState:
        state = cast(OMSState, _decode_value(json.loads(data), cls))
        if state.to_bytes() != data:
            raise ValueError("noncanonical OMS checkpoint")
        return state

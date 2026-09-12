"""Canonical account-writer decisions and gateway observations."""

from dataclasses import dataclass
from decimal import Decimal

from quantdesk.core.events import PAYLOAD_TYPES, CancelTransportResult, OrderInstruction
from quantdesk.core.types import EventPayload
from quantdesk.portfolio.arithmetic import money


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OrderApproved(EventPayload):
    instruction: OrderInstruction
    cash_reserve: Decimal
    fee_reserve: Decimal

    def __post_init__(self) -> None:
        if money(self.cash_reserve) < 0 or money(self.fee_reserve) < 0:
            raise ValueError("negative order encumbrance")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CancelRequested(EventPayload):
    instruction_id: str
    client_order_id: str
    expires_at_ns: int
    risk_version: str
    gateway_fence: str

    def __post_init__(self) -> None:
        if not all(
            (self.instruction_id, self.client_order_id, self.risk_version, self.gateway_fence)
        ):
            raise ValueError("cancel requires identity, risk version, and fence")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CancelAttemptObserved(EventPayload):
    instruction_id: str
    result: CancelTransportResult


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class DispatchStarted(EventPayload):
    instruction_id: str
    attempt_id: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class UnsentAborted(EventPayload):
    instruction_id: str
    reason: str


type GatewayInstruction = OrderInstruction | CancelRequested

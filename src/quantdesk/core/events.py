from __future__ import annotations

import base64
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import TypeVar

from quantdesk.core.types import (
    BookLevel,
    DecimalBalances,
    EventPayload,
    ExecutionType,
    IntentAction,
    OrderType,
    PositionLots,
    Side,
    TimeInForce,
    VenueExtensions,
)

_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_JSON_INTEGER:
            return str(value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical Decimal values must be finite")
        return str(value)
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON mapping keys must be strings")
            result[key] = _canonical_value(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical_value(child) for child in value]
    raise TypeError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """Serialize supported values to deterministic UTF-8 JSON bytes."""

    normalized = _canonical_value(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


PayloadT = TypeVar("PayloadT", bound=EventPayload)


class PayloadTypeRegistry(Mapping[str, type[EventPayload]]):
    def __init__(self) -> None:
        self._types: dict[str, type[EventPayload]] = {}

    def register(self, payload_type: type[PayloadT]) -> type[PayloadT]:
        name = payload_type.__name__
        if name in self._types:
            raise ValueError(f"payload type is already registered: {name}")
        self._types[name] = payload_type
        return payload_type

    def resolve(self, event_type: str) -> type[EventPayload]:
        try:
            return self._types[event_type]
        except KeyError as exc:
            raise KeyError(f"unknown payload type: {event_type}") from exc

    def __getitem__(self, key: str) -> type[EventPayload]:
        return self._types[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._types)

    def __len__(self) -> int:
        return len(self._types)


PAYLOAD_TYPES = PayloadTypeRegistry()


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BookSnapshot(EventPayload):
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    native_sequence: str | None
    checksum: str | None
    venue_extensions: VenueExtensions


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BookDelta(EventPayload):
    bid_updates: tuple[BookLevel, ...]
    ask_updates: tuple[BookLevel, ...]
    native_sequence: str | None
    prior_sequence: str | None
    checksum: str | None
    venue_extensions: VenueExtensions


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class Trade(EventPayload):
    native_trade_id: str
    price_ticks: int
    size_lots: int
    aggressor_side: Side | None
    venue_extensions: VenueExtensions

    def __post_init__(self) -> None:
        if not self.native_trade_id:
            raise ValueError("native_trade_id must be non-empty")
        if self.price_ticks <= 0:
            raise ValueError("price_ticks must be positive")
        if self.size_lots <= 0:
            raise ValueError("size_lots must be positive")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class Quote(EventPayload):
    bid_price_ticks: int
    bid_size_lots: int
    ask_price_ticks: int
    ask_size_lots: int
    native_sequence: str | None
    venue_extensions: VenueExtensions

    def __post_init__(self) -> None:
        if self.bid_price_ticks <= 0 or self.ask_price_ticks <= 0:
            raise ValueError("quote price ticks must be positive")
        if self.bid_size_lots < 0 or self.ask_size_lots < 0:
            raise ValueError("quote size lots must be non-negative")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BarClosed(EventPayload):
    start_ns: int
    end_ns: int
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume_lots: int
    synthetic: bool

    def __post_init__(self) -> None:
        if self.end_ns <= self.start_ns:
            raise ValueError("bar end_ns must be after start_ns")
        prices = (self.open_ticks, self.high_ticks, self.low_ticks, self.close_ticks)
        if any(price <= 0 for price in prices):
            raise ValueError("bar price ticks must be positive")
        if self.low_ticks > min(self.open_ticks, self.close_ticks):
            raise ValueError("bar low_ticks exceeds open or close")
        if self.high_ticks < max(self.open_ticks, self.close_ticks):
            raise ValueError("bar high_ticks is below open or close")
        if self.volume_lots < 0:
            raise ValueError("bar volume_lots must be non-negative")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class MarkPrice(EventPayload):
    price: Decimal
    event_ns: int
    source: str

    def __post_init__(self) -> None:
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("mark price must be positive and finite")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FundingRateAnnounced(EventPayload):
    rate: Decimal
    settlement_ns: int

    def __post_init__(self) -> None:
        if not self.rate.is_finite():
            raise ValueError("funding rate must be finite")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class InstrumentSpecUpdated(EventPayload):
    instrument_id: str
    valid_from_ns: int
    known_from_ns: int
    revision_hash: str
    tick_size: Decimal
    quantity_step: Decimal
    contract_multiplier: Decimal
    base_unit: str
    quote_unit: str
    settlement_unit: str
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal
    trading_status: str
    min_leverage: Decimal
    max_leverage: Decimal
    supported_margin_modes: tuple[str, ...]
    supported_position_modes: tuple[str, ...]
    funding_schedule: tuple[str, ...]
    venue_extensions: VenueExtensions

    def __post_init__(self) -> None:
        positive_values = {
            "tick_size": self.tick_size,
            "quantity_step": self.quantity_step,
            "contract_multiplier": self.contract_multiplier,
            "min_quantity": self.min_quantity,
            "max_quantity": self.max_quantity,
            "min_notional": self.min_notional,
            "min_leverage": self.min_leverage,
            "max_leverage": self.max_leverage,
        }
        for name, value in positive_values.items():
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.max_quantity < self.min_quantity:
            raise ValueError("max_quantity must be at least min_quantity")
        if self.max_leverage < self.min_leverage:
            raise ValueError("max_leverage must be at least min_leverage")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ConnectionChanged(EventPayload):
    channel: str
    connected: bool
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BookValidityChanged(EventPayload):
    validity_epoch: str
    state: str
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class DataGap(EventPayload):
    channel: str
    start_sequence: str | None
    end_sequence: str | None
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ClockAdjusted(EventPayload):
    prior_available_ns: int
    next_available_ns: int
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RecorderHealthChanged(EventPayload):
    healthy: bool
    durable_watermark: str | None
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FeatureSnapshot(EventPayload):
    snapshot_id: str
    decision_seq: int
    available_ns: int
    config_hash: str
    schema_hash: str
    feature_names: tuple[str, ...]
    feature_values: tuple[Decimal | None, ...]
    source_watermark: int

    def __post_init__(self) -> None:
        if len(self.feature_names) != len(self.feature_values):
            raise ValueError("feature_names and feature_values must have equal length")
        if any(value is not None and not value.is_finite() for value in self.feature_values):
            raise ValueError("feature values must be finite or None")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class StrategyIntent(EventPayload):
    intent_id: str
    strategy_id: str
    instrument_id: str
    decision_seq: int
    feature_snapshot_id: str
    config_hash: str
    model_hash_or_none: str | None
    action: IntentAction
    side: Side
    desired_quantity: Decimal | None
    risk_budget: Decimal | None
    price_policy: str
    expires_at_ns: int
    stop_policy: str
    reason: str

    def __post_init__(self) -> None:
        for name, value in (
            ("desired_quantity", self.desired_quantity),
            ("risk_budget", self.risk_budget),
        ):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError(f"{name} must be positive and finite when provided")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RiskDecision(EventPayload):
    intent_id: str
    approved: bool
    reason_code: str
    approved_quantity_lots: int
    risk_version: str

    def __post_init__(self) -> None:
        if self.approved_quantity_lots < 0:
            raise ValueError("approved_quantity_lots must be non-negative")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class IntentRejected(EventPayload):
    intent_id: str
    reason_code: str
    message: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OrderInstruction(EventPayload):
    instruction_id: str
    client_order_id: str
    parent_intent_id: str
    account_id: str
    environment: str
    instrument_id: str
    side: Side
    quantity_lots: int
    price_ticks: int | None
    order_type: OrderType
    time_in_force: TimeInForce
    reduce_only: bool
    native_trigger_basis: str | None
    native_trigger_value: Decimal | None
    owner_strategy_id: str
    protection_group_id: str | None
    expires_at_ns: int
    risk_version: str
    gateway_fence: str

    def __post_init__(self) -> None:
        if self.quantity_lots <= 0:
            raise ValueError("quantity_lots must be positive")
        if self.price_ticks is not None and self.price_ticks <= 0:
            raise ValueError("price_ticks must be positive when provided")
        if self.native_trigger_value is not None and (
            not self.native_trigger_value.is_finite() or self.native_trigger_value <= 0
        ):
            raise ValueError("native_trigger_value must be positive and finite when provided")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class SubmitTransportResult(EventPayload):
    instruction_id: str
    accepted: bool
    venue_order_id: str | None
    error_code: str | None


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OrderReport(EventPayload):
    client_order_id: str
    venue_order_id: str | None
    lifecycle: str
    cumulative_fill_lots: int
    event_ns: int

    def __post_init__(self) -> None:
        if self.cumulative_fill_lots < 0:
            raise ValueError("cumulative_fill_lots must be non-negative")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ExecutionReport(EventPayload):
    native_execution_id: str
    client_order_id: str | None
    venue_order_id: str | None
    side: Side
    executed_lots: int
    price: Decimal
    event_ns: int
    receipt_ns: int
    fee_amount: Decimal
    fee_currency: str
    fee_rate: Decimal | None
    maker: bool | None
    execution_type: ExecutionType
    native_realized_pnl: Decimal | None

    def __post_init__(self) -> None:
        if not self.native_execution_id:
            raise ValueError("native_execution_id must be non-empty")
        if self.executed_lots <= 0:
            raise ValueError("executed_lots must be positive")
        decimal_values = (self.price, self.fee_amount, self.fee_rate, self.native_realized_pnl)
        if any(value is not None and not value.is_finite() for value in decimal_values):
            raise ValueError("execution Decimal values must be finite")
        if self.price <= 0:
            raise ValueError("execution price must be positive")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CancelTransportResult(EventPayload):
    client_order_id: str
    accepted: bool
    error_code: str | None


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ProtectionReport(EventPayload):
    protection_group_id: str
    status: str
    protected_lots: int
    observed_ns: int
    reason: str | None

    def __post_init__(self) -> None:
        if self.protected_lots < 0:
            raise ValueError("protected_lots must be non-negative")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ReconciliationObservation(EventPayload):
    observation_id: str
    start_receive_seq: int
    end_receive_seq: int
    converged: bool
    discrepancies: tuple[str, ...]


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FundingSettlement(EventPayload):
    native_transaction_id: str
    instrument_id: str
    amount: Decimal
    asset: str
    settlement_ns: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FeeAdjustment(EventPayload):
    native_transaction_id: str
    native_execution_id: str
    amount: Decimal
    asset: str
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CashTransfer(EventPayload):
    native_transaction_id: str
    amount: Decimal
    asset: str
    direction: str
    event_ns: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class AccountSnapshotObserved(EventPayload):
    observation_id: str
    wallet_balances: DecimalBalances
    positions: PositionLots
    observed_ns: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class LedgerAdjustmentApproved(EventPayload):
    adjustment_id: str
    amount: Decimal
    asset: str
    reason: str
    author: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class PositionDiscrepancy(EventPayload):
    instrument_id: str
    expected_lots: int
    observed_lots: int
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class TimerFired(EventPayload):
    timer_id: str
    due_ns: int
    scheduled_by_event_id: str
    actual_available_ns: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OperatorCommand(EventPayload):
    command_id: str
    command_type: str
    target: tuple[tuple[str, str], ...]
    body_hash: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CommandResult(EventPayload):
    command_id: str
    state: str
    reason: str | None
    applied_seq: int | None


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RiskLatchChanged(EventPayload):
    latch_id: str
    scope: str
    active: bool
    reason: str
    risk_version: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ModelActivated(EventPayload):
    strategy_id: str
    prior_model_hash: str | None
    model_hash: str
    activation_seq: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ConfigActivated(EventPayload):
    prior_config_hash: str | None
    config_hash: str
    activation_seq: int


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class SimulatedLiquidationTriggered(EventPayload):
    instrument_id: str
    position_lots: int
    mark_price: Decimal
    reason: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CheckpointWritten(EventPayload):
    checkpoint_id: str
    engine_seq: int
    deterministic_state_hash: str


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RunBoundary(EventPayload):
    run_id: str
    boundary: str
    available_ns: int


@dataclass(frozen=True, slots=True)
class IncomingEvent:
    event_type: str
    schema_version: int
    run_id: str
    account_id: str | None
    venue: str | None
    environment: str
    instrument_id: str | None
    source_channel: str
    connection_epoch: str
    source_message_id: str | None
    source_sequence: str | None
    exchange_event_ns: int | None
    exchange_transaction_ns: int | None
    receive_wall_ns: int
    receive_monotonic_ns: int
    available_ns: int
    causation_id: str | None
    correlation_id: str
    raw_ref: str | None
    producer_version: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class Envelope(IncomingEvent):
    event_id: str
    engine_seq: int

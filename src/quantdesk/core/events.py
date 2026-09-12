from __future__ import annotations

import base64
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import TypeVar

from quantdesk.core.types import EventPayload

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
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BookDelta(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class Trade(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class Quote(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BarClosed(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class MarkPrice(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FundingRateAnnounced(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class InstrumentSpecUpdated(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ConnectionChanged(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class BookValidityChanged(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class DataGap(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ClockAdjusted(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RecorderHealthChanged(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FeatureSnapshot(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class StrategyIntent(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RiskDecision(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class IntentRejected(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OrderInstruction(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class SubmitTransportResult(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OrderReport(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ExecutionReport(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CancelTransportResult(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ProtectionReport(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ReconciliationObservation(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FundingSettlement(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FeeAdjustment(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CashTransfer(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class AccountSnapshotObserved(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class LedgerAdjustmentApproved(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class PositionDiscrepancy(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class TimerFired(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class OperatorCommand(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CommandResult(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RiskLatchChanged(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ModelActivated(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ConfigActivated(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class SimulatedLiquidationTriggered(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class CheckpointWritten(EventPayload):
    pass


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class RunBoundary(EventPayload):
    pass


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

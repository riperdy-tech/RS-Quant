from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

type CanonicalScalar = bool | int | float | str | Decimal | None
type PayloadField = tuple[str, CanonicalScalar]
type VenueExtensions = tuple[PayloadField, ...]
type DecimalBalances = tuple[tuple[str, Decimal], ...]
type PositionLots = tuple[tuple[str, int], ...]


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class IntentAction(StrEnum):
    ENTER = "ENTER"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    CANCEL_ENTRY = "CANCEL_ENTRY"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "POST_ONLY"


class ExecutionType(StrEnum):
    TRADE = "TRADE"
    FUNDING = "FUNDING"
    SETTLEMENT = "SETTLEMENT"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price_ticks: int
    size_lots: int

    def __post_init__(self) -> None:
        if self.price_ticks <= 0:
            raise ValueError("price_ticks must be positive")
        if self.size_lots < 0:
            raise ValueError("size_lots must be non-negative")


@dataclass(frozen=True, slots=True)
class EventPayload:
    """Marker base for explicitly structured immutable event payloads."""

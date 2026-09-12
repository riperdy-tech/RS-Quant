"""Additional canonical accounting observations and encumbrance revisions."""

from dataclasses import dataclass
from decimal import Decimal

from quantdesk.core.events import (
    PAYLOAD_TYPES,
    CashTransfer,
    ExecutionReport,
    FeeAdjustment,
    FundingSettlement,
    LedgerAdjustmentApproved,
)
from quantdesk.core.types import EventPayload, Side
from quantdesk.portfolio.arithmetic import money

type FinancialPayload = (
    ExecutionReport | CashTransfer | FundingSettlement | FeeAdjustment | LedgerAdjustmentApproved
)


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class FinancialEventObserved(EventPayload):
    financial_payload: FinancialPayload
    aliases: tuple[str, ...]
    source_observation_ids: tuple[str, ...]
    trading_adjustment: bool

    def __post_init__(self) -> None:
        if type(self.trading_adjustment) is not bool:
            raise TypeError("trading adjustment flag must be boolean")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ReservationChanged(EventPayload):
    reservation_id: str
    instrument_id: str
    side: Side
    remaining_lots: int
    cash_amount: Decimal
    fee_buffer: Decimal
    reduce_only: bool
    revision: int

    def __post_init__(self) -> None:
        if not self.reservation_id or not self.instrument_id or not isinstance(self.side, Side):
            raise ValueError("reservation identity/instrument/side required")
        if type(self.remaining_lots) is not int or self.remaining_lots < 0:
            raise ValueError("remaining lots must be a nonnegative integer")
        if (
            type(self.revision) is not int
            or self.revision < 1
            or type(self.reduce_only) is not bool
        ):
            raise ValueError("invalid reservation revision or reduce-only flag")
        if money(self.cash_amount) < 0 or money(self.fee_buffer) < 0:
            raise ValueError("negative reservation")
        if self.remaining_lots == 0 and (self.cash_amount or self.fee_buffer):
            raise ValueError("released reservation must have zero encumbrance")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ReservationBatchChanged(EventPayload):
    instrument_id: str
    reservations: tuple[ReservationChanged, ...]

    def __post_init__(self) -> None:
        if not self.reservations or any(
            r.instrument_id != self.instrument_id for r in self.reservations
        ):
            raise ValueError("reservation batch requires one instrument")
        if len({r.reservation_id for r in self.reservations}) != len(self.reservations):
            raise ValueError("duplicate reservation in atomic batch")


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class ConversionRateObserved(EventPayload):
    base_asset: str
    quote_asset: str
    rate: Decimal
    event_ns: int
    source: str

    def __post_init__(self) -> None:
        if not self.base_asset or self.quote_asset != "USDT" or not self.source:
            raise ValueError("conversion source and USDT quote required")
        if self.base_asset == "USDT" or money(self.rate) <= 0:
            raise ValueError("conversion must be positive and for a foreign asset")
        if type(self.event_ns) is not int or self.event_ns < 0:
            raise ValueError("conversion timestamp invalid")

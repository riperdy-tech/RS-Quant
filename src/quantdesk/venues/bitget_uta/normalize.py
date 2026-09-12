"""Exact V3 wire mappings. Unknown economics remain observations and block recovery."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quantdesk.core.events import (
    PAYLOAD_TYPES,
    BookDelta,
    BookSnapshot,
    CashTransfer,
    ExecutionReport,
    FundingSettlement,
    OrderReport,
)
from quantdesk.core.types import BookLevel, EventPayload, ExecutionType, Side
from quantdesk.venues.instruments import InstrumentSpec, exact


def integer(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("nonnegative exact integer required")


def timestamp(value: object) -> int:
    return integer(value) * 1_000_000


def decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise TypeError("financial wire value must be a decimal string")
    return exact(value)


def identifier(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("native identifier must be a nonempty string")
    return value


def instrument(data: Mapping[str, Any], known_ns: int) -> InstrumentSpec:
    if (
        data["category"].upper() != "USDT-FUTURES"
        or data["quoteCoin"] != "USDT"
        or data["type"] != "perpetual"
    ):
        raise ValueError("unsupported instrument product")
    tick, step = decimal(data["priceMultiplier"]), decimal(data["quantityMultiplier"])
    if int(str(tick.as_tuple().exponent)) < -integer(data["pricePrecision"]) or int(
        str(step.as_tuple().exponent)
    ) < -integer(data["quantityPrecision"]):
        raise ValueError("multipliers conflict with precision")
    return InstrumentSpec.create(
        instrument_id=f"bitget:USDT-FUTURES:{data['baseCoin']}:USDT:USDT:{data['symbol']}",
        tick_size=tick,
        quantity_step=step,
        valid_from_ns=known_ns,
        known_from_ns=known_ns,
        min_quantity=decimal(data["minOrderQty"]),
        max_quantity=decimal(data["maxOrderQty"]),
        min_notional=decimal(data["minOrderAmount"]),
        trading_status="TRADING" if data["status"] == "online" else str(data["status"]),
        min_leverage=decimal(data["minLeverage"]),
        max_leverage=decimal(data["maxLeverage"]),
        funding_schedule=(f"interval_hours:{integer(data['fundInterval'])}",),
    )


@PAYLOAD_TYPES.register
@dataclass(frozen=True, slots=True)
class VenueObservation(EventPayload):
    """Non-economic metadata; raw JSON preserves unsupported fields for review."""

    kind: str
    native_id: str
    json_data: str
    reason: str | None = None


class Normalizer:
    def __init__(self, specs: tuple[InstrumentSpec, ...]) -> None:
        self.specs = {s.instrument_id.split(":")[-1]: s for s in specs}

    def spec(self, row: Mapping[str, Any]) -> InstrumentSpec:
        if str(row["category"]).upper() != "USDT-FUTURES":
            raise ValueError("unsupported observed product")
        return self.specs[identifier(row["symbol"])]

    def order(self, row: Mapping[str, Any]) -> OrderReport:
        spec = self.spec(row)
        statuses = {
            "live": "CREATED",
            "new": "OPEN",
            "partially_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "cancelled": "CANCELED",
            "canceled": "CANCELED",
        }
        if row["orderStatus"] not in statuses:
            raise ValueError("unknown order status")
        return OrderReport(
            identifier(row["clientOid"]),
            identifier(row["orderId"]),
            statuses[row["orderStatus"]],
            spec.quantity_to_lots(decimal(row["cumExecQty"])),
            timestamp(row["updatedTime"]),
        )

    def fill(self, row: Mapping[str, Any], *, receipt_ns: int) -> ExecutionReport:
        spec = self.spec(row)
        fees = row["feeDetail"]
        if not isinstance(fees, list) or len(fees) != 1:
            raise ValueError("one explicitly reported fee currency required")
        fee = fees[0]
        scope = row.get("tradeScope")
        if scope not in {"maker", "taker"}:
            raise ValueError("unknown liquidity role")
        return ExecutionReport(
            identifier(row["execId"]),
            row.get("clientOid") or None,
            identifier(row["orderId"]),
            Side(identifier(row["side"]).upper()),
            spec.quantity_to_lots(decimal(row["execQty"])),
            decimal(row["execPrice"]),
            timestamp(row.get("execTime", row.get("createdTime"))),
            receipt_ns,
            decimal(fee["fee"]),
            identifier(fee["feeCoin"]),
            decimal(row["feeRate"]) if row.get("feeRate") else None,
            scope == "maker",
            ExecutionType.TRADE,
            decimal(row["execPnl"]) if row.get("execPnl") else None,
        )

    def book(
        self, symbol: str, topic: str, action: str, row: Mapping[str, Any]
    ) -> BookSnapshot | BookDelta:
        if topic not in {"books", "books1", "books5", "books50"}:
            raise ValueError("unsupported book topic")
        if action not in {"snapshot", "update"} or (topic != "books" and action != "snapshot"):
            raise ValueError("snapshot feed rejects update")
        spec = self.specs[symbol]

        def levels(name: str) -> tuple[BookLevel, ...]:
            values = row[name]
            if not isinstance(values, list) or len(values) > 20000:
                raise ValueError("malformed or unbounded depth")
            return tuple(
                BookLevel(spec.price_to_ticks(decimal(p)), spec.quantity_to_lots(decimal(q)))
                for p, q in values
            )

        bids, asks = levels("b"), levels("a")
        sequence = str(integer(row["seq"]))
        # V3 supplies seq/pseq, no checksum contract. Never borrow V2 CRC rules.
        if row.get("checksum") is not None:
            raise ValueError("unverified checksum contract")
        extensions = (("topic", topic), ("maxDepth", str(row.get("maxDepth", ""))))
        if action == "snapshot":
            return BookSnapshot(bids, asks, sequence, None, extensions)
        return BookDelta(bids, asks, sequence, str(integer(row["pseq"])), None, extensions)

    def financial(self, row: Mapping[str, Any]) -> EventPayload:
        kind, native = identifier(row["type"]), identifier(row["id"])
        amount, asset, at = decimal(row["amount"]), identifier(row["coin"]), timestamp(row["ts"])
        if kind in {"TRANSFER_IN", "TRANSFER_OUT"}:
            incoming = kind == "TRANSFER_IN"
            if (amount > 0) != incoming and amount != 0:
                raise ValueError("cash-flow direction conflicts with signed amount")
            return CashTransfer(native, amount.copy_abs(), asset, "IN" if incoming else "OUT", at)
        if kind in {
            "MARGIN_SETTLE_FEE_USER_IN",
            "MARGIN_SETTLE_FEE_USER_OUT",
            "FIXED_SETTLE_FEE_USER_IN",
            "FIXED_SETTLE_FEE_USER_OUT",
            "CONTRACT_MAIN_SETTLE_FEE_USER_IN",
            "CONTRACT_MAIN_SETTLE_FEE_USER_OUT",
        }:
            if (amount > 0) != kind.endswith("_IN") and amount != 0:
                raise ValueError("funding direction conflicts with signed amount")
            return FundingSettlement(native, self.spec(row).instrument_id, amount, asset, at)
        # Trade cash records are corroboration, never a second fee/PnL posting.
        corroborating = kind in {
            "OPEN_LONG",
            "OPEN_SHORT",
            "BUY_DEAL",
            "SELL_DEAL",
            "CLOSE_LONG",
            "CLOSE_SHORT",
            "FIXED_OPEN_LONG",
            "FIXED_OPEN_SHORT",
            "FIXED_BUY_DEAL",
            "FIXED_SELL_DEAL",
            "FIXED_CLOSE_LONG",
            "FIXED_CLOSE_SHORT",
            "ORDER_PLF_FEE_OUT",
        }
        return VenueObservation(
            "financial",
            native,
            json.dumps(row, sort_keys=True, separators=(",", ":")),
            None if corroborating else "UNMATCHED_CASH_MOVEMENT",
        )

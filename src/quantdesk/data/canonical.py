"""Strict decoding shared by market consumers and artifact trust boundaries."""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import fields
from decimal import Decimal
from typing import cast

from quantdesk.core.events import (
    BarClosed,
    BookDelta,
    BookSnapshot,
    FundingRateAnnounced,
    IncomingEvent,
    MarkPrice,
    Quote,
    Trade,
)
from quantdesk.core.types import EventPayload, Side
from quantdesk.persistence.manifests import RawRef


def canonical_int(value: object, *, minimum: int | None = None) -> int:
    """Decode exact JSON integers and canonical decimal strings, never bool/float."""
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)", value):
        result = int(value)
    else:
        raise ValueError("integer or canonical decimal integer string required")
    if minimum is not None and result < minimum:
        raise ValueError(f"integer must be at least {minimum}")
    return result


def text_value(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty text required")
    return value


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def json_object(data: object) -> dict[str, object]:
    if not isinstance(data, (str, bytes)):
        raise ValueError("JSON bytes or text required")
    result = json.loads(data, object_pairs_hook=_object)
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return cast(dict[str, object], result)


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("canonical Decimal string required")
    try:
        result = Decimal(value)
    except ArithmeticError as exc:
        raise ValueError("invalid canonical Decimal string") from exc
    if not result.is_finite():
        raise ValueError("finite Decimal required")
    return result


def validate_payload(event_type: str, data: object) -> EventPayload:
    from quantdesk.data.orderbook.validator import levels

    types: dict[str, type[EventPayload]] = {
        "Trade": Trade,
        "BookSnapshot": BookSnapshot,
        "BookDelta": BookDelta,
        "Quote": Quote,
        "BarClosed": BarClosed,
        "MarkPrice": MarkPrice,
        "FundingRateAnnounced": FundingRateAnnounced,
    }
    if event_type not in types:
        raise ValueError("unsupported market payload type")
    kind = types[event_type]
    value = json_object(data)
    if set(value) != {field.name for field in fields(kind)}:
        raise ValueError("payload fields differ from supported schema")
    if "venue_extensions" in value:
        extensions = value["venue_extensions"]
        if not isinstance(extensions, list):
            raise ValueError("venue_extensions must be an array")
        for item in extensions:
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                raise ValueError("venue extension must be a named scalar pair")
            scalar = item[1]
            if scalar is not None and not isinstance(scalar, (str, bool, int, float)):
                raise ValueError("venue extension must be a scalar")
            if isinstance(scalar, float) and not math.isfinite(scalar):
                raise ValueError("venue extension must be finite")
        value["venue_extensions"] = tuple(tuple(item) for item in extensions)
    integer_fields = {
        "price_ticks",
        "size_lots",
        "bid_price_ticks",
        "bid_size_lots",
        "ask_price_ticks",
        "ask_size_lots",
        "start_ns",
        "end_ns",
        "open_ticks",
        "high_ticks",
        "low_ticks",
        "close_ticks",
        "volume_lots",
        "event_ns",
        "settlement_ns",
    }
    for name in value.keys() & integer_fields:
        value[name] = canonical_int(value[name], minimum=0)
    for name in ("native_sequence", "prior_sequence", "checksum"):
        if name in value:
            value[name] = text_value(value[name], optional=True)
    for name in ("native_trade_id", "source"):
        if name in value:
            value[name] = text_value(value[name])
    if event_type == "Trade":
        side = value["aggressor_side"]
        if side is not None:
            if not isinstance(side, str) or side not in {"BUY", "SELL"}:
                raise ValueError("invalid aggressor side")
            value["aggressor_side"] = Side(side)
    for name in ("bids", "asks", "bid_updates", "ask_updates"):
        if name in value:
            value[name] = levels(value[name])
    if "synthetic" in value and type(value["synthetic"]) is not bool:
        raise ValueError("synthetic must be boolean")
    for name in ("price", "rate"):
        if name in value:
            value[name] = _decimal(value[name])
    # Dynamic constructor is constrained by the exact registered schema above.
    return kind(**value)


def decode_market_event(data: object, *, envelope: bool = False) -> IncomingEvent:
    value = json_object(data)
    expected = {field.name for field in fields(IncomingEvent)}
    if envelope:
        expected |= {"event_id", "engine_seq"}
    if set(value) != expected:
        raise ValueError("envelope fields differ from supported schema")
    if envelope:
        text_value(value.pop("event_id"))
        canonical_int(value.pop("engine_seq"), minimum=1)
    for name in ("schema_version", "receive_wall_ns", "receive_monotonic_ns", "available_ns"):
        value[name] = canonical_int(value[name], minimum=0)
    if value["schema_version"] != 1:
        raise ValueError("unsupported schema version")
    for name in ("exchange_event_ns", "exchange_transaction_ns"):
        if value[name] is not None:
            value[name] = canonical_int(value[name], minimum=0)
    required_text = {
        "event_type",
        "run_id",
        "venue",
        "environment",
        "instrument_id",
        "source_channel",
        "connection_epoch",
        "correlation_id",
        "producer_version",
    }
    optional_text = {
        "account_id",
        "source_message_id",
        "source_sequence",
        "causation_id",
        "raw_ref",
    }
    for name in required_text:
        value[name] = text_value(value[name])
    for name in optional_text:
        value[name] = text_value(value[name], optional=True)
    if value["account_id"] is not None:
        raise ValueError("private events cannot enter public market datasets")
    if value["raw_ref"] is not None:
        RawRef.parse(cast(str, value["raw_ref"]))
    encoded = value["payload"]
    if not isinstance(encoded, dict) or set(encoded) != {"$bytes"}:
        raise ValueError("canonical payload byte wrapper required")
    if not isinstance(encoded["$bytes"], str):
        raise ValueError("payload base64 must be text")
    payload = base64.b64decode(encoded["$bytes"], validate=True)
    event_type = cast(str, value["event_type"])
    validate_payload(event_type, payload)
    value["payload"] = payload
    return IncomingEvent(**value)  # type: ignore[arg-type]

"""V3 order bodies; no core arithmetic or authorization occurs in this module."""

import re

from quantdesk.core.events import OrderInstruction
from quantdesk.core.types import OrderType
from quantdesk.venues.instruments import InstrumentSpec, exact_product


def client_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
        raise ValueError("client ID outside safe Bitget syntax intersection")
    return value


def order_request(value: OrderInstruction, spec: InstrumentSpec) -> tuple[str, dict[str, object]]:
    if value.instrument_id != spec.instrument_id or not value.instrument_id.startswith(
        "bitget:USDT-FUTURES:"
    ):
        raise ValueError("instrument does not match Bitget specification")
    quantity = spec.base_quantity(value.quantity_lots)
    if spec.trading_status != "TRADING" or not spec.min_quantity <= quantity <= spec.max_quantity:
        raise ValueError("instrument quantity/status filter failed")
    body: dict[str, object] = {
        "category": "USDT-FUTURES",
        "symbol": spec.instrument_id.split(":")[-1],
        "clientOid": client_id(value.client_order_id),
        "qty": format(quantity, "f"),
    }
    if value.order_type == OrderType.STOP:
        raise ValueError("standalone protection placement schema is unverified")
    # Bounded exits use limit IOC. An unbounded market instruction cannot express
    # the required local price collar, so this profile rejects it.
    if value.order_type != OrderType.LIMIT or value.price_ticks is None:
        raise ValueError("bounded limit order required")
    price = spec.price(value.price_ticks)
    if exact_product(quantity, price) < spec.min_notional:
        raise ValueError("minimum notional filter failed")
    body.update(
        {
            "orderType": "limit",
            "price": format(price, "f"),
            "side": value.side.value.lower(),
            "timeInForce": value.time_in_force.value.lower(),
            "reduceOnly": "yes" if value.reduce_only else "no",
            "marginMode": "isolated",
        }
    )
    if value.native_trigger_value is not None:
        if (
            value.reduce_only
            or value.native_trigger_basis not in {"MARK", "LAST"}
            or not value.protection_group_id
        ):
            raise ValueError("invalid attached trigger profile")
        spec.price_to_ticks(value.native_trigger_value)
        body.update(
            {
                "stopLoss": format(value.native_trigger_value, "f"),
                "slTriggerBy": "mark" if value.native_trigger_basis == "MARK" else "market",
                "slOrderType": "market",
            }
        )
    return "/api/v3/trade/place-order", body

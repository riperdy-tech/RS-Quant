"""Risk sizing: §11.1 formula with all required caps.

Size via risk_cash / (stop_distance + roundtrip_cost), then cap by strategy
allocation, account exposure, margin, participation, and venue maximum.
Round quantity down to lots; if below minimum, reject with
BELOW_MINIMUM_AFTER_RISK_CAP. Never round up or increase capital.
"""

from __future__ import annotations

from decimal import Decimal

from quantdesk.config.schema import RiskConfig
from quantdesk.venues.instruments import InstrumentSpec


class RiskSizer:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def calculate_size(
        self,
        intent_desired_qty: Decimal | None,
        risk_cash: Decimal,
        stop_distance_per_base_unit: Decimal,
        estimated_roundtrip_cost_per_base_unit: Decimal,
        current_price: Decimal,
        spec: InstrumentSpec,
        account_exposure_remaining: Decimal | None = None,
        symbol_exposure_remaining: Decimal | None = None,
        max_notional_usdt: Decimal | None = None,
        max_total_notional_usdt: Decimal | None = None,
        current_total_notional: Decimal | None = None,
        margin_available: Decimal | None = None,
        visible_depth_lots: int | None = None,
        strategy_allocation_remaining: Decimal | None = None,
    ) -> tuple[int, str]:
        """Calculate allowed quantity in lots per §11.1 sizing formula.

        Returns (lots, reason_code). reason_code is "OK" on success.
        """
        if stop_distance_per_base_unit <= 0:
            return 0, "INVALID_STOP_DISTANCE"
        if risk_cash <= 0:
            return 0, "ZERO_RISK_BUDGET"

        risk_cost_per_unit = stop_distance_per_base_unit + estimated_roundtrip_cost_per_base_unit
        if risk_cost_per_unit <= 0:
            return 0, "INVALID_RISK_COST"

        # §11.1: size via risk_cash / (stop_distance + cost)
        allowed_base_qty = risk_cash / risk_cost_per_unit

        # Cap by explicit intent desired quantity
        if intent_desired_qty is not None and intent_desired_qty < allowed_base_qty:
            allowed_base_qty = intent_desired_qty

        # Cap by strategy allocation remaining
        if (
            strategy_allocation_remaining is not None
            and strategy_allocation_remaining < allowed_base_qty
        ):
            allowed_base_qty = strategy_allocation_remaining

        # Cap by account exposure remaining
        if (
            account_exposure_remaining is not None
            and current_price > 0
        ):
            exposure_qty = account_exposure_remaining / current_price
            if exposure_qty < allowed_base_qty:
                allowed_base_qty = exposure_qty

        # Cap by symbol exposure remaining
        if (
            symbol_exposure_remaining is not None
            and current_price > 0
        ):
            sym_qty = symbol_exposure_remaining / current_price
            if sym_qty < allowed_base_qty:
                allowed_base_qty = sym_qty

        # Cap by margin available
        if margin_available is not None and current_price > 0:
            margin_qty = margin_available / current_price
            if margin_qty < allowed_base_qty:
                allowed_base_qty = margin_qty

        # Cap by order notional limit
        if max_notional_usdt is not None and current_price > 0:
            notional_qty = max_notional_usdt / current_price
            if notional_qty < allowed_base_qty:
                allowed_base_qty = notional_qty

        # Cap by total notional limit
        if (
            max_total_notional_usdt is not None
            and current_price > 0
            and current_total_notional is not None
        ):
            remaining = max_total_notional_usdt - current_total_notional
            if remaining <= 0:
                return 0, "MAX_TOTAL_NOTIONAL_USDT_EXCEEDED"
            remaining_qty = remaining / current_price
            if remaining_qty < allowed_base_qty:
                allowed_base_qty = remaining_qty

        # Cap by venue maximum
        if allowed_base_qty > spec.max_quantity:
            allowed_base_qty = spec.max_quantity

        # Cap by participation/depth — §11.1
        if visible_depth_lots is not None and visible_depth_lots > 0:
            max_participation_lots = int(
                Decimal(visible_depth_lots) * self.config.max_order_visible_depth_fraction
            )
            participation_qty = Decimal(max_participation_lots) * spec.quantity_step
            if participation_qty < allowed_base_qty:
                allowed_base_qty = participation_qty

        # §11.1: round down to lots; never round up
        if allowed_base_qty <= 0:
            return 0, "BELOW_MINIMUM_AFTER_RISK_CAP"
        lots = int(allowed_base_qty / spec.quantity_step)

        # Re-check minimums after rounding
        final_base_qty = Decimal(lots) * spec.quantity_step

        if final_base_qty < spec.min_quantity:
            return 0, "BELOW_MINIMUM_AFTER_RISK_CAP"

        if current_price > 0 and final_base_qty * current_price < spec.min_notional:
            return 0, "BELOW_MIN_NOTIONAL"

        return lots, "OK"

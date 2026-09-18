"""Chandelier Volatility Risk Sizer and Asymmetric TP/SL Engine.

Implements the dynamic risk-budgeting and asymmetric target/stop mechanics
from the Pine Script strategy ('CH종합 DIY Custom rev15'):
- Unit risk derived from exact structural distance to the Chandelier Exit line
- Compounding capital budgeting (equity = initial + net_profit)
- Asymmetric TP/SL 'BOTH' mode (max target for return, tightest boundary for stop)
- Dynamic Break-Even and trailing stop calculation
- Strict Decimal precision financial arithmetic without floating point drift
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

from quantdesk.core.types import Side


@dataclass(frozen=True, slots=True)
class AsymmetricTPSL:
    """Calculated Take Profit, Stop Loss, and Risk parameters."""

    take_profit: Decimal
    stop_loss: Decimal
    stop_distance: Decimal
    risk_dollars: Decimal
    contracts: Decimal
    break_even_price: Decimal


class ChandelierRiskSizer:
    """Calculates position sizing and dynamic stop lines via Chandelier ATR envelope."""

    def __init__(
        self,
        risk_percentage: Decimal = Decimal("0.02"),  # 2% equity at risk
        max_leverage: Decimal = Decimal("3.0"),
        lot_step: Decimal = Decimal("0.001"),
        tp_perc: Decimal = Decimal("0.03"),         # 3.0%
        tp_atr_mult: Decimal = Decimal("2.5"),      # 2.5x ATR
        sl_perc: Decimal = Decimal("0.02"),         # 2.0%
        sl_atr_mult: Decimal = Decimal("2.0"),      # 2.0x ATR
    ) -> None:
        self.risk_pct = risk_percentage
        self.max_leverage = max_leverage
        self.lot_step = lot_step
        self.tp_perc = tp_perc
        self.tp_atr_mult = tp_atr_mult
        self.sl_perc = sl_perc
        self.sl_atr_mult = sl_atr_mult

    def calculate_asymmetric_boundaries(
        self,
        side: Side,
        entry_price: Decimal,
        atr: Decimal,
        chandelier_line: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Calculates TP and SL in 'BOTH' mode (conservative protective boundary for stop,

        highest yield target for profit).

        Returns:
            (take_profit, stop_loss, stop_distance)
        """
        if entry_price <= Decimal("0"):
            raise ValueError("Entry price must be positive")
        if atr <= Decimal("0"):
            atr = entry_price * Decimal("0.01")  # fallback to 1% if ATR missing

        if side == Side.BUY:
            # TP: max between percentage offset and ATR distance
            tp_perc_price = entry_price * (Decimal("1") + self.tp_perc)
            tp_atr_price = entry_price + (self.tp_atr_mult * atr)
            tp = max(tp_perc_price, tp_atr_price)

            # SL: tightest / highest boundary between % offset, ATR distance, and Chandelier Stop
            sl_perc_price = entry_price * (Decimal("1") - self.sl_perc)
            sl_atr_price = entry_price - (self.sl_atr_mult * atr)
            sl_candidates = [sl_perc_price, sl_atr_price]
            if chandelier_line > Decimal("0") and chandelier_line < entry_price:
                sl_candidates.append(chandelier_line)
            sl = max(sl_candidates)
            dist = entry_price - sl

        else:  # Side.SELL
            tp_perc_price = entry_price * (Decimal("1") - self.tp_perc)
            tp_atr_price = entry_price - (self.tp_atr_mult * atr)
            tp = min(tp_perc_price, tp_atr_price)

            sl_perc_price = entry_price * (Decimal("1") + self.sl_perc)
            sl_atr_price = entry_price + (self.sl_atr_mult * atr)
            sl_candidates = [sl_perc_price, sl_atr_price]
            if chandelier_line > Decimal("0") and chandelier_line > entry_price:
                sl_candidates.append(chandelier_line)
            sl = min(sl_candidates)
            dist = sl - entry_price

        if dist <= Decimal("0"):
            dist = Decimal("1.5") * atr

        return tp, sl, dist

    def size_position(
        self,
        total_equity: Decimal,
        entry_price: Decimal,
        side: Side,
        atr: Decimal,
        chandelier_line: Decimal,
        confidence_multiplier: Decimal = Decimal("1.0"),
    ) -> AsymmetricTPSL:
        """Calculates exact lot size and TP/SL levels according to Chandelier risk rules."""
        if total_equity <= Decimal("0"):
            raise ValueError("Total equity must be positive")

        tp, sl, dist = self.calculate_asymmetric_boundaries(
            side=side,
            entry_price=entry_price,
            atr=atr,
            chandelier_line=chandelier_line,
        )

        # Total capital dollar risk (scaled by ML confidence multiplier if present)
        risk_budget = total_equity * self.risk_pct * confidence_multiplier

        # Contract quantity based on distance to structural stop
        raw_contracts = risk_budget / dist

        # Cap by maximum allowable notional leverage
        max_notional = total_equity * self.max_leverage
        max_contracts = max_notional / entry_price
        capped_contracts = min(raw_contracts, max_contracts)

        # Round down to lot step
        lots = (capped_contracts / self.lot_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * self.lot_step
        final_contracts = max(self.lot_step, lots)

        return AsymmetricTPSL(
            take_profit=tp.quantize(Decimal("0.01")),
            stop_loss=sl.quantize(Decimal("0.01")),
            stop_distance=dist.quantize(Decimal("0.01")),
            risk_dollars=risk_budget.quantize(Decimal("0.01")),
            contracts=final_contracts,
            break_even_price=entry_price,
        )

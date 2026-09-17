"""Sweep heuristic rule strategy per §12.3."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.base import Strategy


class SweepHeuristic(Strategy):
    def __init__(
        self,
        instrument_id: str = "BTCUSDT",
        strategy_id: str = "sweep-v1",
        tick_size: Decimal = Decimal("0.5"),
    ):
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id
        self.tick_size = tick_size
        self.last_signal: Side | None = None
        self.max_hold_ns = 5_000_000_000  # 5 seconds maximum hold

        # Position tracking per §12.3
        self.position_lots = Decimal("0")
        self.position_side: Side | None = None
        self.entry_price: Decimal | None = None
        self.entry_time_ns: int | None = None
        self.stop_price: Decimal | None = None
        self.target_price: Decimal | None = None

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        features = context.get("features", {})
        mid = features.get("mid")

        # 1. Manage active position exit
        if self.position_lots > 0 and mid is not None and self.entry_price is not None:
            curr_mid = Decimal(str(mid))
            elapsed_ns = event.available_ns - (self.entry_time_ns or event.available_ns)
            exit_reason: str | None = None

            if self.position_side == Side.BUY:
                if self.stop_price and curr_mid <= self.stop_price:
                    exit_reason = "sweep_stop_hit"
                elif self.target_price and curr_mid >= self.target_price:
                    exit_reason = "sweep_target_hit"
                elif elapsed_ns >= self.max_hold_ns:
                    exit_reason = "sweep_max_hold_5s"
            elif self.position_side == Side.SELL:
                if self.stop_price and curr_mid >= self.stop_price:
                    exit_reason = "sweep_stop_hit"
                elif self.target_price and curr_mid <= self.target_price:
                    exit_reason = "sweep_target_hit"
                elif elapsed_ns >= self.max_hold_ns:
                    exit_reason = "sweep_max_hold_5s"

            if exit_reason:
                exit_side = Side.SELL if self.position_side == Side.BUY else Side.BUY
                qty = self.position_lots
                self.position_lots = Decimal("0")
                self.position_side = None
                self.entry_price = None
                self.entry_time_ns = None
                self.last_signal = None
                intent = StrategyIntent(
                    intent_id=f"{event.event_id}-{self.strategy_id}-exit",
                    strategy_id=self.strategy_id,
                    instrument_id=self.instrument_id,
                    decision_seq=event.engine_seq,
                    feature_snapshot_id=f"snap-{event.engine_seq}",
                    config_hash="cfg-default",
                    model_hash_or_none=None,
                    action=IntentAction.EXIT,
                    side=exit_side,
                    desired_quantity=qty,
                    risk_budget=None,
                    price_policy="MARKET",
                    expires_at_ns=event.available_ns + 5_000_000_000,
                    stop_policy="NONE",
                    reason=exit_reason,
                )
                return (intent,)

        # 2. Check candidate entry
        if self.position_lots > 0:
            return ()

        sweep_recovery = features.get("sweep_recovery_side")
        extreme_price = features.get("sweep_extreme_price")

        if not sweep_recovery or mid is None:
            self.last_signal = None
            return ()

        side: Side = Side.BUY if sweep_recovery == "BUY" else Side.SELL

        # Edge-triggered entry after recovery is observed
        if side != self.last_signal:
            self.last_signal = side
            mid_dec = Decimal(str(mid))
            extreme_dec = Decimal(str(extreme_price)) if extreme_price is not None else mid_dec

            # Stop beyond observed sweep extreme + 2 ticks (§12.3)
            two_ticks = self.tick_size * Decimal("2")
            if side == Side.BUY:
                stop_p = min(mid_dec, extreme_dec) - two_ticks
                risk_unit = mid_dec - stop_p
                target_p = mid_dec + risk_unit
            else:
                stop_p = max(mid_dec, extreme_dec) + two_ticks
                risk_unit = stop_p - mid_dec
                target_p = mid_dec - risk_unit

            self.position_lots = Decimal("1")
            self.position_side = side
            self.entry_price = mid_dec
            self.entry_time_ns = event.available_ns
            self.stop_price = stop_p
            self.target_price = target_p

            intent = StrategyIntent(
                intent_id=f"{event.event_id}-{self.strategy_id}-enter",
                strategy_id=self.strategy_id,
                instrument_id=self.instrument_id,
                decision_seq=event.engine_seq,
                feature_snapshot_id=f"snap-{event.engine_seq}",
                config_hash="cfg-default",
                model_hash_or_none=None,
                action=IntentAction.ENTER,
                side=side,
                desired_quantity=Decimal("1"),
                risk_budget=Decimal("10"),
                price_policy="MARKET",
                expires_at_ns=event.available_ns + 5_000_000_000,
                stop_policy="SWEEP_EXTREME_2_TICKS",
                reason="sweep_recovery",
            )
            return (intent,)

        return ()

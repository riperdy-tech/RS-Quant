"""Imbalance scalper rule strategy per §12.3."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.base import Strategy


class ImbalanceScalper(Strategy):
    def __init__(self, instrument_id: str = "BTCUSDT", strategy_id: str = "imbalance-scalper-v1"):
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id
        self.cooldown_ns = 1_000_000_000  # 1-second internal loop cooldown
        self.max_hold_ns = 300_000_000_000  # 300 seconds (5 minutes) maximum hold
        self.threshold = 0.35
        self.atr_target_multiplier = 3.5
        self.last_signal: Side | None = None
        self.last_exit_time_ns: int | None = None

        # Position tracking
        self.position_lots = Decimal("0")
        self.position_side: Side | None = None
        self.entry_price: Decimal | None = None
        self.entry_time_ns: int | None = None
        self.stop_price: Decimal | None = None
        self.target_price: Decimal | None = None

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        features = context.get("features", {})
        spread_bps = features.get("spread_bps")
        depth5_imb = features.get("depth5_imbalance")
        micro = features.get("microprice")
        mid = features.get("mid")
        vol_1s = features.get("volume_1s_signed")
        atr14 = features.get("atr14")

        # 1. Manage active position exit if in position
        if self.position_lots > 0 and mid is not None and self.entry_price is not None:
            curr_mid = Decimal(str(mid))
            elapsed_ns = event.available_ns - (self.entry_time_ns or event.available_ns)
            exit_reason: str | None = None

            if self.position_side == Side.BUY:
                if self.stop_price and curr_mid <= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and curr_mid >= self.target_price:
                    exit_reason = "take_profit_hit"
                elif elapsed_ns >= self.max_hold_ns:
                    exit_reason = "max_hold_timeout"
            elif self.position_side == Side.SELL:
                if self.stop_price and curr_mid >= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and curr_mid <= self.target_price:
                    exit_reason = "take_profit_hit"
                elif elapsed_ns >= self.max_hold_ns:
                    exit_reason = "max_hold_timeout"

            if exit_reason:
                exit_side = Side.SELL if self.position_side == Side.BUY else Side.BUY
                qty = self.position_lots
                self.position_lots = Decimal("0")
                self.position_side = None
                self.entry_price = None
                self.last_exit_time_ns = event.available_ns
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
                    expires_at_ns=event.available_ns + 500_000_000,
                    stop_policy="NONE",
                    reason=exit_reason,
                )
                return (intent,)

        # 2. Check cooldown and preconditions for entry
        if self.position_lots > 0:
            return ()

        if (
            self.last_exit_time_ns
            and (event.available_ns - self.last_exit_time_ns) < self.cooldown_ns
        ):
            return ()

        if any(v is None for v in [spread_bps, depth5_imb, micro, mid, vol_1s, atr14]):
            return ()

        if spread_bps > 5.0 or atr14 <= 0:
            self.last_signal = None
            return ()

        # 3. Entry condition evaluation (§12.3)
        thresh = getattr(self, "threshold", 0.35)
        side: Side | None = None
        if depth5_imb > thresh and micro > mid and vol_1s > 0:
            side = Side.BUY
        elif depth5_imb < -thresh and micro < mid and vol_1s < 0:
            side = Side.SELL

        # Edge-triggered entry
        if side and side != self.last_signal:
            self.last_signal = side
            mid_dec = Decimal(str(mid))
            atr_dec = Decimal(str(atr14))
            mult = Decimal(str(getattr(self, "atr_target_multiplier", 3.5)))
            stop_dist = Decimal("1.5") * atr_dec
            target_dist = mult * atr_dec

            # Setup position expectation
            self.position_lots = Decimal("1")
            self.position_side = side
            self.entry_price = mid_dec
            self.entry_time_ns = event.available_ns
            if side == Side.BUY:
                self.stop_price = mid_dec - stop_dist
                self.target_price = mid_dec + target_dist
            else:
                self.stop_price = mid_dec + stop_dist
                self.target_price = mid_dec - target_dist

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
                price_policy="BEST_SAME_SIDE",
                expires_at_ns=event.available_ns + 500_000_000,
                stop_policy="1.5_ATR",
                reason="imbalance_signal",
            )
            return (intent,)

        if not side:
            self.last_signal = None

        return ()

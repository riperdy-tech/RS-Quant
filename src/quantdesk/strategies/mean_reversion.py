"""Mean reversion rule strategy per §12.3."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.base import Strategy


class MeanReversion(Strategy):
    def __init__(self, instrument_id: str = "BTCUSDT", strategy_id: str = "mean_reversion-v1"):
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id
        self.last_signal: Side | None = None

        # Position tracking per §12.3
        self.position_lots = Decimal("0")
        self.position_side: Side | None = None
        self.entry_price: Decimal | None = None
        self.entry_bar_count: int = 0
        self.stop_price: Decimal | None = None

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        if event.event_type != "BarClosed":
            return ()

        features = context.get("features", {})
        close = features.get("close")
        upper_bb = features.get("bollinger_upper")
        lower_bb = features.get("bollinger_lower")
        mid_bb = features.get("bollinger_mid")
        rsi14 = features.get("rsi14")
        atr14 = features.get("atr14")

        # 1. Manage active position exit (§12.3: middle band, hard stop 1.5 ATR, max 10 bars)
        if self.position_lots > 0 and close is not None:
            self.entry_bar_count += 1
            close_dec = Decimal(str(close))
            exit_reason: str | None = None

            if self.position_side == Side.BUY:
                if mid_bb is not None and close >= float(mid_bb):
                    exit_reason = "bollinger_mid_exit"
                elif self.stop_price and close_dec <= self.stop_price:
                    exit_reason = "hard_stop_hit"
                elif self.entry_bar_count >= 10:
                    exit_reason = "max_hold_10_bars"
            elif self.position_side == Side.SELL:
                if mid_bb is not None and close <= float(mid_bb):
                    exit_reason = "bollinger_mid_exit"
                elif self.stop_price and close_dec >= self.stop_price:
                    exit_reason = "hard_stop_hit"
                elif self.entry_bar_count >= 10:
                    exit_reason = "max_hold_10_bars"

            if exit_reason:
                exit_side = Side.SELL if self.position_side == Side.BUY else Side.BUY
                qty = self.position_lots
                self.position_lots = Decimal("0")
                self.position_side = None
                self.entry_price = None
                self.entry_bar_count = 0
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
                    expires_at_ns=event.available_ns + 60_000_000_000,
                    stop_policy="NONE",
                    reason=exit_reason,
                )
                return (intent,)

        # 2. Preconditions for entry
        if self.position_lots > 0:
            return ()

        if any(v is None for v in [close, upper_bb, lower_bb, mid_bb, rsi14, atr14]):
            return ()

        if atr14 <= 0:
            self.last_signal = None
            return ()

        # 3. Entry condition evaluation (§12.3: close < lower_bb & rsi < 30; symmetric above)
        side: Side | None = None
        if close < lower_bb and rsi14 < 30:
            side = Side.BUY
        elif close > upper_bb and rsi14 > 70:
            side = Side.SELL

        # Edge-triggered entry
        if side and side != self.last_signal:
            self.last_signal = side
            close_dec = Decimal(str(close))
            atr_dec = Decimal(str(atr14))
            stop_dist = Decimal("1.5") * atr_dec

            self.position_lots = Decimal("1")
            self.position_side = side
            self.entry_price = close_dec
            self.entry_bar_count = 0
            if side == Side.BUY:
                self.stop_price = close_dec - stop_dist
            else:
                self.stop_price = close_dec + stop_dist

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
                expires_at_ns=event.available_ns + 60_000_000_000,
                stop_policy="1.5_ATR",
                reason="mean_reversion",
            )
            return (intent,)

        if not side:
            self.last_signal = None

        return ()

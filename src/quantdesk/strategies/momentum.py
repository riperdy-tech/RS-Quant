"""Momentum breakout rule strategy per §12.3."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.base import Strategy


class MomentumBreakout(Strategy):
    def __init__(self, instrument_id: str = "BTCUSDT", strategy_id: str = "momentum-v1"):
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id
        self.last_signal: Side | None = None

        # Position tracking per §12.3
        self.position_lots = Decimal("0")
        self.position_side: Side | None = None
        self.entry_price: Decimal | None = None
        self.entry_bar_count: int = 0
        self.stop_price: Decimal | None = None
        self.target_price: Decimal | None = None

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        if event.event_type != "BarClosed":
            return ()

        features = context.get("features", {})
        close = features.get("close")
        high_20 = features.get("high_20_prior")
        low_20 = features.get("low_20_prior")
        ema10 = features.get("ema10")
        ema30 = features.get("ema30")
        atr14 = features.get("atr14")

        # 1. Manage active position exit
        if self.position_lots > 0 and close is not None:
            self.entry_bar_count += 1
            close_dec = Decimal(str(close))
            exit_reason: str | None = None

            # Dynamic Chandelier trailing stop ratchet (§15 & Pine Script)
            # Only ratchet stop price once trade has moved in our favor past entry price
            ch_long = features.get("chandelier_long_stop")
            ch_short = features.get("chandelier_short_stop")
            if self.position_side == Side.BUY and ch_long and self.entry_price:
                ch_dec = Decimal(str(round(float(ch_long), 2)))
                if ch_dec > self.entry_price and ch_dec < close_dec:
                    self.stop_price = max(self.stop_price or ch_dec, ch_dec)
            elif self.position_side == Side.SELL and ch_short and self.entry_price:
                ch_dec = Decimal(str(round(float(ch_short), 2)))
                if ch_dec < self.entry_price and ch_dec > close_dec:
                    self.stop_price = min(self.stop_price or ch_dec, ch_dec)

            if self.position_side == Side.BUY:
                if self.stop_price and close_dec <= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and close_dec >= self.target_price:
                    exit_reason = "take_profit_hit"
                elif self.entry_bar_count >= 20:
                    exit_reason = "max_hold_20_bars"
            elif self.position_side == Side.SELL:
                if self.stop_price and close_dec >= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and close_dec <= self.target_price:
                    exit_reason = "take_profit_hit"
                elif self.entry_bar_count >= 20:
                    exit_reason = "max_hold_20_bars"

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

        if close is None:
            return ()

        # 3. Entry condition evaluation: Squeeze Momentum + McGinley Dynamic or Donchian Breakout
        side: Side | None = None
        squeeze_color = features.get("squeeze_color")
        mcginley = features.get("mcginley_value")

        # Bullish momentum breakout:
        is_squeeze_long = (squeeze_color == "BLUE") and (mcginley is None or close > mcginley)
        is_donchian_long = (
            high_20 is not None
            and close > high_20
            and (ema10 is None or ema30 is None or ema10 > ema30)
        )

        # Bearish momentum breakout:
        is_squeeze_short = (squeeze_color == "RED") and (mcginley is None or close < mcginley)
        is_donchian_short = (
            low_20 is not None
            and close < low_20
            and (ema10 is None or ema30 is None or ema10 < ema30)
        )

        if is_squeeze_long or is_donchian_long:
            side = Side.BUY
        elif is_squeeze_short or is_donchian_short:
            side = Side.SELL

        # Edge-triggered entry
        if side and side != self.last_signal:
            self.last_signal = side
            close_dec = Decimal(str(close))
            atr_val = atr14 if (atr14 is not None and atr14 > 0) else (float(close) * 0.001)
            atr_dec = Decimal(str(round(atr_val, 2)))
            stop_dist = Decimal("1.5") * atr_dec
            min_target_dist = close_dec * Decimal("0.008")  # At least 80 bps
            target_dist = max(Decimal("3.0") * atr_dec, min_target_dist)

            self.position_lots = Decimal("1")
            self.position_side = side
            self.entry_price = close_dec
            self.entry_bar_count = 0
            if side == Side.BUY:
                self.stop_price = close_dec - stop_dist
                self.target_price = close_dec + target_dist
            else:
                self.stop_price = close_dec + stop_dist
                self.target_price = close_dec - target_dist

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
                reason="momentum_breakout",
            )
            return (intent,)

        if not side:
            self.last_signal = None

        return ()

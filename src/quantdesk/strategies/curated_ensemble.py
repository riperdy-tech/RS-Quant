"""Curated 12-Factor Multi-Timeframe Strategy Engine.

Translates the institutional Pine Script architecture into QuantDesk's
event-driven Python core. Operates on 2-hour resampled bars with a 4-tier
filtration pyramid:
- Tier 1: 17-bar Donchian Breakout Trigger & Consensus Score (0-10)
- Tier 2: LazyBear Squeeze Momentum Color Alignment (Blue/Orange vs. Green/Red)
- Tier 3: Macro Trailing Score SMA(15) Gate (> 4.2 for Long, < 4.9 for Short)
- Tier 4: ADX Trend Regime & Historical Volatility Expansion Gates
- Dynamic Position Sizing & Chandelier Exit Asymmetric Trailing Stop Loss
"""

from __future__ import annotations

import json
from collections import deque
from decimal import Decimal
from typing import Any, Sequence

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.features.ensemble_features import (
    CuratedEnsembleExtractor,
    EnsembleBarState,
    RegimeVelocity,
    SqueezeColor,
)
from quantdesk.strategies.base import Strategy


class CuratedEnsembleStrategy(Strategy):
    """Institutional 12-Factor Ensemble Strategy with 2h Multi-Timeframe Gating."""

    def __init__(
        self,
        instrument_id: str = "BTCUSDT",
        strategy_id: str = "curated-ensemble-v1",
        macro_timeframe_seconds: int = 7200,  # 2 Hours (Pine Script native timeframe)
        breakout_lookback: int = 17,
        score_sma_len: int = 15,
        min_adx: float = 18.0,
        min_hv: float = 1.0,
        risk_pct_per_trade: float = 0.02,  # 2% equity risk
        max_leverage: float = 3.0,
        enable_break_even: bool = True,
        enable_trailing_sl: bool = True,
    ) -> None:
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id
        self.macro_timeframe_seconds = macro_timeframe_seconds
        self.breakout_lookback = breakout_lookback
        self.score_sma_len = score_sma_len
        self.min_adx = min_adx
        self.min_hv = min_hv
        self.risk_pct = Decimal(str(risk_pct_per_trade))
        self.max_leverage = Decimal(str(max_leverage))
        self.enable_break_even = enable_break_even
        self.enable_trailing_sl = enable_trailing_sl

        # Feature extractor
        self.extractor = CuratedEnsembleExtractor()

        # Resampling buffer for 2-hour macro bars
        self.current_macro_start_ns: int | None = None
        self.current_macro_open: float | None = None
        self.current_macro_high: float = -float("inf")
        self.current_macro_low: float = float("inf")
        self.current_macro_close: float | None = None
        self.current_macro_volume: float = 0.0

        # Rolling completed 2h bars
        self.macro_timestamps: deque[int] = deque(maxlen=200)
        self.macro_opens: deque[float] = deque(maxlen=200)
        self.macro_highs: deque[float] = deque(maxlen=200)
        self.macro_lows: deque[float] = deque(maxlen=200)
        self.macro_closes: deque[float] = deque(maxlen=200)
        self.macro_volumes: deque[float] = deque(maxlen=200)

        # Strategy position state
        self.position_lots = Decimal("0")
        self.position_side: Side | None = None
        self.entry_price: Decimal | None = None
        self.entry_bar_count: int = 0
        self.stop_price: Decimal | None = None
        self.target_price: Decimal | None = None
        self.tp_hit_flag: bool = False
        self.last_signal: Side | None = None

        # Most recent ensemble analysis snapshot
        self.latest_bar_state: EnsembleBarState | None = None

    def _update_macro_bar(
        self, start_ns: int, end_ns: int, open_p: float, high_p: float, low_p: float, close_p: float, volume_p: float
    ) -> bool:
        """Accumulates sub-bars into 2-hour macro candles.

        Returns True if a 2-hour macro candle just closed.
        """
        macro_interval_ns = self.macro_timeframe_seconds * 1_000_000_000

        # Determine macro bucket
        bucket_start = (start_ns // macro_interval_ns) * macro_interval_ns

        if self.current_macro_start_ns is None:
            self.current_macro_start_ns = bucket_start
            self.current_macro_open = open_p
            self.current_macro_high = high_p
            self.current_macro_low = low_p
            self.current_macro_close = close_p
            self.current_macro_volume = volume_p
            return False

        if bucket_start > self.current_macro_start_ns:
            # Commit the completed 2h macro bar
            self.macro_timestamps.append(self.current_macro_start_ns)
            self.macro_opens.append(self.current_macro_open if self.current_macro_open is not None else open_p)
            self.macro_highs.append(self.current_macro_high)
            self.macro_lows.append(self.current_macro_low)
            self.macro_closes.append(self.current_macro_close if self.current_macro_close is not None else close_p)
            self.macro_volumes.append(self.current_macro_volume)

            # Start the new 2h bucket
            self.current_macro_start_ns = bucket_start
            self.current_macro_open = open_p
            self.current_macro_high = high_p
            self.current_macro_low = low_p
            self.current_macro_close = close_p
            self.current_macro_volume = volume_p
            return True
        else:
            # Update within current bucket
            self.current_macro_high = max(self.current_macro_high, high_p)
            self.current_macro_low = min(self.current_macro_low, low_p)
            self.current_macro_close = close_p
            self.current_macro_volume += volume_p
            return False

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        """Processes incoming events (BarClosed) and runs the 4-tier filtration pyramid."""
        if event.event_type != "BarClosed":
            return ()

        features = context.get("features", {})
        close_p = features.get("close")
        open_p = features.get("open", close_p)
        high_p = features.get("high", close_p)
        low_p = features.get("low", close_p)
        vol_p = features.get("volume", 100.0)

        if close_p is None:
            return ()

        # Extract timestamps from event payload or features
        start_ns = event.available_ns
        end_ns = event.available_ns + 60_000_000_000

        # Update 2-hour macro resampler
        macro_closed = self._update_macro_bar(
            start_ns=start_ns,
            end_ns=end_ns,
            open_p=float(open_p),
            high_p=float(high_p),
            low_p=float(low_p),
            close_p=float(close_p),
            volume_p=float(vol_p),
        )

        close_dec = Decimal(str(close_p))

        # 1. Manage Active Position (Exits, Dynamic Trailing SL & Break-Even Ratchet)
        if self.position_lots > 0:
            self.entry_bar_count += 1
            exit_reason: str | None = None

            if self.position_side == Side.BUY:
                # Check Take Profit
                if self.target_price and close_dec >= self.target_price:
                    self.tp_hit_flag = True
                    if not self.enable_trailing_sl:
                        exit_reason = "take_profit_hit"

                # Break-Even Ratchet: Once TP is touched, stop cannot be lower than entry
                if self.enable_break_even and self.tp_hit_flag and self.entry_price:
                    if self.stop_price is None or self.stop_price < self.entry_price:
                        self.stop_price = self.entry_price

                # Dynamic Chandelier Trailing Stop
                if self.enable_trailing_sl and self.latest_bar_state:
                    l_stop_dec = Decimal(str(round(self.latest_bar_state.long_stop, 2)))
                    if l_stop_dec > Decimal("0"):
                        self.stop_price = max(self.stop_price or l_stop_dec, l_stop_dec)

                # Check Stop Loss
                if self.stop_price and close_dec <= self.stop_price:
                    exit_reason = "stop_loss_hit"

            elif self.position_side == Side.SELL:
                # Check Take Profit
                if self.target_price and close_dec <= self.target_price:
                    self.tp_hit_flag = True
                    if not self.enable_trailing_sl:
                        exit_reason = "take_profit_hit"

                # Break-Even Ratchet
                if self.enable_break_even and self.tp_hit_flag and self.entry_price:
                    if self.stop_price is None or self.stop_price > self.entry_price:
                        self.stop_price = self.entry_price

                # Dynamic Chandelier Trailing Stop
                if self.enable_trailing_sl and self.latest_bar_state:
                    s_stop_dec = Decimal(str(round(self.latest_bar_state.short_stop, 2)))
                    if s_stop_dec > Decimal("0"):
                        self.stop_price = min(self.stop_price or s_stop_dec, s_stop_dec)

                # Check Stop Loss
                if self.stop_price and close_dec >= self.stop_price:
                    exit_reason = "stop_loss_hit"

            if exit_reason:
                exit_side = Side.SELL if self.position_side == Side.BUY else Side.BUY
                qty = self.position_lots
                self.position_lots = Decimal("0")
                self.position_side = None
                self.entry_price = None
                self.entry_bar_count = 0
                self.tp_hit_flag = False
                self.last_signal = None

                intent = StrategyIntent(
                    intent_id=f"{event.event_id}-{self.strategy_id}-exit",
                    strategy_id=self.strategy_id,
                    instrument_id=self.instrument_id,
                    decision_seq=event.engine_seq,
                    feature_snapshot_id=f"snap-{event.engine_seq}",
                    config_hash="cfg-ensemble",
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

        # 2. Preconditions for Entry: Can only enter if flat
        if self.position_lots > 0:
            return ()

        # Need at least 25 macro bars to calculate the full 12-factor ensemble
        if len(self.macro_closes) < 25:
            return ()

        # Run feature extraction on macro bars
        macro_states = self.extractor.compute_all(
            timestamps=list(self.macro_timestamps),
            opens=list(self.macro_opens),
            highs=list(self.macro_highs),
            lows=list(self.macro_lows),
            closes=list(self.macro_closes),
            volumes=list(self.macro_volumes),
        )
        if not macro_states:
            return ()

        latest = macro_states[-1]
        self.latest_bar_state = latest

        # Populate features dictionary in context for downstream HybridStrategy / LightGBM
        context["features"] = {
            "close": latest.close,
            "rqk_value": latest.rqk_value,
            "mcginley_value": latest.mcginley_value,
            "squeeze_val": latest.squeeze_val,
            "squeeze_color": latest.squeeze_color.value,
            "cmf_value": latest.cmf_value,
            "stc_value": latest.stc_value,
            "qqe_line": latest.qqe_line,
            "adx_value": latest.adx_value,
            "long_stop": latest.long_stop,
            "short_stop": latest.short_stop,
            "donchian_high": latest.donchian_high,
            "donchian_low": latest.donchian_low,
            "hv_annualized": latest.hv_annualized,
            "atr_14": latest.atr_14,
            "raw_score": latest.raw_score,
            "rounded_score": latest.rounded_score,
            "score_slope": latest.score_slope,
            "trailing_score_sma15": latest.trailing_score_sma15,
        }

        # =========================================================================
        # 4-Tier Filtration Pyramid Evaluation
        # =========================================================================
        side_candidate: Side | None = None

        # --- Tier 1: 17-bar Donchian Breakout Trigger conditioned on Consensus Score ---
        # Long: Score >= 5 and Close > Donchian High
        # Short: Score <= 5 and Close < Donchian Low
        is_breakout_long = (latest.rounded_score >= 5) and (latest.close > latest.donchian_high)
        is_breakout_short = (latest.rounded_score <= 5) and (latest.close < latest.donchian_low)

        if is_breakout_long:
            side_candidate = Side.BUY
        elif is_breakout_short:
            side_candidate = Side.SELL
        else:
            self.last_signal = None
            return ()

        # --- Tier 2: Squeeze Momentum Regime Compatibility ---
        # Long allowed ONLY on Blue or Orange (not in deadband offset)
        # Short allowed ONLY on Green or Red (not in deadband offset)
        if side_candidate == Side.BUY and not latest.squeeze_long_ok:
            return ()
        if side_candidate == Side.SELL and not latest.squeeze_short_ok:
            return ()

        # --- Tier 3: Macro Trailing Score History Filter (Anti-Knife Catching) ---
        # Long vetoed if trailing 15-bar SMA <= 4.2
        # Short vetoed if trailing 15-bar SMA >= 4.9
        if side_candidate == Side.BUY and not latest.score_gate_long_ok:
            return ()
        if side_candidate == Side.SELL and not latest.score_gate_short_ok:
            return ()

        # --- Tier 4: Auxiliary Volatility & Regime Gate ---
        # Market must exhibit directional trend energy (ADX >= 18) and minimum volatility
        if latest.adx_value < self.min_adx or latest.hv_annualized < self.min_hv:
            return ()

        # Edge-triggered entry: Do not repeat identical signal on consecutive bars
        if side_candidate == self.last_signal:
            return ()

        self.last_signal = side_candidate

        # =========================================================================
        # Risk Budgeting & Chandelier Position Sizing
        # =========================================================================
        entry_price_dec = close_dec
        atr_dec = Decimal(str(round(latest.atr_14, 2))) if latest.atr_14 > 0 else Decimal("10.0")

        # Asymmetric TP/SL: BOTH Mode (Seeking maximum return on TP, most protective on SL)
        # Long Take Profit: max(entry * 1.03, entry + 2.5 * ATR)
        # Long Stop Loss: max(entry * 0.98, entry - 2.0 * ATR, chandelier_long_stop)
        if side_candidate == Side.BUY:
            tp_perc = entry_price_dec * Decimal("1.03")
            tp_atr = entry_price_dec + Decimal("2.5") * atr_dec
            self.target_price = max(tp_perc, tp_atr)

            sl_perc = entry_price_dec * Decimal("0.98")
            sl_atr = entry_price_dec - Decimal("2.0") * atr_dec
            ch_stop = Decimal(str(round(latest.long_stop, 2)))
            self.stop_price = max(sl_perc, sl_atr, ch_stop)
            stop_distance = abs(entry_price_dec - self.stop_price)
        else:
            tp_perc = entry_price_dec * Decimal("0.97")
            tp_atr = entry_price_dec - Decimal("2.5") * atr_dec
            self.target_price = min(tp_perc, tp_atr)

            sl_perc = entry_price_dec * Decimal("1.02")
            sl_atr = entry_price_dec + Decimal("2.0") * atr_dec
            ch_stop = Decimal(str(round(latest.short_stop, 2)))
            self.stop_price = min(sl_perc, sl_atr, ch_stop)
            stop_distance = abs(entry_price_dec - self.stop_price)

        if stop_distance <= Decimal("0"):
            stop_distance = Decimal("1.5") * atr_dec

        # Equity from context (default $10,000 for DEMO sizing)
        equity_dec = Decimal(str(context.get("equity", 10000.0)))
        dollar_risk = equity_dec * self.risk_pct
        calculated_contracts = (dollar_risk / stop_distance).quantize(Decimal("0.001"))
        max_contracts = ((equity_dec * self.max_leverage) / entry_price_dec).quantize(Decimal("0.001"))
        final_qty = max(Decimal("0.001"), min(calculated_contracts, max_contracts))

        self.position_lots = final_qty
        self.position_side = side_candidate
        self.entry_price = entry_price_dec
        self.entry_bar_count = 0
        self.tp_hit_flag = False

        intent = StrategyIntent(
            intent_id=f"{event.event_id}-{self.strategy_id}-enter",
            strategy_id=self.strategy_id,
            instrument_id=self.instrument_id,
            decision_seq=event.engine_seq,
            feature_snapshot_id=f"snap-{event.engine_seq}",
            config_hash="cfg-ensemble",
            model_hash_or_none=None,
            action=IntentAction.ENTER,
            side=side_candidate,
            desired_quantity=final_qty,
            risk_budget=dollar_risk,
            price_policy="MARKET",
            expires_at_ns=event.available_ns + 60_000_000_000,
            stop_policy="CHANDELIER_BOTH",
            reason=f"curated_ensemble_{side_candidate.value.lower()}_score_{latest.rounded_score}",
        )
        return (intent,)

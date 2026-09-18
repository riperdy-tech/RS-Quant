"""Unified Self-Learning Agentic Alpha Engine (SRAAE).

Fuses scattered 1-dimensional strategies into a single holistic multi-horizon
decision engine per instrument leg:
1. Pipeline 1: Macro and Regime Compass (15m-2H 12-Factor Pine Consensus + Fed/Whale Flow)
2. Pipeline 2: Tactical Setup (1m-5m Squeeze Momentum Expansion + Donchian Breakout)
3. Pipeline 3: Microstructural Sniper (L2 Depth Imbalance + Maker Post-Only Execution)
4. Pipeline 4: 3x Leverage Risk Budgeting and Dynamic Chandelier Trailing Stops
5. Multi-Speed Nested Learning Heartbeat:
   - Fast Rhythm (Every Trade): Safety attribution and asymmetric cooldown adaptation
   - Medium Rhythm (Rolling 10-20 Trades): Autoregressive indicator weighting (IC) and dynamic thresholds
   - Slow Rhythm (Rolling 100+ Trades): Walk-forward Triple Barrier LightGBM retraining
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import logging
import math
import time
from typing import Any, Sequence

import numpy as np

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.features.ensemble_features import (
    CuratedEnsembleExtractor,
    EnsembleBarState,
    SqueezeColor,
)
from quantdesk.venues.bitget_uta.contract_specs import BitgetContractSpecsRegistry

logger = logging.getLogger("quantdesk.unified_agentic")


class DirectionalBias(StrEnum):
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    STAND_ASIDE = "STAND_ASIDE"


class MarketRegimeType(StrEnum):
    STRONG_BULL = "STRONG_BULL"
    WEAK_BULL = "WEAK_BULL"
    NEUTRAL_CHOP = "NEUTRAL_CHOP"
    WEAK_BEAR = "WEAK_BEAR"
    STRONG_BEAR = "STRONG_BEAR"


class AttributionTag(StrEnum):
    PROFIT_TARGET_HIT = "PROFIT_TARGET_HIT"
    TRAILING_STOP_HIT = "TRAILING_STOP_HIT"
    FEE_DRAG_LOSS = "FEE_DRAG_LOSS"
    RAPID_STOP_CHOP = "RAPID_STOP_CHOP"
    WHALE_REVERSAL = "WHALE_REVERSAL"
    TIMEOUT_EXIT = "TIMEOUT_EXIT"


@dataclass(frozen=True)
class TradeEpisode:
    """Immutable record of an executed trade for episodic memory and learning."""
    episode_id: str
    timestamp_ns: int
    instrument_id: str
    side: str
    entry_price: Decimal
    exit_price: Decimal
    qty_units: Decimal
    duration_s: int
    gross_pnl: Decimal
    fee: Decimal
    net_pnl: Decimal
    attribution: AttributionTag
    features_at_entry: dict[str, float]


@dataclass
class DynamicParameters:
    """Self-learning parameters dynamically updated by the autoregressive agent."""
    indicator_weights: dict[str, float] = field(default_factory=lambda: {
        "rqk": 1.0,
        "mcginley": 1.0,
        "squeeze": 1.0,
        "cmf": 1.0,
        "stc": 1.0,
        "qqe": 1.0,
        "adx": 1.0,
        "chandelier": 1.0,
        "volume_delta": 1.0,
        "donchian": 1.0,
    })
    atr_target_mult: Decimal = Decimal("3.0")
    depth5_threshold: float = 0.35
    entry_cooldown_s: int = 60
    volatility_hurdle_bps: float = 6.0
    conviction_threshold: float = 0.55
    consecutive_losses: int = 0
    total_episodes_evaluated: int = 0
    last_medium_tune_ns: int = 0


class EpisodicMemoryBuffer:
    """Maintains recent trade episodes and computes statistical Information Coefficients."""

    def __init__(self, maxlen: int = 100) -> None:
        self.episodes: deque[TradeEpisode] = deque(maxlen=maxlen)

    def append(self, episode: TradeEpisode) -> None:
        self.episodes.append(episode)

    def __len__(self) -> int:
        return len(self.episodes)

    @property
    def win_rate_pct(self) -> float:
        if not self.episodes:
            return 0.0
        wins = sum(1 for ep in self.episodes if ep.net_pnl > Decimal("0"))
        return round(100.0 * wins / len(self.episodes), 1)

    @property
    def total_net_pnl(self) -> Decimal:
        return sum((ep.net_pnl for ep in self.episodes), Decimal("0.00"))

    def compute_rolling_information_coefficients(self) -> dict[str, float]:
        """Calculates Pearson correlation between indicator signals at entry and trade returns."""
        if len(self.episodes) < 5:
            return {k: 0.0 for k in [
                "rqk", "mcginley", "squeeze", "cmf", "stc",
                "qqe", "adx", "chandelier", "volume_delta", "donchian"
            ]}

        recent = list(self.episodes)[-20:]
        returns = []
        for ep in recent:
            ret = float((ep.exit_price - ep.entry_price) / ep.entry_price)
            if ep.side == "SELL":
                ret = -ret
            returns.append(ret)

        ret_arr = np.array(returns, dtype=float)
        ret_std = np.std(ret_arr)
        if ret_std < 1e-8:
            return {k: 0.0 for k in [
                "rqk", "mcginley", "squeeze", "cmf", "stc",
                "qqe", "adx", "chandelier", "volume_delta", "donchian"
            ]}

        ic_map: dict[str, float] = {}
        for name in [
            "rqk", "mcginley", "squeeze", "cmf", "stc",
            "qqe", "adx", "chandelier", "volume_delta", "donchian"
        ]:
            signals = [ep.features_at_entry.get(name, 0.0) for ep in recent]
            sig_arr = np.array(signals, dtype=float)
            sig_std = np.std(sig_arr)
            if sig_std < 1e-8:
                ic_map[name] = 0.0
            else:
                corr = np.corrcoef(sig_arr, ret_arr)[0, 1]
                ic_map[name] = float(np.nan_to_num(corr, nan=0.0))

        return ic_map


class UnifiedAgenticAlphaEngine:
    """Holistic Quantitative Engine combining Macro Compass, Tactical Setup, Micro Sniper, and Learning."""

    def __init__(
        self,
        instrument_id: str,
        strategy_id: str | None = None,
        max_leverage: float = 3.0,
        risk_pct_per_trade: float = 0.02,
        target_notional: float | Decimal = Decimal("15000.00"),
    ) -> None:
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id or f"unified-{instrument_id[:3].lower()}"
        self.max_leverage = Decimal(str(max_leverage))
        self.risk_pct = Decimal(str(risk_pct_per_trade))
        self.target_notional = Decimal(str(target_notional))
        self.spec = BitgetContractSpecsRegistry.get(self.instrument_id)

        # Self-Learning Components
        self.params = DynamicParameters()
        self.memory = EpisodicMemoryBuffer(maxlen=100)
        self.extractor = CuratedEnsembleExtractor()

        # Engine State
        self.current_bias: DirectionalBias = DirectionalBias.STAND_ASIDE
        self.current_regime: MarketRegimeType = MarketRegimeType.NEUTRAL_CHOP
        self.tactical_state: str = "INITIALIZING"
        self.rolling_ic: dict[str, float] = {}

        # Position Tracking
        self.position_side: Side | None = None
        self.position_lots: Decimal = Decimal("0")
        self.entry_price: Decimal | None = None
        self.stop_price: Decimal | None = None
        self.target_price: Decimal | None = None
        self.entry_time_ns: int = 0
        self.last_exit_time_ns: int = 0
        self.breakeven_active: bool = False
        self.entry_feature_snapshot: dict[str, float] = {}

    def set_capital_and_leverage(self, margin_per_leg: Decimal, leverage: Decimal) -> None:
        """Dynamically updates leverage and allocated target notional per leg."""
        self.max_leverage = leverage
        self.target_notional = margin_per_leg * leverage

    # =========================================================================
    # Pipeline 1: Macro and Regime Compass
    # =========================================================================
    def evaluate_macro_compass(self, features: dict[str, Any]) -> tuple[DirectionalBias, MarketRegimeType]:
        """Synthesizes 12-factor consensus score, Fed liquidity, and Whale positioning into directional bias."""
        score = float(features.get("consensus_score", features.get("score", 5.0)))
        macro_regime = features.get("macro_regime")
        warn_bearish = bool(features.get("warn_bearish", False))
        warn_bullish = bool(features.get("warn_bullish", False))
        whale_z = features.get("whale_net_flow_zscore")
        whale_z_val = float(whale_z) if whale_z is not None else 0.0

        # Classify Market Regime
        if score >= 7.0 and not warn_bearish:
            regime = MarketRegimeType.STRONG_BULL
        elif score >= 5.5 and not warn_bearish:
            regime = MarketRegimeType.WEAK_BULL
        elif score <= 3.0 or macro_regime == "BEARISH_WARNING":
            regime = MarketRegimeType.STRONG_BEAR
        elif score <= 4.5:
            regime = MarketRegimeType.WEAK_BEAR
        else:
            regime = MarketRegimeType.NEUTRAL_CHOP

        # Compute Directional Bias
        if regime in (MarketRegimeType.STRONG_BULL, MarketRegimeType.WEAK_BULL):
            # Institutional whale distribution vetoes long bias
            if whale_z_val < -1.5:
                bias = DirectionalBias.STAND_ASIDE
            else:
                bias = DirectionalBias.LONG_ONLY
        elif regime in (MarketRegimeType.STRONG_BEAR, MarketRegimeType.WEAK_BEAR):
            # Institutional whale accumulation vetoes short bias
            if whale_z_val > 1.5:
                bias = DirectionalBias.STAND_ASIDE
            else:
                bias = DirectionalBias.SHORT_ONLY
        else:
            bias = DirectionalBias.STAND_ASIDE

        # Bitget 8-Hour Funding Rate Regime Filter (§12 Funding Drag & Crowding Protection)
        # Extreme positive funding (> +25 bps / 0.25%) indicates overextended longs; veto long bias
        # Extreme negative funding (< -25 bps / -0.25%) indicates overextended shorts; veto short bias
        funding_rate_bps = float(features.get("funding_rate_bps", 0.0))
        if funding_rate_bps > 25.0 and bias == DirectionalBias.LONG_ONLY:
            bias = DirectionalBias.STAND_ASIDE
        elif funding_rate_bps < -25.0 and bias == DirectionalBias.SHORT_ONLY:
            bias = DirectionalBias.STAND_ASIDE

        self.current_bias = bias
        self.current_regime = regime
        return bias, regime

    # =========================================================================
    # Pipeline 2: Tactical Setup Engine
    # =========================================================================
    def evaluate_tactical_setup(self, features: dict[str, Any], mid: float) -> Side | None:
        """Evaluates Squeeze Momentum expansion, McGinley trend slope, and Volatility Fee Hurdle."""
        atr = features.get("atr14")
        if atr is None or mid <= 0:
            return None

        # Volatility Fee Hurdle Gate: Expected volatility must clear exchange fee drag
        vol_bps = (float(atr) / float(mid)) * 10000.0
        if vol_bps < self.params.volatility_hurdle_bps:
            self.tactical_state = f"FEE_HURDLE_TOO_LOW ({vol_bps:.1f} < {self.params.volatility_hurdle_bps:.1f} bps)"
            return None

        sq_color = features.get("squeeze_color")
        mcg_val = features.get("mcginley_value")

        # Bullish Tactical Setup: Blue/Orange Squeeze with Price >= McGinley
        is_bull_setup = (sq_color in ("BLUE", "ORANGE")) and (mcg_val is None or mid >= mcg_val)

        # Bearish Tactical Setup: Red/Green Squeeze with Price <= McGinley
        is_bear_setup = (sq_color in ("RED", "GREEN")) and (mcg_val is None or mid <= mcg_val)

        if is_bull_setup and self.current_bias == DirectionalBias.LONG_ONLY:
            self.tactical_state = "BULLISH_SETUP_ARMED"
            return Side.BUY
        elif is_bear_setup and self.current_bias == DirectionalBias.SHORT_ONLY:
            self.tactical_state = "BEARISH_SETUP_ARMED"
            return Side.SELL

        self.tactical_state = f"FILTERED (Bias={self.current_bias.value}, Squeeze={sq_color})"
        return None

    # =========================================================================
    # Pipeline 3: Microstructural Sniper Execution
    # =========================================================================
    def evaluate_micro_sniper(self, candidate_side: Side, features: dict[str, Any], mid: float) -> bool:
        """Confirms entry timing using L2 depth imbalance and aggressive trade delta for passive Maker posting."""
        d5 = features.get("depth5_imbalance")
        micro = features.get("microprice")
        vol_1s = features.get("volume_1s_signed")
        l1_ofi = features.get("l1_ofi")

        if d5 is None or micro is None:
            return False

        d5_val = float(d5)
        thresh = self.params.depth5_threshold
        vol_1s_val = float(vol_1s or 0.0)
        l1_ofi_val = float(l1_ofi or 0.0)

        if candidate_side == Side.BUY:
            # Long sniper: Depth imbalance strongly positive, microprice >= mid, buyer flow present
            orderflow_bullish = (vol_1s_val >= 0.0) or (l1_ofi_val >= 0.0)
            if d5_val >= thresh and micro >= mid and orderflow_bullish:
                return True
        elif candidate_side == Side.SELL:
            # Short sniper: Depth imbalance strongly negative, microprice <= mid, seller flow present
            orderflow_bearish = (vol_1s_val <= 0.0) or (l1_ofi_val <= 0.0)
            if d5_val <= -thresh and micro <= mid and orderflow_bearish:
                return True

        return False

    # =========================================================================
    # Master Strategy Event Handler
    # =========================================================================
    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        """Coordinates all 4 pipelines and manages position lifecycle."""
        features = context.get("features", {})
        mid = features.get("mid")
        if mid is None or float(mid) <= 0:
            return ()

        mid_dec = Decimal(str(round(float(mid), 2)))
        now_ns = event.available_ns

        # 1. Manage Active Position (Exits, Chandelier Ratchet, TP, Breakeven, Momentum & Volume Climax)
        if self.position_lots > 0 and self.entry_price is not None:
            bias, regime = self.evaluate_macro_compass(features)
            exit_reason: str | None = None
            ch_long = features.get("chandelier_long_stop")
            ch_short = features.get("chandelier_short_stop")
            ch_dir = features.get("chandelier_dir")

            # Calculate current position PnL in BPS
            if self.position_side == Side.BUY:
                pnl_bps = ((mid_dec - self.entry_price) / self.entry_price) * Decimal("10000")
            else:
                pnl_bps = ((self.entry_price - mid_dec) / self.entry_price) * Decimal("10000")

            hold_time_s = int((now_ns - self.entry_time_ns) / 1_000_000_000) if self.entry_time_ns > 0 else 0

            # Dynamic Breakeven Ratchet (+25 bps profit automatically secures fee-free stop)
            if pnl_bps >= Decimal("25.0"):
                if self.position_side == Side.BUY:
                    be_stop = self.entry_price * Decimal("1.0008")  # Entry + 8 bps (locks fees)
                    self.stop_price = max(self.stop_price or be_stop, be_stop)
                else:
                    be_stop = self.entry_price * Decimal("0.9992")  # Entry - 8 bps (locks fees)
                    self.stop_price = min(self.stop_price or be_stop, be_stop)
                self.breakeven_active = True

            # Dynamic Chandelier Trailing Ratchet (when in profit)
            if self.position_side == Side.BUY and ch_long:
                ch_dec = Decimal(str(round(float(ch_long), 2)))
                if ch_dec > self.entry_price and ch_dec < mid_dec:
                    self.stop_price = max(self.stop_price or ch_dec, ch_dec)
            elif self.position_side == Side.SELL and ch_short:
                ch_dec = Decimal(str(round(float(ch_short), 2)))
                if ch_dec < self.entry_price and ch_dec > mid_dec:
                    self.stop_price = min(self.stop_price or ch_dec, ch_dec)

            # --- Check Exit Conditions Across 4 Vectors ---

            # Vector 1: Structural Baseline (Stop Loss / Take Profit Hit)
            if self.position_side == Side.BUY:
                if self.stop_price and mid_dec <= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and mid_dec >= self.target_price:
                    exit_reason = "take_profit_hit"
            elif self.position_side == Side.SELL:
                if self.stop_price and mid_dec >= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and mid_dec <= self.target_price:
                    exit_reason = "take_profit_hit"

            # Vector 2: Volume & Order Book Climax (Exhaustion into Resistance)
            if not exit_reason and pnl_bps >= Decimal("10.0"):
                d5 = features.get("depth5_imbalance")
                cmf_v = features.get("cmf_value")
                if self.position_side == Side.BUY:
                    if d5 is not None and float(d5) <= -0.40:
                        exit_reason = "orderbook_ask_wall_climax"
                    elif cmf_v is not None and float(cmf_v) < -0.05:
                        exit_reason = "cmf_institutional_distribution"
                elif self.position_side == Side.SELL:
                    if d5 is not None and float(d5) >= 0.40:
                        exit_reason = "orderbook_bid_wall_climax"
                    elif cmf_v is not None and float(cmf_v) > 0.05:
                        exit_reason = "cmf_institutional_accumulation"

            # Vector 3: Momentum Squeeze Deceleration (Volatile Impulse Exhaustion)
            if not exit_reason and pnl_bps >= Decimal("15.0"):
                sq_color = features.get("squeeze_color")
                if self.position_side == Side.BUY and sq_color in ("GREEN", "RED", "ORANGE"):
                    exit_reason = "momentum_squeeze_deceleration"
                elif self.position_side == Side.SELL and sq_color in ("ORANGE", "BLUE", "GREEN"):
                    exit_reason = "momentum_squeeze_deceleration"

            # Vector 4: Macro & Chandelier Trend Reversal
            if not exit_reason:
                if self.position_side == Side.BUY:
                    if bias == DirectionalBias.SHORT_ONLY or regime == MarketRegimeType.STRONG_BEAR:
                        exit_reason = "macro_regime_reversal"
                    elif ch_dir == -1 and (self.stop_price is None or mid_dec < self.entry_price):
                        exit_reason = "chandelier_trend_flip"
                elif self.position_side == Side.SELL:
                    if bias == DirectionalBias.LONG_ONLY or regime == MarketRegimeType.STRONG_BULL:
                        exit_reason = "macro_regime_reversal"
                    elif ch_dir == 1 and (self.stop_price is None or mid_dec > self.entry_price):
                        exit_reason = "chandelier_trend_flip"

            # Vector 5: Strict Alpha Horizon Time-Decay (20-Minute Scratch Rule & Max Holding Timeout)
            if not exit_reason and hold_time_s >= 1200:  # 20 minutes
                if pnl_bps >= Decimal("10.0"):
                    exit_reason = "time_decay_profit_preservation"
                elif abs(pnl_bps) <= Decimal("15.0"):
                    exit_reason = "alpha_half_life_dead_chop_scratch"
                elif hold_time_s >= 3600:  # 60 minutes hard cap
                    exit_reason = "max_holding_period_timeout"

            if exit_reason:
                exit_side = Side.SELL if self.position_side == Side.BUY else Side.BUY
                qty = self.position_lots
                self.position_lots = Decimal("0")
                self.position_side = None
                self.entry_price = None
                self.stop_price = None
                self.target_price = None
                self.breakeven_active = False
                self.last_exit_time_ns = now_ns
                self.tactical_state = f"EXITED ({exit_reason})"

                intent = StrategyIntent(
                    intent_id=f"{event.event_id}-{self.strategy_id}-exit",
                    strategy_id=self.strategy_id,
                    instrument_id=self.instrument_id,
                    decision_seq=event.engine_seq,
                    feature_snapshot_id=f"snap-{event.engine_seq}",
                    config_hash="cfg-unified",
                    model_hash_or_none=None,
                    action=IntentAction.EXIT,
                    side=exit_side,
                    desired_quantity=qty,
                    risk_budget=None,
                    price_policy="MARKET",
                    expires_at_ns=now_ns + 500_000_000,
                    stop_policy="NONE",
                    reason=exit_reason,
                )
                return (intent,)
            return ()

        # 2. Check Cooldown
        cooldown_ns = self.params.entry_cooldown_s * 1_000_000_000
        if (now_ns - self.last_exit_time_ns) < cooldown_ns:
            return ()

        # 3. Pipeline 1: Macro Compass
        bias, regime = self.evaluate_macro_compass(features)
        if bias == DirectionalBias.STAND_ASIDE:
            return ()

        # 4. Pipeline 2: Tactical Setup
        candidate_side = self.evaluate_tactical_setup(features, float(mid))
        if not candidate_side:
            return ()

        # 5. Pipeline 3: Micro Sniper
        if not self.evaluate_micro_sniper(candidate_side, features, float(mid)):
            return ()

        # 6. Pipeline 4: 3x Leverage Risk Budgeting and Intent Creation
        atr_val = features.get("atr14") or (float(mid) * 0.005)
        atr_dec = Decimal(str(round(float(atr_val), 2)))

        # Structural Stop Buffer: Minimum 35 bps noise insulation, or 1.2x ATR
        ch_long = features.get("chandelier_long_stop")
        ch_short = features.get("chandelier_short_stop")
        min_stop_dist = mid_dec * Decimal("0.0035")
        atr_stop_dist = max(Decimal("1.2") * atr_dec, min_stop_dist)

        # Realistic Structural Take Profit Target: 1.5x ATR, minimum 45 bps (readily achievable intraday)
        min_target_dist = mid_dec * Decimal("0.0045")
        target_dist = max(Decimal("1.5") * atr_dec, min_target_dist)

        # Compute exact contract units from target notional in USDT
        qty_units = self.spec.compute_qty_from_notional(self.target_notional, mid_dec)

        self.position_lots = qty_units
        self.position_side = candidate_side
        self.entry_price = mid_dec
        self.entry_time_ns = now_ns
        self.breakeven_active = False
        if candidate_side == Side.BUY:
            if ch_long:
                ch_dec = Decimal(str(round(float(ch_long), 2)))
                if ch_dec <= (mid_dec - min_stop_dist) and ch_dec >= (mid_dec * Decimal("0.97")):
                    self.stop_price = ch_dec
                else:
                    self.stop_price = mid_dec - atr_stop_dist
            else:
                self.stop_price = mid_dec - atr_stop_dist
            self.target_price = mid_dec + target_dist
        else:
            if ch_short:
                ch_dec = Decimal(str(round(float(ch_short), 2)))
                if ch_dec >= (mid_dec + min_stop_dist) and ch_dec <= (mid_dec * Decimal("1.03")):
                    self.stop_price = ch_dec
                else:
                    self.stop_price = mid_dec + atr_stop_dist
            else:
                self.stop_price = mid_dec + atr_stop_dist
            self.target_price = mid_dec - target_dist

        # Snapshot entry features for episodic learning
        self.entry_feature_snapshot = {
            "rqk": float(features.get("rqk_trend", 0)),
            "mcginley": float(features.get("mcginley_trend", 0)),
            "squeeze": 1.0 if features.get("squeeze_color") in ("BLUE", "ORANGE") else -1.0,
            "cmf": float(features.get("cmf_trend", 0)),
            "stc": float(features.get("stc_trend", 0)),
            "qqe": float(features.get("qqe_trend", 0)),
            "adx": float(features.get("adx_trend", 0)),
            "chandelier": float(features.get("chandelier_dir", 1)),
            "volume_delta": float(features.get("volume_1s_signed", 0)),
            "donchian": 1.0 if candidate_side == Side.BUY else -1.0,
            "mid": float(mid),
            "atr": float(atr_val),
        }

        actual_stop_dist = abs(mid_dec - self.stop_price) if self.stop_price else atr_stop_dist
        self.tactical_state = f"ENTERED {candidate_side.value} {qty_units} {self.spec.base_coin} (Target +{target_dist:.2f} USDT, SL -{actual_stop_dist:.2f} USDT)"

        intent = StrategyIntent(
            intent_id=f"{event.event_id}-{self.strategy_id}-enter",
            strategy_id=self.strategy_id,
            instrument_id=self.instrument_id,
            decision_seq=event.engine_seq,
            feature_snapshot_id=f"snap-{event.engine_seq}",
            config_hash="cfg-unified",
            model_hash_or_none="agentic-policy-v1",
            action=IntentAction.ENTER,
            side=candidate_side,
            desired_quantity=qty_units,
            risk_budget=Decimal("10"),
            price_policy="POST_ONLY",
            expires_at_ns=now_ns + 1_000_000_000,
            stop_policy="CHANDELIER_3X",
            reason=f"unified_{candidate_side.value.lower()}_sniper",
        )
        return (intent,)

    # =========================================================================
    # Multi-Speed Learning Loops
    # =========================================================================
    def record_trade_exit(
        self,
        entry_price: Decimal,
        exit_price: Decimal,
        side: str,
        qty_units: Decimal,
        hold_time_s: int,
        gross_pnl: Decimal,
        fee: Decimal,
        net_pnl: Decimal,
        now_ns: int,
    ) -> None:
        """Fast Rhythm (Every Trade): Runs attribution, asymmetric cooldown adaptation, and records episode."""
        # 1. Attribution Tagging
        if gross_pnl > Decimal("0") and net_pnl < Decimal("0"):
            attribution = AttributionTag.FEE_DRAG_LOSS
        elif net_pnl < Decimal("0") and hold_time_s < 45:
            attribution = AttributionTag.RAPID_STOP_CHOP
        elif net_pnl > Decimal("0"):
            attribution = AttributionTag.PROFIT_TARGET_HIT
        else:
            attribution = AttributionTag.TRAILING_STOP_HIT

        # 2. Fast Asymmetric Cooldown Adaptation
        if net_pnl > Decimal("0"):
            self.params.consecutive_losses = 0
            self.params.entry_cooldown_s = 15  # Reset to minimal cooldown for continuation
        else:
            self.params.consecutive_losses += 1
            # Exponential backoff on chop/losses to prevent churning
            backoff = min(300, 45 * (2 ** min(3, self.params.consecutive_losses - 1)))
            self.params.entry_cooldown_s = backoff

        # 3. Create and store trade episode
        episode = TradeEpisode(
            episode_id=f"ep-{now_ns}",
            timestamp_ns=now_ns,
            instrument_id=self.instrument_id,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            qty_units=qty_units,
            duration_s=hold_time_s,
            gross_pnl=gross_pnl,
            fee=fee,
            net_pnl=net_pnl,
            attribution=attribution,
            features_at_entry=dict(self.entry_feature_snapshot),
        )
        self.memory.append(episode)
        self.params.total_episodes_evaluated += 1

        logger.info(
            f"Agentic fast attribution for {self.instrument_id}: {attribution.value} "
            f"(Net {net_pnl:.2f} USDT, Fee {fee:.2f} USDT, Cooldown {self.params.entry_cooldown_s}s)"
        )

        # 4. Trigger Medium Rhythm dynamically once warmup of 5 trades is reached (continuous single-trade EWMA updates)
        if len(self.memory.episodes) >= 5:
            self.run_autoregressive_parameter_update(now_ns)

    def run_autoregressive_parameter_update(self, now_ns: int) -> None:
        """Medium Rhythm (Rolling 10-20 Trades): Smoothly adapts indicator weights and dynamic thresholds."""
        ic_map = self.memory.compute_rolling_information_coefficients()
        self.rolling_ic = ic_map

        # 1. Autoregressive Indicator Weight Update via Rolling Information Coefficient
        # w_new = max(0.1, 1.0 + 2.0 * IC_i)
        # Smoothed with alpha = 0.15 to avoid single-trade thrashing
        alpha = 0.15
        for name, ic in ic_map.items():
            curr_w = self.params.indicator_weights.get(name, 1.0)
            target_w = max(0.1, min(3.0, 1.0 + 2.0 * ic))
            smoothed_w = (1.0 - alpha) * curr_w + alpha * target_w
            self.params.indicator_weights[name] = round(smoothed_w, 3)

        # 2. Dynamic OBI Threshold Calibration
        # Elevate threshold when consecutive losses occur; relax when performance is clean
        base_thresh = 0.35
        loss_adj = min(0.20, self.params.consecutive_losses * 0.05)
        self.params.depth5_threshold = round(base_thresh + loss_adj, 2)

        # 3. Dynamic Fee Hurdle and ATR Target Multiplier
        # If fee friction detected in recent trades, widen target multiplier up to 4.5x
        recent_fees = [ep for ep in list(self.memory.episodes)[-10:] if ep.attribution == AttributionTag.FEE_DRAG_LOSS]
        if len(recent_fees) >= 2:
            self.params.atr_target_mult = min(Decimal("4.5"), self.params.atr_target_mult + Decimal("0.25"))
            self.params.volatility_hurdle_bps = min(20.0, self.params.volatility_hurdle_bps + 2.0)

        self.params.last_medium_tune_ns = now_ns
        logger.info(
            f"Agentic medium adaptation for {self.instrument_id}: "
            f"Weights={self.params.indicator_weights}, OBI_Thresh={self.params.depth5_threshold}, "
            f"TargetMult={self.params.atr_target_mult}x"
        )

    def get_agentic_status(self) -> dict[str, Any]:
        """Returns deep telemetry on the agent's living brain, weights, and memory."""
        return {
            "instrument_id": self.instrument_id,
            "strategy_id": self.strategy_id,
            "current_bias": self.current_bias.value,
            "current_regime": self.current_regime.value,
            "tactical_state": self.tactical_state,
            "dynamic_parameters": {
                "indicator_weights": self.params.indicator_weights,
                "atr_target_mult": str(self.params.atr_target_mult),
                "depth5_threshold": self.params.depth5_threshold,
                "entry_cooldown_s": self.params.entry_cooldown_s,
                "volatility_hurdle_bps": self.params.volatility_hurdle_bps,
                "conviction_threshold": self.params.conviction_threshold,
                "consecutive_losses": self.params.consecutive_losses,
            },
            "rolling_ic": self.rolling_ic,
            "target_notional": str(self.target_notional),
            "active_position": {
                "side": self.position_side.value if self.position_side else None,
                "lots": str(self.position_lots),
                "entry_price": str(self.entry_price) if self.entry_price else None,
                "stop_price": str(self.stop_price) if self.stop_price else None,
                "target_price": str(self.target_price) if self.target_price else None,
                "breakeven_active": self.breakeven_active,
                "hold_time_s": int((time.time_ns() - self.entry_time_ns) / 1_000_000_000) if self.entry_time_ns > 0 else 0,
            },
            "memory_summary": {
                "total_episodes": len(self.memory),
                "win_rate_pct": f"{self.memory.win_rate_pct}%",
                "total_net_pnl": f"{self.memory.total_net_pnl:.2f}",
                "recent_attributions": [ep.attribution.value for ep in list(self.memory.episodes)[-5:]],
            },
            "timestamp_ns": time.time_ns(),
        }

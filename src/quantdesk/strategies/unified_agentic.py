"""Unified Self-Learning Agentic Alpha Engine (SRAAE).

Fuses scattered 1-dimensional strategies into a single holistic multi-horizon
decision engine per instrument leg:
1. Pipeline 1: Empirical Macro & Regime Compass (Empirical Volume/Depth/Spread + 12-Factor Pine Consensus)
2. Pipeline 2: 10-Indicator Weighted Tactical Setup Engine (Active Autoregressive Weights)
3. Pipeline 3: Microstructural Sniper (L2 Depth Imbalance + Aggressive Flow)
4. Pipeline 4: Online LightGBM Meta-Learner (P(Win | Live State) Gate + Dynamic Sizing)
5. Pipeline 5: Asymmetric Risk Budgeting & Dynamic Real-Time Alpha Half-Life Scratch (< 3m)
6. Multi-Speed Nested Learning Heartbeat:
   - Fast Rhythm (Every Trade): Online replay buffer update + LightGBM warm-start retrain
   - Medium Rhythm (Rolling 5-20 Trades): Information Coefficient adaptation of 10 indicator weights
   - Slow Rhythm (Rolling 100+ Trades): Sandbox backtest validation & candidate policy evolution
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
from quantdesk.features.macro_regime import EmpiricalMarketRegimeClassifier, MacroRegimeOutput
from quantdesk.research.online_meta_learner import OnlineMetaLearner
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
    ALPHA_SCRATCH = "ALPHA_SCRATCH"
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
    atr_target_mult: Decimal = Decimal("2.5")
    depth5_threshold: float = 0.35
    entry_cooldown_s: int = 45
    volatility_hurdle_bps: float = 5.0
    conviction_threshold: float = 0.45
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
        """Calculates Pearson correlation between continuous indicator signals at entry and trade returns."""
        names = [
            "rqk", "mcginley", "squeeze", "cmf", "stc",
            "qqe", "adx", "chandelier", "volume_delta", "donchian"
        ]
        if len(self.episodes) < 5:
            return {k: 0.0 for k in names}

        recent = list(self.episodes)[-20:]
        returns = []
        for ep in recent:
            ret = float((ep.exit_price - ep.entry_price) / ep.entry_price)
            if ep.side == "SELL":
                ret = -ret
            returns.append(ret)

        ret_arr = np.array(returns, dtype=float)
        ret_std = float(np.std(ret_arr))
        if ret_std < 1e-8:
            return {k: 0.0 for k in names}

        ic_map: dict[str, float] = {}
        for name in names:
            signals = [ep.features_at_entry.get(name, 0.0) for ep in recent]
            sig_arr = np.array(signals, dtype=float)
            sig_std = float(np.std(sig_arr))
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

        # Self-Learning Brain & Models
        self.params = DynamicParameters()
        self.memory = EpisodicMemoryBuffer(maxlen=100)
        self.extractor = CuratedEnsembleExtractor()
        self.meta_learner = OnlineMetaLearner(instrument_id=self.instrument_id)
        self.macro_classifier = EmpiricalMarketRegimeClassifier()

        # Engine State & Telemetry
        self.current_bias: DirectionalBias = DirectionalBias.STAND_ASIDE
        self.current_regime: MarketRegimeType = MarketRegimeType.NEUTRAL_CHOP
        self.latest_macro_out: MacroRegimeOutput | None = None
        self.tactical_state: str = "INITIALIZING"
        self.rolling_ic: dict[str, float] = {}
        self.last_conviction: float = 0.0
        self.last_p_win: float = 0.50
        self.last_meta_features: list[float] = []

        # Position Tracking
        self.position_side: Side | None = None
        self.position_lots: Decimal = Decimal("0")
        self.entry_price: Decimal | None = None
        self.stop_price: Decimal | None = None
        self.target_price: Decimal | None = None
        self.tp1_price: Decimal | None = None
        self.tp1_hit: bool = False
        self.entry_time_ns: int = 0
        self.last_exit_time_ns: int = 0
        self.breakeven_active: bool = False
        self.entry_feature_snapshot: dict[str, float] = {}

    def set_capital_and_leverage(self, margin_per_leg: Decimal, leverage: Decimal) -> None:
        """Dynamically updates leverage and allocated target notional per leg."""
        self.max_leverage = leverage
        self.target_notional = margin_per_leg * leverage

    # =========================================================================
    # Pipeline 1: Empirical Macro & Regime Compass
    # =========================================================================
    def evaluate_macro_compass(self, features: dict[str, Any]) -> tuple[DirectionalBias, MarketRegimeType]:
        """Synthesizes Empirical Microstructure Regime, 12-factor consensus, and Whale positioning."""
        macro_out = self.macro_classifier.classify(features)
        self.latest_macro_out = macro_out

        score = float(features.get("consensus_score", features.get("score", 5.0)))
        warn_bearish = bool(features.get("warn_bearish", False))
        warn_bullish = bool(features.get("warn_bullish", False))
        whale_z = features.get("whale_net_flow_zscore")
        whale_z_val = float(whale_z) if whale_z is not None else 0.0

        # Classify Market Regime combining Empirical Metrics with Pine Consensus
        if score >= 6.5 and macro_out.regime_score >= 5.0 and not warn_bearish:
            regime = MarketRegimeType.STRONG_BULL
        elif score >= 5.2 and macro_out.regime_score >= 4.5 and not warn_bearish:
            regime = MarketRegimeType.WEAK_BULL
        elif score <= 3.5 and macro_out.regime_score <= 5.5:
            regime = MarketRegimeType.STRONG_BEAR
        elif score <= 4.8 and not warn_bullish:
            regime = MarketRegimeType.WEAK_BEAR
        else:
            regime = MarketRegimeType.NEUTRAL_CHOP

        # Compute Directional Bias
        if regime in (MarketRegimeType.STRONG_BULL, MarketRegimeType.WEAK_BULL):
            if whale_z_val < -1.5 or not macro_out.allow_entries:
                bias = DirectionalBias.STAND_ASIDE
            else:
                bias = DirectionalBias.LONG_ONLY
        elif regime in (MarketRegimeType.STRONG_BEAR, MarketRegimeType.WEAK_BEAR):
            if whale_z_val > 1.5 or not macro_out.allow_entries:
                bias = DirectionalBias.STAND_ASIDE
            else:
                bias = DirectionalBias.SHORT_ONLY
        else:
            bias = DirectionalBias.STAND_ASIDE

        # Bitget 8-Hour Funding Rate Filter
        funding_rate_bps = float(features.get("funding_rate_bps", 0.0))
        if funding_rate_bps > 25.0 and bias == DirectionalBias.LONG_ONLY:
            bias = DirectionalBias.STAND_ASIDE
        elif funding_rate_bps < -25.0 and bias == DirectionalBias.SHORT_ONLY:
            bias = DirectionalBias.STAND_ASIDE

        self.current_bias = bias
        self.current_regime = regime
        return bias, regime

    # =========================================================================
    # Pipeline 2: 10-Indicator Weighted Tactical Setup Engine
    # =========================================================================
    def compute_indicator_conviction(self, features: dict[str, Any], mid: float) -> tuple[float, dict[str, float]]:
        """Calculates normalized weighted conviction score across all 10 orthogonal indicators."""
        atr = float(features.get("atr14") or (mid * 0.005))
        atr_val = atr if atr > 0 else 1.0
        signals: dict[str, float] = {}

        # 1. RQK (Normalized deviation or trend fallback)
        if "rqk_value" in features and features["rqk_value"] is not None:
            rqk = float(features["rqk_value"])
            signals["rqk"] = float(np.clip((mid - rqk) / atr_val, -1.0, 1.0))
        else:
            signals["rqk"] = float(features.get("rqk_trend", 0.0) or 0.0)

        # 2. McGinley Dynamic (Trend alignment or trend fallback)
        if "mcginley_value" in features and features["mcginley_value"] is not None:
            mcg = float(features["mcginley_value"])
            signals["mcginley"] = float(np.clip((mid - mcg) / atr_val, -1.0, 1.0))
        else:
            signals["mcginley"] = float(features.get("mcginley_trend", 0.0) or 0.0)

        # 3. Squeeze Momentum (Acceleration color state)
        sq_color = features.get("squeeze_color", "BLUE")
        if sq_color == "BLUE":
            signals["squeeze"] = 1.0
        elif sq_color == "ORANGE":
            signals["squeeze"] = 0.5
        elif sq_color == "GREEN":
            signals["squeeze"] = -0.5
        else:  # RED
            signals["squeeze"] = -1.0

        # 4. Chaikin Money Flow (Institutional volume accumulation)
        if "cmf_value" in features and features["cmf_value"] is not None:
            cmf = float(features["cmf_value"])
            signals["cmf"] = float(np.clip(cmf / 0.10, -1.0, 1.0))
        else:
            signals["cmf"] = float(features.get("cmf_trend", 0.0) or 0.0)

        # 5. Schaff Trend Cycle (Oscillator cycle phase)
        if "stc_value" in features and features["stc_value"] is not None:
            stc = float(features["stc_value"])
            signals["stc"] = float(np.clip((stc - 50.0) / 40.0, -1.0, 1.0))
        else:
            signals["stc"] = float(features.get("stc_trend", 0.0) or 0.0)

        # 6. QQE Mod (Trend qualitative estimate)
        if "qqe_line" in features and features["qqe_line"] is not None:
            qqe = float(features["qqe_line"])
            signals["qqe"] = 1.0 if qqe > 0 else (-1.0 if qqe < 0 else 0.0)
        else:
            signals["qqe"] = float(features.get("qqe_trend", 0.0) or 0.0)

        # 7. ADX Trend Filter (Strength conditioned by trend direction)
        if "adx_value" in features and features["adx_value"] is not None:
            adx = float(features["adx_value"])
            trend_dir = 1.0 if signals["mcginley"] >= 0 else -1.0
            signals["adx"] = float(trend_dir * np.clip(adx / 30.0, 0.2, 1.0))
        else:
            signals["adx"] = float(features.get("adx_trend", 0.0) or 0.0)

        # 8. Chandelier Exit Direction
        ch_dir = float(features.get("chandelier_dir", 1) or 1)
        signals["chandelier"] = 1.0 if ch_dir > 0 else -1.0

        # 9. Volume Delta / Order Flow
        vol_z = float(features.get("volume_zscore", 0.0) or 0.0)
        d5 = float(features.get("depth5_imbalance", 0.0) or 0.0)
        signals["volume_delta"] = float(np.clip(vol_z * 0.5 + d5 * 0.5, -1.0, 1.0))

        # 10. Donchian Channel Position
        dh = features.get("donchian_high")
        dl = features.get("donchian_low")
        if dh is not None and dl is not None and float(dh) > float(dl):
            pos = (mid - (float(dh) + float(dl)) / 2.0) / ((float(dh) - float(dl)) / 2.0)
            signals["donchian"] = float(np.clip(pos, -1.0, 1.0))
        else:
            signals["donchian"] = 1.0 if signals["mcginley"] >= 0 else -1.0

        # Autoregressive Weighted Linear Sum
        total_w = sum(self.params.indicator_weights.get(k, 1.0) for k in signals)
        if total_w <= 0:
            total_w = 1.0
        weighted_sum = sum(self.params.indicator_weights.get(k, 1.0) * signals[k] for k in signals)
        conviction = float(weighted_sum / total_w)
        return conviction, signals

    def evaluate_tactical_setup(self, features: dict[str, Any], mid: float) -> Side | None:
        """Evaluates 10-indicator conviction score and Volatility Fee Hurdle."""
        atr = features.get("atr14")
        if atr is None or mid <= 0:
            return None

        # Volatility Fee Hurdle: Volatility must clear exchange fee friction (> 5.0 bps)
        vol_bps = (float(atr) / float(mid)) * 10000.0
        if vol_bps < self.params.volatility_hurdle_bps:
            self.tactical_state = f"FEE_HURDLE_LOW ({vol_bps:.1f} < {self.params.volatility_hurdle_bps:.1f} bps)"
            return None

        conviction, signals = self.compute_indicator_conviction(features, mid)
        self.last_conviction = conviction
        thresh = self.params.conviction_threshold

        # Bullish Setup: Weighted conviction exceeds positive threshold and bias matches
        if conviction >= thresh and self.current_bias == DirectionalBias.LONG_ONLY:
            self.tactical_state = f"BULLISH_CONVICTION_ARMED (Conv={conviction:+.2f})"
            return Side.BUY
        # Bearish Setup: Weighted conviction exceeds negative threshold and bias matches
        elif conviction <= -thresh and self.current_bias == DirectionalBias.SHORT_ONLY:
            self.tactical_state = f"BEARISH_CONVICTION_ARMED (Conv={conviction:+.2f})"
            return Side.SELL

        self.tactical_state = f"FILTERED (Conv={conviction:+.2f}, Thresh={thresh:.2f}, Bias={self.current_bias.value})"
        return None

    # =========================================================================
    # Pipeline 3: Microstructural Sniper Execution
    # =========================================================================
    def evaluate_micro_sniper(self, candidate_side: Side, features: dict[str, Any], mid: float) -> bool:
        """Confirms entry timing using L2 depth imbalance and orderbook flow for passive Maker posting."""
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
            orderflow_bullish = (vol_1s_val >= 0.0) or (l1_ofi_val >= 0.0)
            if d5_val >= thresh and micro >= mid and orderflow_bullish:
                return True
        elif candidate_side == Side.SELL:
            orderflow_bearish = (vol_1s_val <= 0.0) or (l1_ofi_val <= 0.0)
            if d5_val <= -thresh and micro <= mid and orderflow_bearish:
                return True

        return False

    # =========================================================================
    # Master Strategy Event Handler
    # =========================================================================
    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        """Coordinates all pipelines, manages position lifecycle, and enforces risk discipline."""
        features = context.get("features", {})
        mid = features.get("mid")
        if mid is None or float(mid) <= 0:
            return ()

        mid_dec = Decimal(str(round(float(mid), 2)))
        now_ns = event.available_ns

        # 1. Manage Active Position (Trailing Stops, TP1 Profit Lock, Real-Time Alpha Scratch)
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

            # Dynamic Breakeven Ratchet (+15 bps profit secures fee-free stop)
            if pnl_bps >= Decimal("15.0"):
                if self.position_side == Side.BUY:
                    be_stop = self.entry_price * Decimal("1.0008")  # Entry + 8 bps (locks round-trip fee)
                    self.stop_price = max(self.stop_price or be_stop, be_stop)
                else:
                    be_stop = self.entry_price * Decimal("0.9992")  # Entry - 8 bps (locks round-trip fee)
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

            # --- Check Exit Conditions Across 5 Vectors ---

            # Vector 1: Structural Baseline (Stop Loss Hit or Take Profit Target Hit)
            if self.position_side == Side.BUY:
                if self.stop_price and mid_dec <= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and mid_dec >= self.target_price:
                    exit_reason = "take_profit_hit"
                elif self.tp1_price and mid_dec >= self.tp1_price and not self.tp1_hit:
                    # Mark TP1 hit; ratchet stop to locked-in gain
                    self.tp1_hit = True
                    be_stop = self.entry_price * Decimal("1.0012")
                    self.stop_price = max(self.stop_price or be_stop, be_stop)
            elif self.position_side == Side.SELL:
                if self.stop_price and mid_dec >= self.stop_price:
                    exit_reason = "stop_loss_hit"
                elif self.target_price and mid_dec <= self.target_price:
                    exit_reason = "take_profit_hit"
                elif self.tp1_price and mid_dec <= self.tp1_price and not self.tp1_hit:
                    self.tp1_hit = True
                    be_stop = self.entry_price * Decimal("0.9988")
                    self.stop_price = min(self.stop_price or be_stop, be_stop)

            # Vector 2: Volume & Order Book Climax Exhaustion
            if not exit_reason and pnl_bps >= Decimal("10.0"):
                d5 = features.get("depth5_imbalance")
                cmf_v = features.get("cmf_value")
                if self.position_side == Side.BUY:
                    if d5 is not None and float(d5) <= -0.45:
                        exit_reason = "orderbook_ask_wall_climax"
                    elif cmf_v is not None and float(cmf_v) < -0.06:
                        exit_reason = "cmf_institutional_distribution"
                elif self.position_side == Side.SELL:
                    if d5 is not None and float(d5) >= 0.45:
                        exit_reason = "orderbook_bid_wall_climax"
                    elif cmf_v is not None and float(cmf_v) > 0.06:
                        exit_reason = "cmf_institutional_accumulation"

            # Vector 3: Momentum Squeeze Deceleration
            if not exit_reason and pnl_bps >= Decimal("12.0"):
                sq_color = features.get("squeeze_color")
                if self.position_side == Side.BUY and sq_color in ("GREEN", "RED"):
                    exit_reason = "momentum_squeeze_deceleration"
                elif self.position_side == Side.SELL and sq_color in ("ORANGE", "BLUE"):
                    exit_reason = "momentum_squeeze_deceleration"

            # Vector 4: Real-Time Alpha Half-Life Scratch (Momentum Stall Protection)
            # If after 180 seconds the trade has not generated positive alpha and momentum reverses into adverse flow,
            # exit at scratch rather than holding a deteriorating dead position.
            if not exit_reason and hold_time_s >= 180 and hold_time_s < 1200:
                sq_color = features.get("squeeze_color")
                d5 = features.get("depth5_imbalance")
                d5_val = float(d5 or 0.0)

                # Long alpha stall: flat/negative PnL AND momentum flipped to bearish with adverse depth
                if self.position_side == Side.BUY and pnl_bps < Decimal("3.0"):
                    if (sq_color in ("GREEN", "RED") and d5_val <= -0.20) or d5_val <= -0.40:
                        exit_reason = "alpha_half_life_scratch"
                # Short alpha stall: flat/negative PnL AND momentum flipped to bullish with adverse depth
                elif self.position_side == Side.SELL and pnl_bps < Decimal("3.0"):
                    if (sq_color in ("BLUE", "ORANGE") and d5_val >= 0.20) or d5_val >= 0.40:
                        exit_reason = "alpha_half_life_scratch"

            # Vector 5: Strict Alpha Horizon Time-Decay (20-Minute Scratch Rule & Max Holding Timeout)
            if not exit_reason and hold_time_s >= 1200:  # 20 minutes
                if pnl_bps >= Decimal("10.0"):
                    exit_reason = "time_decay_profit_preservation"
                elif abs(pnl_bps) <= Decimal("15.0"):
                    exit_reason = "alpha_half_life_dead_chop_scratch"
                elif hold_time_s >= 3600:  # 60 minutes hard cap
                    exit_reason = "max_holding_period_timeout"
                elif hold_time_s >= 1800:
                    exit_reason = "max_scalp_duration_limit"

            if exit_reason:
                exit_side = Side.SELL if self.position_side == Side.BUY else Side.BUY
                qty = self.position_lots
                self.position_lots = Decimal("0")
                self.position_side = None
                self.entry_price = None
                self.stop_price = None
                self.target_price = None
                self.tp1_price = None
                self.tp1_hit = False
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
        if self.last_exit_time_ns > 0 and (now_ns - self.last_exit_time_ns) < cooldown_ns:
            return ()

        # 3. Pipeline 1: Empirical Macro Compass
        bias, regime = self.evaluate_macro_compass(features)
        if bias == DirectionalBias.STAND_ASIDE:
            return ()

        # 4. Pipeline 2: 10-Indicator Tactical Setup
        candidate_side = self.evaluate_tactical_setup(features, float(mid))
        if not candidate_side:
            return ()

        # 5. Pipeline 3: Micro Sniper
        if not self.evaluate_micro_sniper(candidate_side, features, float(mid)):
            return ()

        # 6. Pipeline 4: Online LightGBM Meta-Labeler Inference
        atr_val = features.get("atr14") or (float(mid) * 0.005)
        atr_dec = Decimal(str(round(float(atr_val), 2)))
        meta_feats = self.meta_learner.extract_features(features, float(mid), float(atr_val))
        p_win = self.meta_learner.predict_win_probability(meta_feats)
        self.last_p_win = p_win
        self.last_meta_features = list(meta_feats)

        # Meta-labeler probability gate (Veto negative-expectancy setups)
        if p_win < 0.45:
            self.tactical_state = f"META_LEARNER_VETO (P(Win)={p_win:.1%} < 45.0%)"
            return ()

        # 7. Pipeline 5: Sizing, Structural Tight Stops & Intent Dispatch
        # Dynamic sizing conditioned on win probability
        sizing_mult = Decimal("1.0") if p_win >= 0.60 else (Decimal("0.8") if p_win >= 0.50 else Decimal("0.6"))
        notional_to_use = self.target_notional * sizing_mult
        qty_units = self.spec.compute_qty_from_notional(notional_to_use, mid_dec)

        # Structural Stop Buffer: Minimum 35 bps noise insulation, or 1.2x ATR
        ch_long = features.get("chandelier_long_stop")
        ch_short = features.get("chandelier_short_stop")
        min_stop_dist = mid_dec * Decimal("0.0035")
        atr_stop_dist = max(Decimal("1.2") * atr_dec, min_stop_dist)

        # 2-Stage Profit Target: TP1 at 1.0x ATR, TP2 at 1.5x ATR (min 45 bps)
        min_target_dist = mid_dec * Decimal("0.0045")
        target_dist = max(Decimal("1.5") * atr_dec, min_target_dist)
        tp1_dist = max(Decimal("1.0") * atr_dec, min_stop_dist)

        self.position_lots = qty_units
        self.position_side = candidate_side
        self.entry_price = mid_dec
        self.entry_time_ns = now_ns
        self.breakeven_active = False
        self.tp1_hit = False

        if candidate_side == Side.BUY:
            if ch_long:
                ch_dec = Decimal(str(round(float(ch_long), 2)))
                if ch_dec <= (mid_dec - min_stop_dist) and ch_dec >= (mid_dec * Decimal("0.97")):
                    self.stop_price = ch_dec
                else:
                    self.stop_price = mid_dec - atr_stop_dist
            else:
                self.stop_price = mid_dec - atr_stop_dist
            self.tp1_price = mid_dec + tp1_dist
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
            self.tp1_price = mid_dec - tp1_dist
            self.target_price = mid_dec - target_dist

        # Snapshot full 10-indicator signals dictionary for Information Coefficient learning
        conv, raw_signals = self.compute_indicator_conviction(features, float(mid))
        self.entry_feature_snapshot = dict(raw_signals)

        actual_stop_dist = abs(mid_dec - self.stop_price)
        self.tactical_state = (
            f"ENTERED {candidate_side.value} {qty_units} {self.spec.base_coin} "
            f"[P(Win)={p_win:.1%}, Conv={conv:+.2f}, TP1=+{tp1_dist:.2f}, SL=-{actual_stop_dist:.2f}]"
        )

        intent = StrategyIntent(
            intent_id=f"{event.event_id}-{self.strategy_id}-enter",
            strategy_id=self.strategy_id,
            instrument_id=self.instrument_id,
            decision_seq=event.engine_seq,
            feature_snapshot_id=f"snap-{event.engine_seq}",
            config_hash="cfg-unified",
            model_hash_or_none="agentic-policy-v2",
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
        """Fast Rhythm: Attribution, Cooldown Adaptation, and Online LightGBM Warm-Start Retrain."""
        # 1. Attribution Tagging
        if gross_pnl > Decimal("0") and net_pnl < Decimal("0"):
            attribution = AttributionTag.FEE_DRAG_LOSS
        elif net_pnl < Decimal("0") and hold_time_s < 200:
            attribution = AttributionTag.ALPHA_SCRATCH
        elif net_pnl > Decimal("0"):
            attribution = AttributionTag.PROFIT_TARGET_HIT
        else:
            attribution = AttributionTag.TRAILING_STOP_HIT

        # 2. Asymmetric Cooldown Adaptation
        if net_pnl > Decimal("0"):
            self.params.consecutive_losses = 0
            self.params.entry_cooldown_s = 20
        else:
            self.params.consecutive_losses += 1
            backoff = min(240, 45 * (2 ** min(3, self.params.consecutive_losses - 1)))
            self.params.entry_cooldown_s = backoff

        # 3. Store Trade Episode
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

        # 4. Tier 2 Online LightGBM Replay Buffer & Warm-Start Retraining
        if self.last_meta_features:
            retrained = self.meta_learner.record_episode(
                features=self.last_meta_features,
                net_pnl=float(net_pnl),
                now_ns=now_ns,
            )
            if retrained:
                logger.info(
                    f"Tier 2 Online Meta-Learner Retrained for {self.instrument_id}! "
                    f"Accuracy={self.meta_learner.last_accuracy:.1%}, Retrains={self.meta_learner.total_retrains}"
                )

        logger.info(
            f"Agentic fast attribution for {self.instrument_id}: {attribution.value} "
            f"(Net {net_pnl:.2f} USDT, Fee {fee:.2f} USDT, Duration {hold_time_s}s, Cooldown {self.params.entry_cooldown_s}s)"
        )

        # 5. Trigger Medium Rhythm Dynamic Indicator Weight Updates
        if len(self.memory.episodes) >= 5:
            self.run_autoregressive_parameter_update(now_ns)

    def run_autoregressive_parameter_update(self, now_ns: int) -> None:
        """Medium Rhythm: Adapts 10 indicator weights via Information Coefficients."""
        ic_map = self.memory.compute_rolling_information_coefficients()
        self.rolling_ic = ic_map

        # Smooth autoregressive indicator weight update: w_new = max(0.1, min(3.0, 1.0 + 2.5 * IC_i))
        alpha = 0.20
        for name, ic in ic_map.items():
            curr_w = self.params.indicator_weights.get(name, 1.0)
            target_w = max(0.1, min(3.0, 1.0 + 2.5 * ic))
            smoothed_w = (1.0 - alpha) * curr_w + alpha * target_w
            self.params.indicator_weights[name] = round(smoothed_w, 3)

        # Dynamic OBI Threshold Calibration
        base_thresh = 0.35
        loss_adj = min(0.20, self.params.consecutive_losses * 0.05)
        self.params.depth5_threshold = round(base_thresh + loss_adj, 2)

        self.params.last_medium_tune_ns = now_ns
        logger.info(
            f"Agentic medium adaptation for {self.instrument_id}: "
            f"Weights={self.params.indicator_weights}, OBI_Thresh={self.params.depth5_threshold}"
        )

    def get_agentic_status(self) -> dict[str, Any]:
        """Returns deep telemetry on the agent's living brain, weights, and memory."""
        macro_dict: dict[str, Any] = {}
        if self.latest_macro_out:
            macro_dict = {
                "regime": self.latest_macro_out.regime,
                "regime_score": self.latest_macro_out.regime_score,
                "volatility_regime": self.latest_macro_out.volatility_regime,
                "liquidity_condition": self.latest_macro_out.liquidity_condition,
                "volume_zscore": self.latest_macro_out.volume_zscore,
                "depth_imbalance": self.latest_macro_out.depth_imbalance,
                "spread_bps": self.latest_macro_out.spread_bps,
                "allow_entries": self.latest_macro_out.allow_entries,
            }

        return {
            "instrument_id": self.instrument_id,
            "strategy_id": self.strategy_id,
            "current_bias": self.current_bias.value,
            "current_regime": self.current_regime.value,
            "tactical_state": self.tactical_state,
            "empirical_macro": macro_dict,
            "meta_learner": {
                "last_p_win": round(self.last_p_win, 3),
                "total_retrains": self.meta_learner.total_retrains,
                "replay_buffer_len": len(self.meta_learner.replay_buffer),
                "last_accuracy": round(self.meta_learner.last_accuracy, 3),
            },
            "dynamic_parameters": {
                "indicator_weights": self.params.indicator_weights,
                "last_conviction": round(self.last_conviction, 3),
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
                "tp1_price": str(self.tp1_price) if self.tp1_price else None,
                "target_price": str(self.target_price) if self.target_price else None,
                "tp1_hit": self.tp1_hit,
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

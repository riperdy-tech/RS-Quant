"""Empirical Market Regime and Microstructure Health Calculator.

Calculates real-time quantitative market states purely from live market feeds:
1. Volume Z-Score (relative to rolling 20-period baseline)
2. Volatility Ratio (ATR14 relative to 24h baseline)
3. Order Book Health (Top-5 depth liquidity check)
4. Spread Shock (Bid-ask spread blowouts)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
import time
import numpy as np
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketRegimeState:
    """Quantitative market state snapshot calculated purely from received data."""
    timestamp_ns: int
    volume_zscore: float
    volatility_ratio: float
    top5_depth_btc: float
    spread_bps: float
    is_liquidity_vacuum: bool
    is_high_volume_expansion: bool
    is_quiet_chop: bool
    regime_label: str
    regime_score: float = 5.0
    depth_imbalance: float = 0.0

    @property
    def regime(self) -> str:
        return self.regime_label

    @property
    def volatility_regime(self) -> str:
        if self.volatility_ratio > 1.3:
            return "EXPANDING_VOLATILITY"
        elif self.volatility_ratio < 0.75:
            return "COMPRESSED_VOLATILITY"
        return "NORMAL_VOLATILITY"

    @property
    def liquidity_condition(self) -> str:
        if self.is_liquidity_vacuum:
            return "LIQUIDITY_VACUUM"
        elif self.spread_bps < 0.25:
            return "DEEP_TIGHT_BOOK"
        return "NORMAL_BOOK"

    @property
    def allow_entries(self) -> bool:
        return not self.is_liquidity_vacuum and not self.is_quiet_chop

    @property
    def bullish_liquidity(self) -> bool:
        return self.depth_imbalance > 0.10 and not self.is_liquidity_vacuum

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "volume_zscore": round(self.volume_zscore, 2),
            "volatility_ratio": round(self.volatility_ratio, 2),
            "top5_depth_btc": round(self.top5_depth_btc, 2),
            "spread_bps": round(self.spread_bps, 2),
            "is_liquidity_vacuum": self.is_liquidity_vacuum,
            "is_high_volume_expansion": self.is_high_volume_expansion,
            "is_quiet_chop": self.is_quiet_chop,
            "regime_label": self.regime_label,
            "regime_score": round(self.regime_score, 2),
            "depth_imbalance": round(self.depth_imbalance, 2),
        }


# Type alias for caller convenience
MacroRegimeOutput = MarketRegimeState


class EmpiricalMarketRegimeClassifier:
    """Online statistical market regime classifier avoiding static time-zone heuristics."""

    def __init__(self, rolling_window: int = 20) -> None:
        self.rolling_window = rolling_window
        self.recent_volumes: deque[float] = deque(maxlen=rolling_window)
        self.recent_atrs: deque[float] = deque(maxlen=288)  # ~24h of 5m ATRs

    def update_volume(self, volume: float) -> float:
        """Appends 5m candle volume and returns current volume Z-score."""
        if volume > 0:
            self.recent_volumes.append(volume)
        if len(self.recent_volumes) < 5:
            return 0.0
        v_arr = np.array(self.recent_volumes, dtype=float)
        mean_v = np.mean(v_arr)
        std_v = np.std(v_arr)
        if std_v < 1e-6:
            return 0.0
        return float((volume - mean_v) / std_v)

    def update_atr(self, atr: float) -> float:
        """Appends ATR and returns current Volatility Ratio vs 24h baseline."""
        if atr > 0:
            self.recent_atrs.append(atr)
        if len(self.recent_atrs) < 10:
            return 1.0
        baseline_atr = float(np.median(self.recent_atrs))
        if baseline_atr <= 0:
            return 1.0
        return float(atr / baseline_atr)

    def evaluate(
        self,
        now_ns: int,
        latest_5m_volume: float,
        latest_atr: float,
        bids: list[list[str]] | list[tuple[float, float]],
        asks: list[list[str]] | list[tuple[float, float]],
    ) -> MarketRegimeState:
        """Evaluates live market conditions into empirical state vector."""
        z_vol = self.update_volume(latest_5m_volume)
        vol_ratio = self.update_atr(latest_atr)

        # 1. Top-5 Depth sum
        top5_bids = sum(float(b[1]) for b in bids[:5]) if bids else 0.0
        top5_asks = sum(float(a[1]) for a in asks[:5]) if asks else 0.0
        depth_total = (top5_bids + top5_asks) / 2.0

        # 2. Live Spread in Bps
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2.0
            spread_bps = ((best_ask - best_bid) / mid) * 10000.0 if mid > 0 else 0.15
        else:
            spread_bps = 0.15

        # 3. Microstructural classifications
        is_liquidity_vacuum = (spread_bps >= 2.5) or (depth_total > 0 and depth_total < 8.0)
        is_high_volume_expansion = (z_vol >= 1.2) and (vol_ratio >= 1.1)
        is_quiet_chop = (z_vol <= -0.4) and (vol_ratio <= 0.85)

        if is_liquidity_vacuum:
            regime_label = "LIQUIDITY_VACUUM_FREEZE"
        elif is_high_volume_expansion:
            regime_label = "HIGH_VOLUME_EXPANSION"
        elif is_quiet_chop:
            regime_label = "LOW_VOLUME_CHOP"
        else:
            regime_label = "NORMAL_LIQUID_TREND"

        score = 5.0 + (z_vol * 1.5) - (max(0.0, spread_bps - 0.5) * 1.5)
        score = float(np.clip(score, 0.0, 10.0))

        return MarketRegimeState(
            timestamp_ns=now_ns,
            volume_zscore=z_vol,
            volatility_ratio=vol_ratio,
            top5_depth_btc=depth_total,
            spread_bps=spread_bps,
            is_liquidity_vacuum=is_liquidity_vacuum,
            is_high_volume_expansion=is_high_volume_expansion,
            is_quiet_chop=is_quiet_chop,
            regime_label=regime_label,
            regime_score=score,
            depth_imbalance=0.0,
        )

    def classify(self, features: dict[str, Any]) -> MarketRegimeState:
        """Convenience method accepting raw features dictionary."""
        now_ns = int(features.get("now_ns", time.time_ns()))
        vol = float(features.get("volume", features.get("volume_1m", 100.0)) or 100.0)
        atr = float(features.get("atr14", 100.0) or 100.0)
        bids = features.get("bids", [])
        asks = features.get("asks", [])
        d5 = float(features.get("depth5_imbalance", 0.0) or 0.0)
        spread_bps_feat = features.get("spread_bps")

        state = self.evaluate(now_ns, vol, atr, bids, asks)
        actual_spread = float(spread_bps_feat) if spread_bps_feat is not None else state.spread_bps
        z_vol_feat = features.get("volume_zscore")
        actual_z_vol = float(z_vol_feat) if z_vol_feat is not None else state.volume_zscore

        score = 5.0 + (actual_z_vol * 1.5) + (d5 * 2.0) - (max(0.0, actual_spread - 0.5) * 1.5)
        score = float(np.clip(score, 0.0, 10.0))

        return MarketRegimeState(
            timestamp_ns=now_ns,
            volume_zscore=actual_z_vol,
            volatility_ratio=state.volatility_ratio,
            top5_depth_btc=state.top5_depth_btc,
            spread_bps=actual_spread,
            is_liquidity_vacuum=(actual_spread >= 2.5) or state.is_liquidity_vacuum,
            is_high_volume_expansion=state.is_high_volume_expansion or (actual_z_vol >= 1.2),
            is_quiet_chop=state.is_quiet_chop or (actual_z_vol <= -0.4),
            regime_label=state.regime_label,
            regime_score=score,
            depth_imbalance=d5,
        )

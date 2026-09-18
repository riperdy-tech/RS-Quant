"""Macro Net Liquidity, Tether Dominance, and Convergence Radar per Pine Script rev22."""

from __future__ import annotations

import enum
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("quantdesk.data.macro_liquidity")


class MacroRegime(str, enum.Enum):
    BULLISH_SIGNAL = "BULLISH_SIGNAL"
    BEARISH_WARNING = "BEARISH_WARNING"
    NEUTRAL_CHOP = "NEUTRAL_CHOP"


@dataclass(frozen=True, slots=True)
class FedNetLiquiditySnapshot:
    """Federal Reserve Net Liquidity: WALCL - (WTREGEN + RRPONTSYD)."""

    timestamp_ns: int
    walcl: float
    tga: float
    rrp: float
    net_liquidity: float
    net_liquidity_sma20: float
    trend_direction: int  # +1 for expanding, -1 for contracting, 0 for flat
    pct_change: float
    is_bullish: bool
    z_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TetherDominanceSnapshot:
    """Tether Market Cap Dominance (USDT.D) trend and momentum."""

    timestamp_ns: int
    usdt_dominance_pct: float
    ema5: float
    z_score: float
    slope: float
    trend_up: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MacroRadarReport:
    """Consolidated market convergence warning radar."""

    regime: MacroRegime
    warn_bearish: bool
    warn_bullish: bool
    warning_strength: float  # 0.0 to 100.0
    strength_level: str  # WEAK, MODERATE, STRONG, VERY_STRONG, EXTREME
    fed_snapshot: FedNetLiquiditySnapshot | None = None
    usdt_snapshot: TetherDominanceSnapshot | None = None
    timestamp_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "warn_bearish": self.warn_bearish,
            "warn_bullish": self.warn_bullish,
            "warning_strength": round(self.warning_strength, 1),
            "strength_level": self.strength_level,
            "fed_snapshot": self.fed_snapshot.to_dict() if self.fed_snapshot else None,
            "usdt_snapshot": self.usdt_snapshot.to_dict() if self.usdt_snapshot else None,
            "timestamp_ns": self.timestamp_ns,
        }


class FedNetLiquidityClient:
    """Processes Federal Reserve balance sheet items into Net Liquidity metrics."""

    @staticmethod
    def compute_net_liquidity(walcl: float, tga: float, rrp: float) -> float:
        """Net Liquidity = Fed Assets - (Treasury General Account + Reverse Repo)."""
        return walcl - (tga + rrp)

    def calculate_snapshot(
        self,
        walcl_series: list[float],
        tga_series: list[float],
        rrp_series: list[float],
        lookback: int = 20,
        timestamp_ns: int = 0,
    ) -> FedNetLiquiditySnapshot:
        if not walcl_series or not tga_series or not rrp_series:
            raise ValueError("Time series cannot be empty")

        n = min(len(walcl_series), len(tga_series), len(rrp_series))
        net_series = [
            self.compute_net_liquidity(walcl_series[i], tga_series[i], rrp_series[i])
            for i in range(n)
        ]

        current_walcl = walcl_series[-1]
        current_tga = tga_series[-1]
        current_rrp = rrp_series[-1]
        current_net = net_series[-1]

        # 20-period SMA smoothing
        sma_window = net_series[-min(lookback, n) :]
        sma20 = sum(sma_window) / len(sma_window)

        # Baseline comparison over lookback bars
        lookback_bars = min(lookback, n - 1) if n > 1 else 1
        past_net = net_series[-lookback_bars - 1] if n > lookback_bars else net_series[0]
        abs_change = current_net - past_net
        pct_change = (abs_change / abs(past_net) * 100.0) if past_net != 0 else 0.0

        trend_dir = 1 if abs_change > 0 else (-1 if abs_change < 0 else 0)
        is_bullish = trend_dir > 0

        # Z-score over window
        variance = sum((x - sma20) ** 2 for x in sma_window) / len(sma_window)
        stdev = math.sqrt(variance) if variance > 0 else 1e-6
        z_score = (current_net - sma20) / stdev

        return FedNetLiquiditySnapshot(
            timestamp_ns=timestamp_ns or int(time.time() * 1e9),
            walcl=current_walcl,
            tga=current_tga,
            rrp=current_rrp,
            net_liquidity=current_net,
            net_liquidity_sma20=sma20,
            trend_direction=trend_dir,
            pct_change=pct_change,
            is_bullish=is_bullish,
            z_score=z_score,
        )


class TetherDominanceClient:
    """Processes USDT.D series into trend, smoothed EMA, and normalized slope."""

    def calculate_snapshot(
        self,
        usdt_values: list[float],
        smooth_len: int = 5,
        lookback: int = 20,
        timestamp_ns: int = 0,
    ) -> TetherDominanceSnapshot:
        if not usdt_values:
            raise ValueError("USDT series cannot be empty")

        current_val = usdt_values[-1]
        n = len(usdt_values)

        # 5-period EMA smoothing
        alpha = 2.0 / (smooth_len + 1.0)
        ema = usdt_values[0]
        for v in usdt_values[1:]:
            ema = alpha * v + (1.0 - alpha) * ema

        # Average slope over last trend_length (e.g., 3-5 bars)
        trend_len = min(5, n - 1) if n > 1 else 1
        changes = [usdt_values[-i] - usdt_values[-i - 1] for i in range(1, trend_len + 1)]
        avg_slope = sum(changes) / len(changes) if changes else 0.0
        trend_up = avg_slope > 0

        # Z-score over lookback
        window = usdt_values[-min(lookback, n) :]
        mean_val = sum(window) / len(window)
        variance = sum((x - mean_val) ** 2 for x in window) / len(window)
        stdev = math.sqrt(variance) if variance > 0 else 1e-6
        z_score = (current_val - mean_val) / stdev

        return TetherDominanceSnapshot(
            timestamp_ns=timestamp_ns or int(time.time() * 1e9),
            usdt_dominance_pct=current_val,
            ema5=ema,
            z_score=z_score,
            slope=avg_slope,
            trend_up=trend_up,
        )


class MacroConvergenceRadar:
    """Evaluates macro regime and computes quantitative warning strength (0-100)."""

    @staticmethod
    def _get_strength_level(strength: float) -> str:
        if strength < 20.0:
            return "WEAK"
        if strength < 40.0:
            return "MODERATE"
        if strength < 60.0:
            return "STRONG"
        if strength < 80.0:
            return "VERY_STRONG"
        return "EXTREME"

    def evaluate(
        self,
        fed_snapshot: FedNetLiquiditySnapshot,
        usdt_snapshot: TetherDominanceSnapshot,
    ) -> MacroRadarReport:
        # 1. Trend Directions
        usdt_up = usdt_snapshot.trend_up
        fed_up = fed_snapshot.is_bullish

        # 2. Convergence Warnings per Pine Script rev22 lines 428-429
        # Bearish Warning: USDT.D Rising AND Fed Liquidity Falling
        warn_bearish = usdt_up and (not fed_up)
        # Bullish Signal: USDT.D Falling AND Fed Liquidity Rising
        warn_bullish = (not usdt_up) and fed_up

        regime = MacroRegime.NEUTRAL_CHOP
        if warn_bearish:
            regime = MacroRegime.BEARISH_WARNING
        elif warn_bullish:
            regime = MacroRegime.BULLISH_SIGNAL

        # 3. Strength Calculation (0 to 100) per Pine Script lines 446-452
        usdt_component = min(abs(usdt_snapshot.z_score) * 20.0, 100.0)
        fed_component = min(abs(fed_snapshot.z_score) * 20.0, 100.0)

        if warn_bearish or warn_bullish:
            warning_strength = min((usdt_component + fed_component) / 2.0, 100.0)
        else:
            warning_strength = min(abs(usdt_component - fed_component) / 2.0, 50.0)

        strength_level = self._get_strength_level(warning_strength)

        return MacroRadarReport(
            regime=regime,
            warn_bearish=warn_bearish,
            warn_bullish=warn_bullish,
            warning_strength=warning_strength,
            strength_level=strength_level,
            fed_snapshot=fed_snapshot,
            usdt_snapshot=usdt_snapshot,
            timestamp_ns=max(fed_snapshot.timestamp_ns, usdt_snapshot.timestamp_ns),
        )

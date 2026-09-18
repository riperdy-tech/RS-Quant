"""Whale Positioning, Long/Short Ratio MACD, and Net Flow Decomposition per Pine Script rev22."""

from __future__ import annotations

import collections
import logging
import math
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger("quantdesk.data.positioning_feed")


@dataclass(frozen=True, slots=True)
class WhalePositioningSnapshot:
    """Snapshot of institutional positioning momentum and decomposed net flow."""

    timestamp_ns: int
    oi: float
    lsr: float
    long_contracts: float
    short_contracts: float
    d_long: float
    d_short: float
    net_flow_raw: float
    net_flow_zscore: float
    net_flow_direction: int  # +1 for net whale buying, -1 for net whale selling, 0 for flat
    ratio_ln: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    is_spike: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "oi": self.oi,
            "lsr": round(self.lsr, 3),
            "long_contracts": round(self.long_contracts, 1),
            "short_contracts": round(self.short_contracts, 1),
            "d_long": round(self.d_long, 1),
            "d_short": round(self.d_short, 1),
            "net_flow_raw": round(self.net_flow_raw, 1),
            "net_flow_zscore": round(self.net_flow_zscore, 2),
            "net_flow_direction": self.net_flow_direction,
            "ratio_ln": round(self.ratio_ln, 4),
            "macd_line": round(self.macd_line, 4),
            "macd_signal": round(self.macd_signal, 4),
            "macd_hist": round(self.macd_hist, 4),
            "is_spike": self.is_spike,
        }


class WhaleNetFlowCalculator:
    """Incremental calculator for Whale Long/Short Net Flow and Positioning MACD."""

    def __init__(
        self,
        fast_len: int = 12,
        slow_len: int = 26,
        signal_len: int = 9,
        lookback_zscore: int = 50,
        spike_threshold: float = 1.5,
    ) -> None:
        self.fast_len = fast_len
        self.slow_len = slow_len
        self.signal_len = signal_len
        self.lookback_zscore = lookback_zscore
        self.spike_threshold = spike_threshold

        self._prev_longs: float | None = None
        self._prev_shorts: float | None = None
        self._flow_history: collections.deque[float] = collections.deque(maxlen=lookback_zscore)

        # EMA state for MACD of ln(Longs / Shorts)
        self._ema_fast: float | None = None
        self._ema_slow: float | None = None
        self._ema_signal: float | None = None

    @staticmethod
    def decompose_contracts(oi: float, lsr: float) -> tuple[float, float]:
        """Calculates Longs and Shorts from Open Interest and Long/Short Ratio.

        Formula from Pine Script lines 183-186:
        Longs = OI * LSR / (LSR + 1.0)
        Shorts = OI / (LSR + 1.0)
        """
        safe_lsr = max(lsr, 1e-8)
        shorts = oi / (safe_lsr + 1.0)
        longs = oi * safe_lsr / (safe_lsr + 1.0)
        return longs, shorts

    def update(self, oi: float, lsr: float, timestamp_ns: int = 0) -> WhalePositioningSnapshot:
        longs, shorts = self.decompose_contracts(oi, lsr)

        # 1. Delta contracts and Net Flow
        if self._prev_longs is None or self._prev_shorts is None:
            d_long = 0.0
            d_short = 0.0
        else:
            d_long = longs - self._prev_longs
            d_short = shorts - self._prev_shorts

        self._prev_longs = longs
        self._prev_shorts = shorts

        # Net Flow Raw = dLong - dShort per Pine Script line 213
        net_flow_raw = d_long - d_short
        self._flow_history.append(net_flow_raw)

        # Rolling Z-score scaling per Pine Script lines 128-131 & 221
        n = len(self._flow_history)
        if n > 1:
            mean = sum(self._flow_history) / n
            var = sum((x - mean) ** 2 for x in self._flow_history) / n
            stdev = math.sqrt(var) if var > 0 else 1e-6
            z_score = (net_flow_raw - mean) / stdev
        else:
            z_score = 0.0

        direction = 1 if net_flow_raw > 0 else (-1 if net_flow_raw < 0 else 0)
        is_spike = abs(z_score) >= self.spike_threshold

        # 2. Positioning MACD on ln(Longs / Shorts) per Pine Script lines 115-122
        ratio_scaled = math.log(max(longs / max(shorts, 1e-10), 1e-10))

        alpha_fast = 2.0 / (self.fast_len + 1.0)
        alpha_slow = 2.0 / (self.slow_len + 1.0)
        alpha_sig = 2.0 / (self.signal_len + 1.0)

        if self._ema_fast is None:
            self._ema_fast = ratio_scaled
            self._ema_slow = ratio_scaled
            macd_line = 0.0
            self._ema_signal = 0.0
            macd_hist = 0.0
        else:
            self._ema_fast = alpha_fast * ratio_scaled + (1.0 - alpha_fast) * self._ema_fast
            self._ema_slow = alpha_slow * ratio_scaled + (1.0 - alpha_slow) * self._ema_slow
            macd_line = self._ema_fast - self._ema_slow

            if self._ema_signal is None:
                self._ema_signal = macd_line
            else:
                self._ema_signal = alpha_sig * macd_line + (1.0 - alpha_sig) * self._ema_signal
            macd_hist = macd_line - self._ema_signal

        return WhalePositioningSnapshot(
            timestamp_ns=timestamp_ns or int(time.time() * 1e9),
            oi=oi,
            lsr=lsr,
            long_contracts=longs,
            short_contracts=shorts,
            d_long=d_long,
            d_short=d_short,
            net_flow_raw=net_flow_raw,
            net_flow_zscore=z_score,
            net_flow_direction=direction,
            ratio_ln=ratio_scaled,
            macd_line=macd_line,
            macd_signal=self._ema_signal or 0.0,
            macd_hist=macd_hist,
            is_spike=is_spike,
        )

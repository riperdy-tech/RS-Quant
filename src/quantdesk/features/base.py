"""Incremental feature engine and feature contracts per §12."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from quantdesk.core.events import Envelope
from quantdesk.features.orderflow import (
    CVD,
    L1OFI,
    RankedMLOFI,
    TradeCluster,
    depth_imbalance,
    l1_imbalance,
    microprice,
)
from quantdesk.features.technicals import (
    ATR,
    EMA,
    RSI,
    VWAP,
    BollingerBands,
    Supertrend,
)


@dataclass(frozen=True, slots=True)
class FeatureValue:
    name: str
    value: float | str | None
    missing_reason: str | None
    definition_version: str
    instrument_id: str
    source_watermark_ns: int
    dependency_available_ns: int
    computed_ns: int
    warmup_met: bool
    validity_epoch: str


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    decision_seq: int
    available_ns: int
    features: tuple[FeatureValue, ...]
    source_watermark: int
    config_hash: str
    schema_hash: str


@runtime_checkable
class FeatureEngine(Protocol):
    def update(self, event: Envelope, book_view: Any = None) -> tuple[FeatureValue, ...]:
        ...

    def snapshot(self, decision_seq: int, available_ns: int) -> FeatureSnapshot:
        ...


class IncrementalFeatureEngine:
    """Concrete incremental feature engine per §12.

    Maintains technical and orderflow feature calculators across
    market events without future leakage.
    """

    def __init__(self, instrument_id: str = "BTCUSDT", config_hash: str = "default"):
        self.instrument_id = instrument_id
        self.config_hash = config_hash
        self.schema_hash = "features-v1"
        self.validity_epoch = "epoch-1"

        # Technicals
        self.atr14 = ATR(14)
        self.ema10 = EMA(10)
        self.ema30 = EMA(30)
        self.rsi14 = RSI(14)
        self.bb20 = BollingerBands(20)
        self.vwap = VWAP()
        self.supertrend = Supertrend(10, 3.0)

        # Bar memory for high_20_prior and low_20_prior
        self.prior_highs: deque[float] = deque(maxlen=20)
        self.prior_lows: deque[float] = deque(maxlen=20)

        # Rolling bar buffer for short-term Pine Script alpha factors (15s / 1m bars)
        self.bar_opens: deque[float] = deque(maxlen=40)
        self.bar_highs: deque[float] = deque(maxlen=40)
        self.bar_lows: deque[float] = deque(maxlen=40)
        self.bar_closes: deque[float] = deque(maxlen=40)
        self.bar_volumes: deque[float] = deque(maxlen=40)

        # Orderflow
        self.cvd = CVD()
        self.l1_ofi = L1OFI()
        self.mlofi = RankedMLOFI(ranks=5)
        self.trade_cluster = TradeCluster(time_limit_ns=50_000_000)

        # Rolling 1-second trades (timestamp_ns, signed_volume)
        self.rolling_1s_trades: deque[tuple[int, float]] = deque()

        # Current state values
        self._features: dict[str, FeatureValue] = {}
        self._watermark_ns = 0
        self.sweep_candidate: dict[str, Any] | None = None
        self.last_top5_range: tuple[float, float] | None = None

    def update(self, event: Envelope, book_view: Any = None) -> tuple[FeatureValue, ...]:
        """Update incremental features from incoming event."""
        updated: list[FeatureValue] = []
        available_ns = event.available_ns
        self._watermark_ns = max(self._watermark_ns, available_ns)

        payload_dict: dict[str, Any] = {}
        if isinstance(event.payload, (bytes, str)):
            try:
                decoded = json.loads(event.payload)
                if isinstance(decoded, dict):
                    payload_dict = decoded
            except Exception:
                payload_dict = {}
        elif isinstance(event.payload, dict):
            payload_dict = event.payload

        if event.event_type in ("BookSnapshot", "BookDelta", "Quote"):
            bids = payload_dict.get("bids", [])
            asks = payload_dict.get("asks", [])
            if bids and asks:
                best_bid_p, best_bid_s = float(bids[0][0]), float(bids[0][1])
                best_ask_p, best_ask_s = float(asks[0][0]), float(asks[0][1])
                mid_val = (best_bid_p + best_ask_p) / 2.0
                spread = 10000.0 * (best_ask_p - best_bid_p) / mid_val if mid_val > 0 else None
                l1_imb = l1_imbalance(best_bid_s, best_ask_s)
                micro = microprice(best_bid_p, best_ask_p, best_bid_s, best_ask_s)
                d5 = depth_imbalance([(float(p), float(s)) for p, s in bids],
                                     [(float(p), float(s)) for p, s in asks], 5)
                d20 = depth_imbalance([(float(p), float(s)) for p, s in bids],
                                      [(float(p), float(s)) for p, s in asks], 20)

                top5_prices = [float(p) for p, _ in bids[:5]] + [float(p) for p, _ in asks[:5]]
                if top5_prices:
                    self.last_top5_range = (min(top5_prices), max(top5_prices))

                ofi_val = self.l1_ofi.update(best_bid_p, best_ask_p, best_bid_s, best_ask_s)
                mlofi_val = self.mlofi.update(
                    [(float(p), float(s)) for p, s in bids],
                    [(float(p), float(s)) for p, s in asks],
                    available_ns,
                )

                # Check sweep candidate recovery within 500ms
                sweep_recovery_side = None
                sweep_extreme_price = None
                if self.sweep_candidate:
                    if (available_ns - self.sweep_candidate["completed_ns"]) <= 500_000_000:
                        pre_min, pre_max = self.sweep_candidate["pre_top5_range"]
                        if pre_min <= mid_val <= pre_max:
                            sweep_recovery_side = (
                                "BUY" if self.sweep_candidate["aggressor"] == "SELL" else "SELL"
                            )
                            sweep_extreme_price = self.sweep_candidate["extreme_price"]
                            self.sweep_candidate = None
                    else:
                        self.sweep_candidate = None

                cutoff = available_ns - 1_500_000_000
                while self.rolling_1s_trades and self.rolling_1s_trades[0][0] < cutoff:
                    self.rolling_1s_trades.popleft()
                vol_1s_current = sum(v for _, v in self.rolling_1s_trades)

                book_features: list[tuple[str, float | str | None]] = [
                    ("mid", mid_val),
                    ("spread_bps", spread),
                    ("l1_imbalance", l1_imb),
                    ("microprice", micro),
                    ("depth5_imbalance", d5),
                    ("depth20_imbalance", d20),
                    ("l1_ofi", ofi_val),
                    ("mlofi", mlofi_val),
                    ("volume_1s_signed", vol_1s_current),
                ]
                if sweep_recovery_side:
                    book_features.extend([
                        ("sweep_recovery_side", sweep_recovery_side),
                        ("sweep_extreme_price", sweep_extreme_price),
                    ])

                for name, val in book_features:
                    fv = FeatureValue(
                        name=name,
                        value=val,
                        missing_reason="INSUFFICIENT_DEPTH" if val is None else None,
                        definition_version="v1",
                        instrument_id=self.instrument_id,
                        source_watermark_ns=self._watermark_ns,
                        dependency_available_ns=available_ns,
                        computed_ns=available_ns,
                        warmup_met=val is not None,
                        validity_epoch=self.validity_epoch,
                    )
                    self._features[name] = fv
                    updated.append(fv)

        elif event.event_type == "BookInvalidated":
            self.l1_ofi = L1OFI()
            self.mlofi = RankedMLOFI(ranks=5)
            self.sweep_candidate = None
            for name in ["depth5_imbalance", "depth20_imbalance", "l1_ofi", "mlofi"]:
                if name in self._features:
                    self._features[name] = FeatureValue(
                        name=name,
                        value=None,
                        missing_reason="BOOK_INVALIDATED",
                        definition_version="v1",
                        instrument_id=self.instrument_id,
                        source_watermark_ns=self._watermark_ns,
                        dependency_available_ns=available_ns,
                        computed_ns=available_ns,
                        warmup_met=False,
                        validity_epoch=self.validity_epoch,
                    )
                    updated.append(self._features[name])

        elif event.event_type == "Trade":
            price = float(payload_dict.get("price", 0))
            size = float(payload_dict.get("size", payload_dict.get("lots", 0)))
            aggressor = payload_dict.get(
                "aggressor_side", payload_dict.get("aggressor", "BUY")
            )

            # CVD
            cvd_val = self.cvd.update(size, aggressor)

            # Rolling 1s signed volume
            signed_vol = size if aggressor == "BUY" else -size
            self.rolling_1s_trades.append((available_ns, signed_vol))
            cutoff = available_ns - 1_000_000_000
            while self.rolling_1s_trades and self.rolling_1s_trades[0][0] < cutoff:
                self.rolling_1s_trades.popleft()
            vol_1s = sum(v for _, v in self.rolling_1s_trades)

            # TradeCluster
            _flushed, cluster = self.trade_cluster.update(
                price, size, aggressor, available_ns, event.event_id
            )
            if cluster and cluster.get("is_sweep"):
                # Register sweep candidate with pre-cluster top5 range
                pre_range = self.last_top5_range or (price, price)
                is_buy_aggressor = cluster["aggressor"] == "BUY"
                extreme_p = cluster["max_price"] if is_buy_aggressor else cluster["min_price"]
                self.sweep_candidate = {
                    "aggressor": cluster["aggressor"],
                    "completed_ns": cluster["completed_ns"],
                    "extreme_price": extreme_p,
                    "pre_top5_range": pre_range,
                }

            trade_metrics: list[tuple[str, float | str | None]] = [
                ("cvd", cvd_val),
                ("volume_1s_signed", vol_1s),
            ]
            for t_name, t_val in trade_metrics:
                fv = FeatureValue(
                    name=t_name,
                    value=t_val,
                    missing_reason=None if t_val is not None else "NO_TRADES",
                    definition_version="v1",
                    instrument_id=self.instrument_id,
                    source_watermark_ns=self._watermark_ns,
                    dependency_available_ns=available_ns,
                    computed_ns=available_ns,
                    warmup_met=True,
                    validity_epoch=self.validity_epoch,
                )
                self._features[t_name] = fv
                updated.append(fv)

        elif event.event_type == "BarClosed":
            open_p = float(payload_dict.get("open_ticks", payload_dict.get("open", 0)))
            high_p = float(payload_dict.get("high_ticks", payload_dict.get("high", open_p)))
            low_p = float(payload_dict.get("low_ticks", payload_dict.get("low", open_p)))
            close_p = float(payload_dict.get("close_ticks", payload_dict.get("close", open_p)))
            vol_p = float(payload_dict.get("volume", payload_dict.get("volume_ticks", 10.0)))

            self.bar_opens.append(open_p)
            self.bar_highs.append(high_p)
            self.bar_lows.append(low_p)
            self.bar_closes.append(close_p)
            self.bar_volumes.append(vol_p)

            atr_val = self.atr14.update(high_p, low_p, close_p)
            ema10_val = self.ema10.update(close_p)
            ema30_val = self.ema30.update(close_p)
            rsi_val = self.rsi14.update(close_p)
            bb_lower, bb_mid, bb_upper = self.bb20.update(close_p)

            # 20 prior bars high/low (excluding current)
            high_20 = max(self.prior_highs) if len(self.prior_highs) == 20 else None
            low_20 = min(self.prior_lows) if len(self.prior_lows) == 20 else None
            self.prior_highs.append(high_p)
            self.prior_lows.append(low_p)

            # Short-term Pine Script alpha factors (Squeeze Momentum, McGinley, Chandelier)
            sq_val, sq_color = 0.0, "BLUE"
            mcg_val = close_p
            ch_long = high_p - 2.0 * (atr_val or 1.0)
            ch_short = low_p + 2.0 * (atr_val or 1.0)

            if len(self.bar_closes) >= 10:
                import numpy as np
                from quantdesk.features.ensemble_features import (
                    calculate_chandelier_exit,
                    calculate_mcginley_dynamic,
                    calculate_squeeze_momentum,
                )
                c_arr = np.array(self.bar_closes, dtype=float)
                h_arr = np.array(self.bar_highs, dtype=float)
                l_arr = np.array(self.bar_lows, dtype=float)

                sq_vals, sq_cols = calculate_squeeze_momentum(c_arr, h_arr, l_arr)
                sq_val = float(sq_vals[-1])
                sq_color = sq_cols[-1].value

                mcg_arr, _ = calculate_mcginley_dynamic(c_arr)
                mcg_val = float(mcg_arr[-1])

                ch_l, ch_s, _, _ = calculate_chandelier_exit(h_arr, l_arr, c_arr, length=min(14, len(c_arr)), mult=2.0)
                ch_long = float(ch_l[-1])
                ch_short = float(ch_s[-1])

            for name, val in [
                ("close", close_p),
                ("atr14", atr_val),
                ("ema10", ema10_val),
                ("ema30", ema30_val),
                ("rsi14", rsi_val),
                ("bollinger_lower", bb_lower),
                ("bollinger_mid", bb_mid),
                ("bollinger_upper", bb_upper),
                ("high_20_prior", high_20),
                ("low_20_prior", low_20),
                ("squeeze_val", sq_val),
                ("squeeze_color", sq_color),
                ("mcginley_value", mcg_val),
                ("chandelier_long_stop", ch_long),
                ("chandelier_short_stop", ch_short),
            ]:
                fv = FeatureValue(
                    name=name,
                    value=val,
                    missing_reason="WARMING_UP" if val is None else None,
                    definition_version="v1",
                    instrument_id=self.instrument_id,
                    source_watermark_ns=self._watermark_ns,
                    dependency_available_ns=available_ns,
                    computed_ns=available_ns,
                    warmup_met=val is not None,
                    validity_epoch=self.validity_epoch,
                )
                self._features[name] = fv
                updated.append(fv)

        elif event.event_type == "MacroLiquidityUpdated":
            macro_items = [
                ("macro_fed_liq_zscore", payload_dict.get("macro_fed_liq_zscore")),
                ("macro_fed_liq_trend", payload_dict.get("macro_fed_liq_trend")),
                ("macro_usdt_d_zscore", payload_dict.get("macro_usdt_d_zscore")),
                ("macro_usdt_d_slope", payload_dict.get("macro_usdt_d_slope")),
                ("macro_warning_strength", payload_dict.get("macro_warning_strength")),
                ("macro_regime", payload_dict.get("macro_regime")),
                ("warn_bearish", payload_dict.get("warn_bearish")),
                ("warn_bullish", payload_dict.get("warn_bullish")),
            ]
            for name, val in macro_items:
                fv = FeatureValue(
                    name=name,
                    value=val,
                    missing_reason=None if val is not None else "NO_MACRO_DATA",
                    definition_version="v1",
                    instrument_id=self.instrument_id,
                    source_watermark_ns=self._watermark_ns,
                    dependency_available_ns=available_ns,
                    computed_ns=available_ns,
                    warmup_met=val is not None,
                    validity_epoch=self.validity_epoch,
                )
                self._features[name] = fv
                updated.append(fv)

        elif event.event_type == "WhalePositioningUpdated":
            whale_items = [
                ("whale_ls_ratio_ln", payload_dict.get("whale_ls_ratio_ln")),
                ("whale_ls_macd_hist", payload_dict.get("whale_ls_macd_hist")),
                ("whale_net_flow_zscore", payload_dict.get("whale_net_flow_zscore")),
                ("whale_net_flow_direction", payload_dict.get("whale_net_flow_direction")),
                ("whale_is_spike", payload_dict.get("whale_is_spike")),
            ]
            for name, val in whale_items:
                fv = FeatureValue(
                    name=name,
                    value=val,
                    missing_reason=None if val is not None else "NO_POSITIONING_DATA",
                    definition_version="v1",
                    instrument_id=self.instrument_id,
                    source_watermark_ns=self._watermark_ns,
                    dependency_available_ns=available_ns,
                    computed_ns=available_ns,
                    warmup_met=val is not None,
                    validity_epoch=self.validity_epoch,
                )
                self._features[name] = fv
                updated.append(fv)

        return tuple(updated)

    def snapshot(self, decision_seq: int, available_ns: int) -> FeatureSnapshot:
        """Produce frozen deterministic feature snapshot sorted by name."""
        ordered = tuple(self._features[k] for k in sorted(self._features.keys()))
        return FeatureSnapshot(
            decision_seq=decision_seq,
            available_ns=available_ns,
            features=ordered,
            source_watermark=self._watermark_ns,
            config_hash=self.config_hash,
            schema_hash=self.schema_hash,
        )

    def as_dict(self) -> dict[str, Any]:
        """Convenience dictionary for strategy evaluation."""
        return {k: v.value for k, v in self._features.items()}

"""Curated 12-Factor Ensemble Feature Engine for QuantDesk.

Implements the 12 orthogonal quantitative features reverse-engineered from
the institutional Pine Script strategy ('CH종합 DIY Custom rev15'), removing
the 36-indicator multicollinear redundancy and focusing on pure orthogonal alpha:
1. Nadaraya-Watson Rational Quadratic Kernel (RQK)
2. McGinley Dynamic Adaptive Moving Average
3. LazyBear Squeeze Momentum (BB vs. KC Linreg Histogram & 4-Color state)
4. Chaikin Money Flow (CMF, 20 periods)
5. Schaff Trend Cycle (STC, 23/50/10)
6. Qualitative Quantitative Estimation (QQE Mod, 6/5/3)
7. Directional Movement Index & ADX (14 periods)
8. Chandelier Exit (22-bar ATR trailing stop lines)
9. Donchian Channel Breakout (17-bar envelope)
10. Annualized Historical Volatility (HV, 10 periods)
11. Volume Delta & Relative Volume (20 periods)
12. Composite Candle Score (0-10), EMA(7) Slope Velocity, and Trailing SMA(15) Gate
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

import numpy as np


class RegimeVelocity(str, Enum):
    STRONG_BULL = "STRONG_BULL"      # slope >= 0.50
    MEDIUM_BULL = "MEDIUM_BULL"      # 0.25 <= slope < 0.50
    WEAK_BULL = "WEAK_BULL"          # 0.15 < slope < 0.25
    SIDEWAYS = "SIDEWAYS"            # |slope| <= 0.15 (Veto zone)
    WEAK_BEAR = "WEAK_BEAR"          # -0.25 < slope < -0.15
    MEDIUM_BEAR = "MEDIUM_BEAR"      # -0.50 < slope <= -0.25
    STRONG_BEAR = "STRONG_BEAR"      # slope <= -0.50


class SqueezeColor(str, Enum):
    BLUE = "BLUE"       # Val > 0 and expanding (Bullish acceleration)
    GREEN = "GREEN"     # Val > 0 and contracting (Bullish exhaustion)
    RED = "RED"         # Val <= 0 and expanding (Bearish acceleration)
    ORANGE = "ORANGE"   # Val <= 0 and contracting (Bearish exhaustion)


@dataclass
class EnsembleBarState:
    """Snapshot of the 12 curated features and consensus score for a single bar."""

    timestamp: int
    close: float
    high: float
    low: float
    volume: float

    # 1. RQK
    rqk_value: float = 0.0
    rqk_trend: int = 0  # +1 long, -1 short

    # 2. McGinley Dynamic
    mcginley_value: float = 0.0
    mcginley_trend: int = 0

    # 3. Squeeze Momentum
    squeeze_val: float = 0.0
    squeeze_color: SqueezeColor = SqueezeColor.BLUE
    squeeze_long_ok: bool = False
    squeeze_short_ok: bool = False

    # 4. Chaikin Money Flow
    cmf_value: float = 0.0
    cmf_trend: int = 0

    # 5. Schaff Trend Cycle (STC)
    stc_value: float = 50.0
    stc_trend: int = 0

    # 6. QQE Mod
    qqe_line: float = 0.0
    qqe_trend: int = 0

    # 7. ADX / DMI
    adx_value: float = 0.0
    di_plus: float = 0.0
    di_minus: float = 0.0
    adx_trend: int = 0

    # 8. Chandelier Exit
    long_stop: float = 0.0
    short_stop: float = 0.0
    chandelier_dir: int = 1

    # 9. Donchian 17-bar envelope
    donchian_high: float = 0.0
    donchian_low: float = 0.0
    is_donchian_breakout_long: bool = False
    is_donchian_breakout_short: bool = False

    # 10. Historical Volatility
    hv_annualized: float = 0.0
    atr_14: float = 0.0

    # 11. Volume Delta
    volume_delta: float = 0.0
    volume_ratio: float = 1.0

    # 12. Composite Consensus Score & Velocity
    total_long_votes: int = 0
    total_evaluated: int = 10
    raw_score: float = 5.0
    rounded_score: int = 5
    score_ema: float = 5.0
    score_slope: float = 0.0
    regime: RegimeVelocity = RegimeVelocity.SIDEWAYS
    trailing_score_sma15: float = 5.0

    # Gate evaluations
    score_gate_long_ok: bool = True   # trailing_score_sma15 > 4.2
    score_gate_short_ok: bool = True  # trailing_score_sma15 < 4.9


# =========================================================================
# Vectorized Mathematical Implementations
# =========================================================================


def calculate_rqk(prices: np.ndarray, h: float = 8.0, r: float = 8.0) -> tuple[np.ndarray, np.ndarray]:
    """Nadaraya-Watson Rational Quadratic Kernel regression.

    Returns:
        (y_hat, trend_direction) where trend_direction is +1 (up) or -1 (down).
    """
    n = len(prices)
    y_hat = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    if n == 0:
        return y_hat, trend

    max_lookback = int(h * 3.5)
    for t in range(n):
        lookback = min(t, max_lookback)
        if lookback < 2:
            y_hat[t] = prices[t]
            continue
        i_vec = np.arange(lookback + 1)
        w = np.power(1.0 + (i_vec**2) / (2.0 * r * (h**2)), -r)
        window = prices[t - lookback : t + 1][::-1]
        w_sum = np.sum(w)
        y_hat[t] = np.sum(window * w) / (w_sum if w_sum > 0 else 1.0)

        if t > 0:
            trend[t] = 1 if y_hat[t] > y_hat[t - 1] else (-1 if y_hat[t] < y_hat[t - 1] else trend[t - 1])

    return y_hat, trend


def calculate_mcginley_dynamic(prices: np.ndarray, length: int = 14) -> tuple[np.ndarray, np.ndarray]:
    """McGinley Dynamic adaptive moving average with (Price / Dynamic)^4 speed adjustment.

    Returns:
        (mg_array, trend_direction) where trend_direction is +1 (price > mg) or -1.
    """
    n = len(prices)
    mg = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    if n == 0:
        return mg, trend

    mg[0] = prices[0]
    for t in range(1, n):
        prev = mg[t - 1] if mg[t - 1] > 0 else prices[t]
        ratio = prices[t] / prev
        denominator = length * math.pow(ratio, 4)
        if denominator == 0:
            mg[t] = prev
        else:
            mg[t] = prev + (prices[t] - prev) / denominator
        trend[t] = 1 if prices[t] > mg[t] else (-1 if prices[t] < mg[t] else trend[t - 1])

    return mg, trend


def calculate_squeeze_momentum(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    bb_len: int = 20,
    bb_mult: float = 2.0,
    kc_len: int = 20,
    kc_mult: float = 1.5,
) -> tuple[np.ndarray, list[SqueezeColor]]:
    """LazyBear Squeeze Momentum linear regression histogram & 4-color state.

    Returns:
        (sqz_val, colors)
    """
    n = len(close)
    sqz_val = np.zeros(n)
    colors: list[SqueezeColor] = [SqueezeColor.BLUE] * n
    if n < kc_len:
        return sqz_val, colors

    for t in range(kc_len - 1, n):
        sub_c = close[t - kc_len + 1 : t + 1]
        sub_h = high[t - kc_len + 1 : t + 1]
        sub_l = low[t - kc_len + 1 : t + 1]

        hh = np.max(sub_h)
        ll = np.min(sub_l)
        sma_c = np.mean(sub_c)
        mid = ((hh + ll) / 2.0 + sma_c) / 2.0

        y = sub_c - mid
        x = np.arange(kc_len)
        # Linear regression endpoint at index (kc_len - 1)
        x_mean = (kc_len - 1) / 2.0
        y_mean = np.mean(y)
        numerator = np.sum((x - x_mean) * (y - y_mean))
        denominator = np.sum((x - x_mean) ** 2)
        slope = numerator / denominator if denominator != 0 else 0.0
        intercept = y_mean - slope * x_mean
        val = slope * (kc_len - 1) + intercept
        sqz_val[t] = val

        prev = sqz_val[t - 1] if t > 0 else 0.0
        if val > 0:
            col = SqueezeColor.BLUE if val > prev else SqueezeColor.GREEN
        else:
            col = SqueezeColor.RED if val < prev else SqueezeColor.ORANGE
        colors[t] = col

    return sqz_val, colors


def calculate_chaikin_money_flow(
    close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: np.ndarray, length: int = 20
) -> tuple[np.ndarray, np.ndarray]:
    """Chaikin Money Flow (CMF).

    Returns:
        (cmf_array, trend_direction) where trend is +1 (cmf > 0) or -1.
    """
    n = len(close)
    cmf = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    if n < length:
        return cmf, trend

    denom_hl = high - low
    mfm = np.where(denom_hl > 0, ((close - low) - (high - close)) / denom_hl, 0.0)
    mfv = mfm * volume

    for t in range(length - 1, n):
        vol_sum = np.sum(volume[t - length + 1 : t + 1])
        mfv_sum = np.sum(mfv[t - length + 1 : t + 1])
        val = mfv_sum / vol_sum if vol_sum > 0 else 0.0
        cmf[t] = val
        trend[t] = 1 if val > 0 else -1

    return cmf, trend


def calculate_schaff_trend_cycle(
    close: np.ndarray, fast: int = 23, slow: int = 50, cycle: int = 10, d1: int = 3, d2: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Schaff Trend Cycle (STC) with double-stochastized MACD.

    Returns:
        (stc_array, trend_direction) where trend is +1 (stc >= 75), -1 (stc <= 25), 0 otherwise.
    """
    n = len(close)
    stc = np.full(n, 50.0)
    trend = np.zeros(n, dtype=int)
    if n < slow + cycle:
        return stc, trend

    # 1. MACD
    alpha_f = 2.0 / (fast + 1.0)
    alpha_s = 2.0 / (slow + 1.0)
    ema_f = np.zeros(n)
    ema_s = np.zeros(n)
    ema_f[0] = close[0]
    ema_s[0] = close[0]
    for t in range(1, n):
        ema_f[t] = alpha_f * close[t] + (1 - alpha_f) * ema_f[t - 1]
        ema_s[t] = alpha_s * close[t] + (1 - alpha_s) * ema_s[t - 1]
    macd = ema_f - ema_s

    # 2. First stochastic of MACD
    k1 = np.zeros(n)
    for t in range(cycle - 1, n):
        sub_m = macd[t - cycle + 1 : t + 1]
        ll = np.min(sub_m)
        hh = np.max(sub_m)
        diff = hh - ll
        k1[t] = 100.0 * (macd[t] - ll) / diff if diff > 0 else 50.0

    # 3. EMA(k1, d1)
    d = np.zeros(n)
    alpha_d1 = 2.0 / (d1 + 1.0)
    d[0] = k1[0]
    for t in range(1, n):
        d[t] = alpha_d1 * k1[t] + (1 - alpha_d1) * d[t - 1]

    # 4. Second stochastic of d
    k2 = np.zeros(n)
    for t in range(cycle - 1, n):
        sub_d = d[t - cycle + 1 : t + 1]
        ll2 = np.min(sub_d)
        hh2 = np.max(sub_d)
        diff2 = hh2 - ll2
        k2[t] = 100.0 * (d[t] - ll2) / diff2 if diff2 > 0 else 50.0

    # 5. EMA(k2, d2)
    alpha_d2 = 2.0 / (d2 + 1.0)
    stc_out = np.zeros(n)
    stc_out[0] = k2[0]
    for t in range(1, n):
        stc_out[t] = alpha_d2 * k2[t] + (1 - alpha_d2) * stc_out[t - 1]
        stc_out[t] = max(0.0, min(100.0, stc_out[t]))
        if stc_out[t] >= 75.0:
            trend[t] = 1
        elif stc_out[t] <= 25.0:
            trend[t] = -1
        else:
            trend[t] = 0

    return stc_out, trend


def calculate_qqe_mod(
    close: np.ndarray, rsi_len: int = 6, sf: int = 5, qqe_factor: float = 3.0
) -> tuple[np.ndarray, np.ndarray]:
    """Qualitative Quantitative Estimation (QQE Mod).

    Returns:
        (qqe_line, trend_direction) where trend is +1 or -1.
    """
    n = len(close)
    qqe_line = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    if n < rsi_len * 2:
        return qqe_line, trend

    # Wilder's RSI
    deltas = np.diff(close, prepend=close[0])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.zeros(n)
    avg_loss = np.zeros(n)
    alpha_rsi = 1.0 / rsi_len
    avg_gain[0] = gains[0]
    avg_loss[0] = losses[0]
    for t in range(1, n):
        avg_gain[t] = alpha_rsi * gains[t] + (1 - alpha_rsi) * avg_gain[t - 1]
        avg_loss[t] = alpha_rsi * losses[t] + (1 - alpha_rsi) * avg_loss[t - 1]

    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, 100.0), where=avg_loss > 0)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # RSI MA
    rsi_ma = np.zeros(n)
    alpha_sf = 2.0 / (sf + 1.0)
    rsi_ma[0] = rsi[0]
    for t in range(1, n):
        rsi_ma[t] = alpha_sf * rsi[t] + (1 - alpha_sf) * rsi_ma[t - 1]

    # DAR (Dynamic Average Range)
    atr_rsi = np.abs(np.diff(rsi_ma, prepend=rsi_ma[0]))
    wilder_p = rsi_len * 2 - 1
    alpha_wp = 1.0 / wilder_p
    dar = np.zeros(n)
    dar[0] = atr_rsi[0]
    for t in range(1, n):
        dar[t] = alpha_wp * atr_rsi[t] + (1 - alpha_wp) * dar[t - 1]
    dar = dar * qqe_factor

    long_band = np.zeros(n)
    short_band = np.zeros(n)
    tr = 1
    for t in range(1, n):
        new_long = rsi_ma[t] - dar[t]
        new_short = rsi_ma[t] + dar[t]
        long_band[t] = max(long_band[t - 1], new_long) if (rsi_ma[t - 1] > long_band[t - 1] and rsi_ma[t] > long_band[t - 1]) else new_long
        short_band[t] = min(short_band[t - 1], new_short) if (rsi_ma[t - 1] < short_band[t - 1] and rsi_ma[t] < short_band[t - 1]) else new_short

        if rsi_ma[t] > short_band[t - 1]:
            tr = 1
        elif rsi_ma[t] < long_band[t - 1]:
            tr = -1
        trend[t] = tr
        tl = long_band[t] if tr == 1 else short_band[t]
        qqe_line[t] = tl - 50.0

    return qqe_line, trend


def calculate_adx_dmi(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Directional Movement Index & ADX.

    Returns:
        (adx, di_plus, di_minus, trend_direction) where trend is +1 (bullish), -1 (bearish), 0 (chop).
    """
    n = len(close)
    adx = np.zeros(n)
    di_plus = np.zeros(n)
    di_minus = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    if n < length * 2:
        return adx, di_plus, di_minus, trend

    tr = np.zeros(n)
    dm_plus = np.zeros(n)
    dm_minus = np.zeros(n)

    tr[0] = high[0] - low[0]
    for t in range(1, n):
        hl = high[t] - low[t]
        hc = abs(high[t] - close[t - 1])
        lc = abs(low[t] - close[t - 1])
        tr[t] = max(hl, hc, lc)

        up = high[t] - high[t - 1]
        down = low[t - 1] - low[t]
        dm_plus[t] = up if (up > down and up > 0) else 0.0
        dm_minus[t] = down if (down > up and down > 0) else 0.0

    # Wilder smoothing
    alpha = 1.0 / length
    atr_smooth = np.zeros(n)
    dmp_smooth = np.zeros(n)
    dmm_smooth = np.zeros(n)
    atr_smooth[0] = tr[0]
    dmp_smooth[0] = dm_plus[0]
    dmm_smooth[0] = dm_minus[0]

    for t in range(1, n):
        atr_smooth[t] = alpha * tr[t] + (1 - alpha) * atr_smooth[t - 1]
        dmp_smooth[t] = alpha * dm_plus[t] + (1 - alpha) * dmp_smooth[t - 1]
        dmm_smooth[t] = alpha * dm_minus[t] + (1 - alpha) * dmm_smooth[t - 1]

        di_plus[t] = 100.0 * dmp_smooth[t] / (atr_smooth[t] if atr_smooth[t] > 0 else 1.0)
        di_minus[t] = 100.0 * dmm_smooth[t] / (atr_smooth[t] if atr_smooth[t] > 0 else 1.0)

    # DX & ADX
    sum_di = di_plus + di_minus
    diff_di = np.abs(di_plus - di_minus)
    dx = np.divide(100.0 * diff_di, sum_di, out=np.zeros_like(diff_di), where=sum_di > 0)

    adx[0] = dx[0]
    for t in range(1, n):
        adx[t] = alpha * dx[t] + (1 - alpha) * adx[t - 1]
        if adx[t] >= 18.0:
            if di_plus[t] > di_minus[t]:
                trend[t] = 1
            elif di_minus[t] > di_plus[t]:
                trend[t] = -1
        else:
            trend[t] = 0

    return adx, di_plus, di_minus, trend


def calculate_chandelier_exit(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 22, mult: float = 3.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Chandelier Exit ATR trailing stop lines.

    Returns:
        (long_stop, short_stop, direction, atr_array)
    """
    n = len(close)
    long_stop = np.zeros(n)
    short_stop = np.zeros(n)
    direction = np.ones(n, dtype=int)
    atr = np.zeros(n)
    if n < length:
        return long_stop, short_stop, direction, atr

    # Calculate ATR
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for t in range(1, n):
        hl = high[t] - low[t]
        hc = abs(high[t] - close[t - 1])
        lc = abs(low[t] - close[t - 1])
        tr[t] = max(hl, hc, lc)

    alpha_atr = 1.0 / length
    atr[0] = tr[0]
    for t in range(1, n):
        atr[t] = alpha_atr * tr[t] + (1 - alpha_atr) * atr[t - 1]

    for t in range(length - 1, n):
        sub_c = close[t - length + 1 : t + 1]
        hh = np.max(sub_c)
        ll = np.min(sub_c)
        dist = mult * atr[t]

        l_stop = hh - dist
        s_stop = ll + dist

        prev_l = long_stop[t - 1] if t > 0 else l_stop
        prev_s = short_stop[t - 1] if t > 0 else s_stop

        if t > 0 and close[t - 1] > prev_l:
            long_stop[t] = max(l_stop, prev_l)
        else:
            long_stop[t] = l_stop

        if t > 0 and close[t - 1] < prev_s:
            short_stop[t] = min(s_stop, prev_s)
        else:
            short_stop[t] = s_stop

        if t > 0:
            if close[t] > prev_s:
                direction[t] = 1
            elif close[t] < prev_l:
                direction[t] = -1
            else:
                direction[t] = direction[t - 1]

    return long_stop, short_stop, direction, atr


def calculate_donchian_channels(high: np.ndarray, low: np.ndarray, length: int = 17) -> tuple[np.ndarray, np.ndarray]:
    """Donchian 17-bar envelope (prior bars excluding current bar).

    Returns:
        (upper_band, lower_band)
    """
    n = len(high)
    upper = np.zeros(n)
    lower = np.zeros(n)
    if n < length + 1:
        return upper, lower

    for t in range(length, n):
        upper[t] = np.max(high[t - length : t])
        lower[t] = np.min(low[t - length : t])

    return upper, lower


def calculate_historical_volatility(close: np.ndarray, length: int = 10) -> np.ndarray:
    """Annualized Historical Volatility (HV)."""
    n = len(close)
    hv = np.zeros(n)
    if n < length + 1:
        return hv

    log_returns = np.diff(np.log(np.maximum(close, 1e-8)), prepend=0.0)
    sqrt_365 = math.sqrt(365.0)

    for t in range(length, n):
        std = np.std(log_returns[t - length + 1 : t + 1], ddof=1)
        hv[t] = std * sqrt_365 * 100.0

    return hv


def calculate_volume_delta(
    close: np.ndarray, open_p: np.ndarray, volume: np.ndarray, length: int = 20
) -> tuple[np.ndarray, np.ndarray]:
    """Volume Delta & Relative Volume ratio.

    Returns:
        (delta_array, rvol_array)
    """
    n = len(close)
    delta = np.zeros(n)
    rvol = np.ones(n)
    if n < length:
        return delta, rvol

    up_down = np.where(close >= open_p, volume, -volume)
    for t in range(length - 1, n):
        sub_v = volume[t - length + 1 : t + 1]
        v_sma = np.mean(sub_v) if np.mean(sub_v) > 0 else 1.0
        delta[t] = up_down[t]
        rvol[t] = volume[t] / v_sma

    return delta, rvol


def calculate_consensus_score_pipeline(
    trends: dict[str, np.ndarray],
    weights: dict[str, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[RegimeVelocity], np.ndarray]:
    """Calculates the composite Candle Score (0-10), EMA(7) slope, and trailing SMA(15).

    Returns:
        (raw_score, rounded_score, score_ema, score_slope, regimes, score_sma15)
    """
    indicator_names = list(trends.keys())
    k = len(indicator_names)
    if k == 0:
        raise ValueError("Must provide at least one indicator trend array")

    n = len(trends[indicator_names[0]])
    raw_score = np.zeros(n)
    rounded_score = np.zeros(n, dtype=int)
    score_ema = np.zeros(n)
    score_slope = np.zeros(n)
    regimes: list[RegimeVelocity] = [RegimeVelocity.SIDEWAYS] * n
    score_sma15 = np.full(n, 5.0)

    # 1. Total long count and normalized raw score (0-10)
    for t in range(n):
        if weights:
            total_w = sum(max(0.01, float(weights.get(name, 1.0))) for name in indicator_names)
            weighted_long = sum(
                max(0.01, float(weights.get(name, 1.0)))
                for name in indicator_names
                if trends[name][t] == 1
            )
            raw = (weighted_long / total_w) * 10.0 if total_w > 0 else 5.0
        else:
            long_count = sum(1 for name in indicator_names if trends[name][t] == 1)
            raw = (long_count / float(k)) * 10.0
        raw_score[t] = raw
        rounded_score[t] = int(round(raw))

    # 2. EMA(7) of raw score
    alpha_7 = 2.0 / (7.0 + 1.0)
    score_ema[0] = raw_score[0]
    for t in range(1, n):
        score_ema[t] = alpha_7 * raw_score[t] + (1.0 - alpha_7) * score_ema[t - 1]
        slope = score_ema[t] - score_ema[t - 1]
        score_slope[t] = slope

        # 7-Regime classification
        abs_slope = abs(slope)
        if abs_slope <= 0.15:
            regimes[t] = RegimeVelocity.SIDEWAYS
        elif slope > 0.15:
            if slope >= 0.50:
                regimes[t] = RegimeVelocity.STRONG_BULL
            elif slope >= 0.25:
                regimes[t] = RegimeVelocity.MEDIUM_BULL
            else:
                regimes[t] = RegimeVelocity.WEAK_BULL
        else:  # slope < -0.15
            if slope <= -0.50:
                regimes[t] = RegimeVelocity.STRONG_BEAR
            elif slope <= -0.25:
                regimes[t] = RegimeVelocity.MEDIUM_BEAR
            else:
                regimes[t] = RegimeVelocity.WEAK_BEAR

    # 3. Trailing SMA(15) of rounded score (prior bars excluding current bar, per Pine Script)
    for t in range(15, n):
        score_sma15[t] = np.mean(rounded_score[t - 15 : t])

    return raw_score, rounded_score, score_ema, score_slope, regimes, score_sma15


# =========================================================================
# High-Level Curated Feature Extractor
# =========================================================================


class CuratedEnsembleExtractor:
    """Extracts the 12 curated orthogonal alpha features and manages the consensus state."""

    def __init__(self, bb_offset_factor: float = 0.2) -> None:
        self.bb_offset_factor = bb_offset_factor

    def compute_all(
        self,
        timestamps: Sequence[int],
        opens: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        volumes: Sequence[float],
        weights: dict[str, float] | None = None,
    ) -> list[EnsembleBarState]:
        """Runs the complete vectorized feature pipeline across OHLCV history."""
        n = len(closes)
        if n == 0:
            return []

        c_arr = np.array(closes, dtype=float)
        o_arr = np.array(opens, dtype=float)
        h_arr = np.array(highs, dtype=float)
        l_arr = np.array(lows, dtype=float)
        v_arr = np.array(volumes, dtype=float)

        # 1. RQK
        rqk_val, rqk_trend = calculate_rqk(c_arr)
        # 2. McGinley Dynamic
        mg_val, mg_trend = calculate_mcginley_dynamic(c_arr)
        # 3. Squeeze Momentum
        sqz_val, sqz_colors = calculate_squeeze_momentum(c_arr, h_arr, l_arr)
        # 4. Chaikin Money Flow
        cmf_val, cmf_trend = calculate_chaikin_money_flow(c_arr, h_arr, l_arr, v_arr)
        # 5. Schaff Trend Cycle
        stc_val, stc_trend = calculate_schaff_trend_cycle(c_arr)
        # 6. QQE Mod
        qqe_val, qqe_trend = calculate_qqe_mod(c_arr)
        # 7. ADX / DMI
        adx_val, di_plus, di_minus, adx_trend = calculate_adx_dmi(h_arr, l_arr, c_arr)
        # 8. Chandelier Exit
        long_stop, short_stop, chandelier_dir, atr_arr = calculate_chandelier_exit(h_arr, l_arr, c_arr)
        # 9. Donchian 17
        donch_high, donch_low = calculate_donchian_channels(h_arr, l_arr, 17)
        # 10. Historical Volatility
        hv_arr = calculate_historical_volatility(c_arr)
        # 11. Volume Delta
        v_delta, v_ratio = calculate_volume_delta(c_arr, o_arr, v_arr)

        # 12. Consensus Ensemble Scoring (10 core directional indicators)
        indicator_trends = {
            "rqk": rqk_trend,
            "mcginley": mg_trend,
            "squeeze": np.where((np.array(sqz_colors) == SqueezeColor.BLUE) | (np.array(sqz_colors) == SqueezeColor.ORANGE), 1, -1),
            "cmf": cmf_trend,
            "stc": stc_trend,
            "qqe": qqe_trend,
            "adx": adx_trend,
            "chandelier": chandelier_dir,
            "volume_delta": np.where(v_delta > 0, 1, -1),
            "donchian": np.where(c_arr > donch_high, 1, np.where(c_arr < donch_low, -1, 0)),
        }

        raw_s, round_s, s_ema, s_slope, regimes, s_sma15 = calculate_consensus_score_pipeline(indicator_trends, weights=weights)

        results: list[EnsembleBarState] = []
        for t in range(n):
            c_val = c_arr[t]
            atr_val = atr_arr[t]
            sqz_col = sqz_colors[t]
            sqz_v = sqz_val[t]

            # Deadband offset: 0.2 * ATR
            deadband = self.bb_offset_factor * atr_val if atr_val > 0 else 0.0
            in_offset = abs(sqz_v) <= deadband

            sqz_long_ok = (sqz_col in (SqueezeColor.BLUE, SqueezeColor.ORANGE)) and not in_offset
            sqz_short_ok = (sqz_col in (SqueezeColor.GREEN, SqueezeColor.RED)) and not in_offset

            # Donchian Breakout
            breakout_long = (t >= 17) and (c_val > donch_high[t])
            breakout_short = (t >= 17) and (c_val < donch_low[t])

            # Trailing Score Gating (Pine Script: > 4.2 for long, < 4.9 for short)
            sma15_val = s_sma15[t]
            gate_long = sma15_val > 4.2
            gate_short = sma15_val < 4.9

            state = EnsembleBarState(
                timestamp=int(timestamps[t]),
                close=c_val,
                high=h_arr[t],
                low=l_arr[t],
                volume=v_arr[t],
                rqk_value=rqk_val[t],
                rqk_trend=int(rqk_trend[t]),
                mcginley_value=mg_val[t],
                mcginley_trend=int(mg_trend[t]),
                squeeze_val=sqz_v,
                squeeze_color=sqz_col,
                squeeze_long_ok=sqz_long_ok,
                squeeze_short_ok=sqz_short_ok,
                cmf_value=cmf_val[t],
                cmf_trend=int(cmf_trend[t]),
                stc_value=stc_val[t],
                stc_trend=int(stc_trend[t]),
                qqe_line=qqe_val[t],
                qqe_trend=int(qqe_trend[t]),
                adx_value=adx_val[t],
                di_plus=di_plus[t],
                di_minus=di_minus[t],
                adx_trend=int(adx_trend[t]),
                long_stop=long_stop[t],
                short_stop=short_stop[t],
                chandelier_dir=int(chandelier_dir[t]),
                donchian_high=donch_high[t],
                donchian_low=donch_low[t],
                is_donchian_breakout_long=breakout_long,
                is_donchian_breakout_short=breakout_short,
                hv_annualized=hv_arr[t],
                atr_14=atr_val,
                volume_delta=v_delta[t],
                volume_ratio=v_ratio[t],
                total_long_votes=int(round(raw_s[t] / 10.0 * 10)),
                total_evaluated=10,
                raw_score=raw_s[t],
                rounded_score=int(round_s[t]),
                score_ema=s_ema[t],
                score_slope=s_slope[t],
                regime=regimes[t],
                trailing_score_sma15=sma15_val,
                score_gate_long_ok=gate_long,
                score_gate_short_ok=gate_short,
            )
            results.append(state)

        return results

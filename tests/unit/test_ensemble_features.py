"""Unit tests for curated 12-factor ensemble feature extractor."""

import numpy as np
import pytest

from quantdesk.features.ensemble_features import (
    CuratedEnsembleExtractor,
    EnsembleBarState,
    RegimeVelocity,
    SqueezeColor,
    calculate_adx_dmi,
    calculate_chaikin_money_flow,
    calculate_chandelier_exit,
    calculate_consensus_score_pipeline,
    calculate_donchian_channels,
    calculate_historical_volatility,
    calculate_mcginley_dynamic,
    calculate_qqe_mod,
    calculate_rqk,
    calculate_schaff_trend_cycle,
    calculate_squeeze_momentum,
    calculate_volume_delta,
)


def generate_synthetic_ohlcv(n: int = 100, trend: float = 0.5) -> dict[str, np.ndarray]:
    """Generates synthetic trended OHLCV series for testing."""
    np.random.seed(42)
    t = np.arange(n)
    close = 100.0 + trend * t + np.sin(t / 5.0) * 5.0 + np.random.normal(0, 1.0, n)
    high = close + np.random.uniform(0.5, 2.0, n)
    low = close - np.random.uniform(0.5, 2.0, n)
    open_p = (high + low) / 2.0 + np.random.normal(0, 0.5, n)
    volume = np.random.uniform(100.0, 1000.0, n)
    timestamps = 1700000000 + t * 7200  # 2h intervals

    return {
        "timestamps": timestamps,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_rqk_calculation():
    data = generate_synthetic_ohlcv(50, trend=1.0)
    y_hat, trend = calculate_rqk(data["close"], h=8.0, r=8.0)
    assert len(y_hat) == 50
    assert len(trend) == 50
    # In an uptrend, y_hat should be generally increasing
    assert y_hat[-1] > y_hat[0]
    assert np.all(np.isin(trend, [-1, 0, 1]))


def test_mcginley_dynamic():
    data = generate_synthetic_ohlcv(50, trend=1.0)
    mg, trend = calculate_mcginley_dynamic(data["close"], length=14)
    assert len(mg) == 50
    assert mg[-1] > mg[0]
    assert trend[-1] == 1  # Price is above dynamic moving average in uptrend


def test_squeeze_momentum():
    data = generate_synthetic_ohlcv(60)
    sqz_val, colors = calculate_squeeze_momentum(data["close"], data["high"], data["low"])
    assert len(sqz_val) == 60
    assert len(colors) == 60
    assert all(isinstance(c, SqueezeColor) for c in colors)


def test_chaikin_money_flow():
    data = generate_synthetic_ohlcv(50)
    cmf, trend = calculate_chaikin_money_flow(
        data["close"], data["high"], data["low"], data["volume"], length=20
    )
    assert len(cmf) == 50
    assert np.all(cmf >= -1.0) and np.all(cmf <= 1.0)


def test_schaff_trend_cycle():
    data = generate_synthetic_ohlcv(80)
    stc, trend = calculate_schaff_trend_cycle(data["close"])
    assert len(stc) == 80
    assert np.all(stc >= 0.0) and np.all(stc <= 100.0)


def test_qqe_mod():
    data = generate_synthetic_ohlcv(60)
    qqe_line, trend = calculate_qqe_mod(data["close"])
    assert len(qqe_line) == 60
    assert len(trend) == 60


def test_adx_dmi():
    data = generate_synthetic_ohlcv(60, trend=1.5)
    adx, di_p, di_m, trend = calculate_adx_dmi(data["high"], data["low"], data["close"], length=14)
    assert len(adx) == 60
    assert np.all(adx >= 0.0)
    assert trend[-1] == 1  # Strong uptrend should have positive DI > negative DI and ADX high


def test_chandelier_exit():
    data = generate_synthetic_ohlcv(50, trend=1.0)
    l_stop, s_stop, direction, atr = calculate_chandelier_exit(
        data["high"], data["low"], data["close"], length=22, mult=3.0
    )
    assert len(l_stop) == 50
    assert len(s_stop) == 50
    # Long stop should be below close in strong uptrend
    assert l_stop[-1] < data["close"][-1]
    assert direction[-1] == 1


def test_donchian_channels():
    data = generate_synthetic_ohlcv(40)
    upper, lower = calculate_donchian_channels(data["high"], data["low"], length=17)
    assert len(upper) == 40
    assert len(lower) == 40
    # For any t >= 17, upper must be >= lower
    for t in range(17, 40):
        assert upper[t] >= lower[t]


def test_consensus_score_pipeline():
    n = 60
    trends = {
        "m1": np.ones(n, dtype=int),
        "m2": np.ones(n, dtype=int),
        "m3": np.ones(n, dtype=int),
        "m4": np.full(n, -1, dtype=int),
    }
    raw_s, round_s, s_ema, s_slope, regimes, s_sma15 = calculate_consensus_score_pipeline(trends)
    # 3 out of 4 indicators are long -> raw score = (3/4)*10 = 7.5 -> rounded = 8
    assert np.isclose(raw_s[0], 7.5)
    assert round_s[0] == 8
    assert len(regimes) == n
    assert len(s_sma15) == n


def test_curated_ensemble_extractor_end_to_end():
    data = generate_synthetic_ohlcv(70, trend=0.8)
    extractor = CuratedEnsembleExtractor()
    states = extractor.compute_all(
        timestamps=data["timestamps"],
        opens=data["open"],
        highs=data["high"],
        lows=data["low"],
        closes=data["close"],
        volumes=data["volume"],
    )

    assert len(states) == 70
    last_state = states[-1]
    assert isinstance(last_state, EnsembleBarState)
    assert 0 <= last_state.rounded_score <= 10
    assert isinstance(last_state.regime, RegimeVelocity)
    assert isinstance(last_state.squeeze_color, SqueezeColor)
    assert last_state.long_stop > 0
    assert last_state.short_stop > 0
    assert last_state.trailing_score_sma15 > 0

"""Unit tests for Macro Liquidity and Warning Radar per Pine Script rev22 specification."""

from __future__ import annotations

import pytest

from quantdesk.data.macro_liquidity import (
    FedNetLiquidityClient,
    FedNetLiquiditySnapshot,
    MacroConvergenceRadar,
    MacroRegime,
    TetherDominanceClient,
    TetherDominanceSnapshot,
)


def test_fed_net_liquidity_calculation():
    """Verify Net Liquidity formula: WALCL - (TGA + RRP)."""
    # Suppose Fed Assets = $7,500B, TGA = $750B, RRP = $300B
    walcl = 7500.0
    tga = 750.0
    rrp = 300.0
    expected_net = walcl - (tga + rrp)  # 6450.0

    client = FedNetLiquidityClient()
    net = client.compute_net_liquidity(walcl, tga, rrp)
    assert net == pytest.approx(expected_net, rel=1e-5)


def test_fed_net_liquidity_series_and_sma():
    """Verify 20-period SMA smoothing and trend detection."""
    client = FedNetLiquidityClient()
    # Create an upward trend series of 25 observations
    series_walcl = [7000.0 + i * 20.0 for i in range(25)]
    series_tga = [500.0 for _ in range(25)]
    series_rrp = [300.0 for _ in range(25)]

    snapshot = client.calculate_snapshot(
        walcl_series=series_walcl,
        tga_series=series_tga,
        rrp_series=series_rrp,
        lookback=20,
    )
    assert snapshot.trend_direction == 1
    assert snapshot.pct_change > 0
    assert snapshot.is_bullish is True
    assert snapshot.net_liquidity_sma20 > 0


def test_tether_dominance_trend():
    """Verify USDT Dominance EMA smoothing and slope detection."""
    client = TetherDominanceClient()
    # Decreasing USDT dominance (capital deploying into crypto)
    usdt_values = [6.5 - i * 0.05 for i in range(20)]
    snapshot = client.calculate_snapshot(usdt_values, smooth_len=5)

    assert snapshot.trend_up is False
    assert snapshot.slope < 0
    assert snapshot.usdt_dominance_pct == pytest.approx(usdt_values[-1], rel=1e-5)


def test_macro_convergence_radar_bearish_warning():
    """USDT.D Rising AND Fed Liquidity Falling -> Bearish Warning."""
    radar = MacroConvergenceRadar()

    fed_snap = FedNetLiquiditySnapshot(
        timestamp_ns=1_000_000,
        walcl=7000.0,
        tga=800.0,
        rrp=500.0,
        net_liquidity=5700.0,
        net_liquidity_sma20=5800.0,
        trend_direction=-1,
        pct_change=-1.5,
        is_bullish=False,
        z_score=-1.8,
    )
    usdt_snap = TetherDominanceSnapshot(
        timestamp_ns=1_000_000,
        usdt_dominance_pct=6.8,
        ema5=6.6,
        z_score=1.5,
        slope=0.08,
        trend_up=True,
    )

    report = radar.evaluate(fed_snap, usdt_snap)
    assert report.regime == MacroRegime.BEARISH_WARNING
    assert report.warn_bearish is True
    assert report.warn_bullish is False
    assert report.warning_strength > 0
    assert report.warning_strength <= 100.0
    assert report.strength_level in ("MODERATE", "STRONG", "VERY_STRONG", "EXTREME")


def test_macro_convergence_radar_bullish_signal():
    """USDT.D Falling AND Fed Liquidity Rising -> Bullish Signal."""
    radar = MacroConvergenceRadar()

    fed_snap = FedNetLiquiditySnapshot(
        timestamp_ns=1_000_000,
        walcl=7500.0,
        tga=600.0,
        rrp=200.0,
        net_liquidity=6700.0,
        net_liquidity_sma20=6500.0,
        trend_direction=1,
        pct_change=2.0,
        is_bullish=True,
        z_score=1.9,
    )
    usdt_snap = TetherDominanceSnapshot(
        timestamp_ns=1_000_000,
        usdt_dominance_pct=4.8,
        ema5=5.0,
        z_score=-1.7,
        slope=-0.05,
        trend_up=False,
    )

    report = radar.evaluate(fed_snap, usdt_snap)
    assert report.regime == MacroRegime.BULLISH_SIGNAL
    assert report.warn_bullish is True
    assert report.warn_bearish is False
    assert report.warning_strength > 0


def test_macro_convergence_radar_neutral():
    """Both rising or both falling -> Neutral chop."""
    radar = MacroConvergenceRadar()

    fed_snap = FedNetLiquiditySnapshot(
        timestamp_ns=1_000_000,
        walcl=7000.0,
        tga=700.0,
        rrp=300.0,
        net_liquidity=6000.0,
        net_liquidity_sma20=6000.0,
        trend_direction=1,
        pct_change=0.01,
        is_bullish=True,
        z_score=0.1,
    )
    usdt_snap = TetherDominanceSnapshot(
        timestamp_ns=1_000_000,
        usdt_dominance_pct=5.5,
        ema5=5.5,
        z_score=0.05,
        slope=0.01,
        trend_up=True,
    )

    report = radar.evaluate(fed_snap, usdt_snap)
    assert report.regime == MacroRegime.NEUTRAL_CHOP
    assert report.warn_bearish is False
    assert report.warn_bullish is False


def test_autonomous_live_engine_macro_radar_integration():
    """Verify live runner initializes and returns macro radar telemetry."""
    from quantdesk.strategies.live_runner import AutonomousLiveEngine

    engine = AutonomousLiveEngine(symbols=("BTCUSDT", "ETHUSDT"))
    radar_data = engine.get_macro_radar()

    assert "macro_report" in radar_data
    assert radar_data["macro_report"] is not None
    assert "whale_positioning" in radar_data
    assert "BTCUSDT" in radar_data["whale_positioning"]
    assert "ETHUSDT" in radar_data["whale_positioning"]

    # Test dynamic refresh with updated macro variables
    refreshed = engine.refresh_macro_radar(walcl=7200.0, usdt_d=5.40)
    assert refreshed["macro_report"]["warning_strength"] >= 0


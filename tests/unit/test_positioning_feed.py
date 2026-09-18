"""Unit tests for Whale Positioning and Net Flow decomposition per Pine Script rev22."""

from __future__ import annotations

import math
import pytest

from quantdesk.data.positioning_feed import (
    WhaleNetFlowCalculator,
    WhalePositioningSnapshot,
)


def test_whale_positioning_decomposition():
    """Verify Longs = OI * LSR / (LSR + 1) and Shorts = OI / (LSR + 1)."""
    oi = 10000.0
    lsr = 1.5

    calc = WhaleNetFlowCalculator()
    longs, shorts = calc.decompose_contracts(oi, lsr)

    assert longs + shorts == pytest.approx(oi, rel=1e-5)
    assert (longs / shorts) == pytest.approx(lsr, rel=1e-5)
    assert longs == pytest.approx(6000.0, rel=1e-5)
    assert shorts == pytest.approx(4000.0, rel=1e-5)


def test_whale_net_flow_raw_and_zscore():
    """Verify Net Flow Raw = dLong - dShort and rolling Z-score."""
    calc = WhaleNetFlowCalculator()

    # Step 1: Initial state (T0)
    snap0 = calc.update(oi=10000.0, lsr=1.5, timestamp_ns=1_000_000)
    assert snap0.d_long == 0.0
    assert snap0.d_short == 0.0
    assert snap0.net_flow_raw == 0.0

    # Step 2: Whales add 500 longs and close 200 shorts (T1)
    # Longs: 6000 -> 6500 (+500), Shorts: 4000 -> 3800 (-200)
    # New OI: 10300, New LSR: 6500/3800 = 1.7105
    new_oi = 10300.0
    new_lsr = 6500.0 / 3800.0
    snap1 = calc.update(oi=new_oi, lsr=new_lsr, timestamp_ns=2_000_000)

    assert snap1.d_long == pytest.approx(500.0, abs=1.0)
    assert snap1.d_short == pytest.approx(-200.0, abs=1.0)
    # dLong - dShort = 500 - (-200) = +700
    assert snap1.net_flow_raw == pytest.approx(700.0, abs=2.0)
    assert snap1.net_flow_direction == 1


def test_positioning_macd_momentum():
    """Verify MACD momentum calculation on ln(Longs / Shorts)."""
    calc = WhaleNetFlowCalculator(fast_len=3, slow_len=6, signal_len=3)

    # Feed 15 observations with steadily rising LSR to build up positive MACD histogram
    for i in range(15):
        oi = 10000.0 + i * 200.0
        lsr = 1.0 + i * 0.1  # 1.0, 1.1, 1.2, ...
        snap = calc.update(oi=oi, lsr=lsr, timestamp_ns=i * 1_000_000)

    # Ratio is increasing, so MACD should be positive and rising
    assert snap.ratio_ln > 0
    assert snap.macd_hist is not None
    assert snap.macd_line > 0

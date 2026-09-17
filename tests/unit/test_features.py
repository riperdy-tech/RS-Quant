import math

import pytest

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
    SMA,
    VWAP,
    BollingerBands,
    Supertrend,
)


def test_sma_golden():
    """Independent hand-calculated golden test for SMA(3)."""
    sma = SMA(3)
    assert sma.update(10.0) is None
    assert sma.update(20.0) is None
    # (10 + 20 + 30) / 3 = 20.0
    assert sma.update(30.0) == pytest.approx(20.0)
    # (20 + 30 + 40) / 3 = 30.0
    assert sma.update(40.0) == pytest.approx(30.0)
    # (30 + 40 + 50) / 3 = 40.0
    assert sma.update(50.0) == pytest.approx(40.0)


def test_ema_golden():
    """Independent hand-calculated golden test for EMA(3).
    alpha = 2 / (3 + 1) = 0.5. Seed = SMA(3) = 20.0.
    Step 4: (40 - 20) * 0.5 + 20 = 30.0.
    Step 5: (20 - 30) * 0.5 + 30 = 25.0.
    """
    ema = EMA(3)
    assert ema.update(10.0) is None
    assert ema.update(20.0) is None
    assert ema.update(30.0) == pytest.approx(20.0)
    assert ema.update(40.0) == pytest.approx(30.0)
    assert ema.update(20.0) == pytest.approx(25.0)


def test_rsi_golden_independent():
    """Independent hand-calculated golden test for RSI(3) with Wilder smoothing.
    Period 3 -> alpha = 1/3.
    Closes:
      t0 = 100.0 (baseline)
      t1 = 106.0 -> gain 6, loss 0
      t2 = 103.0 -> gain 0, loss 3
      t3 = 109.0 -> gain 6, loss 0
      Initial SMA seed: avg_gain = (6+0+6)/3 = 4.0; avg_loss = (0+3+0)/3 = 1.0.
      RS = 4.0 / 1.0 = 4.0.
      RSI = 100 - (100 / (1 + 4)) = 80.0.
      t4 = 109.0 -> gain 0, loss 0
      Wilder update:
        avg_gain = (0 - 4)*(1/3) + 4 = 2.6666667
        avg_loss = (0 - 1)*(1/3) + 1 = 0.6666667
        RS = 2.6666667 / 0.6666667 = 4.0
        RSI = 80.0.
    """
    rsi = RSI(3)
    assert rsi.update(100.0) is None
    assert rsi.update(106.0) is None
    assert rsi.update(103.0) is None
    assert rsi.update(109.0) == pytest.approx(80.0)
    assert rsi.update(109.0) == pytest.approx(80.0)

    # Edge case: flat window -> 50.0
    rsi_flat = RSI(3)
    for _ in range(5):
        val = rsi_flat.update(100.0)
    assert val == pytest.approx(50.0)

    # Edge case: only gains -> 100.0
    rsi_up = RSI(3)
    rsi_up.update(100.0)
    rsi_up.update(101.0)
    rsi_up.update(102.0)
    val_up = rsi_up.update(103.0)
    assert val_up == pytest.approx(100.0)


def test_atr_golden_independent():
    """Independent hand-calculated golden test for ATR(3) with Wilder smoothing.
    Period 3 -> alpha = 1/3.
    Bars (high, low, close):
      Bar 0: (105, 95, 100) -> TR0 = 10.0
      Bar 1: (112, 98, 110) -> prev_close=100. TR1 = max(14, 12, 2) = 14.0
      Bar 2: (115, 105, 106) -> prev_close=110. TR2 = max(10, 5, 5) = 10.0
      Initial SMA seed for TR: (10 + 14 + 10) / 3 = 34 / 3 = 11.333333333333334
      Bar 3: (120, 100, 118) -> prev_close=106. TR3 = max(20, 14, 6) = 20.0
      Wilder update: (20 - 34/3)*(1/3) + 34/3 = 128 / 9 = 14.222222222222221
    """
    atr = ATR(3)
    assert atr.update(105.0, 95.0, 100.0) is None
    assert atr.update(112.0, 98.0, 110.0) is None
    val2 = atr.update(115.0, 105.0, 106.0)
    assert val2 == pytest.approx(34.0 / 3.0)
    val3 = atr.update(120.0, 100.0, 118.0)
    assert val3 == pytest.approx(128.0 / 9.0)


def test_bollinger_bands_golden():
    """Independent hand-calculated golden test for BollingerBands(3).
    Values: [10, 20, 30]
    Mean = 20.0
    Variance (population) = ((10-20)^2 + 0 + (30-20)^2) / 3 = 200 / 3 = 66.6666667
    std = sqrt(200/3) = 8.1649658
    Lower = 20 - 2 * std = 3.67006838
    Upper = 20 + 2 * std = 36.32993162
    """
    bb = BollingerBands(3)
    assert bb.update(10.0) == (None, None, None)
    assert bb.update(20.0) == (None, None, None)
    lower, mid, upper = bb.update(30.0)
    assert mid == pytest.approx(20.0)
    expected_std = math.sqrt(200.0 / 3.0)
    assert lower == pytest.approx(20.0 - 2.0 * expected_std)
    assert upper == pytest.approx(20.0 + 2.0 * expected_std)


def test_supertrend_golden():
    """Independent hand-calculated golden test for Supertrend(3, 2.0).
    Verifies initial bands, band carry, and trend flips.
    """
    st = Supertrend(period=3, multiplier=2.0)
    # Warmup with 3 bars (same values as ATR test)
    st.update(105.0, 95.0, 100.0)
    st.update(112.0, 98.0, 110.0)
    val, trend = st.update(115.0, 105.0, 106.0)
    # atr = 34/3; hl2 = 110.0; basic_upper = 110 + 2*atr; basic_lower = 110 - 2*atr
    assert trend == 1
    assert val == pytest.approx(87.33333333333333)

    # Severe dump: close = 70.0 (drops below lower band 87.33) -> trend flips to -1!
    val2, trend2 = st.update(90.0, 60.0, 70.0)
    assert trend2 == -1
    assert val2 is not None


def test_vwap_golden():
    vwap = VWAP()
    assert vwap.update(10.0, 100.0) == 10.0
    # (10*100 + 20*100) / 200 = 15.0
    assert vwap.update(20.0, 100.0) == 15.0
    # (3000 + 30*200) / 400 = 9000 / 400 = 22.5
    assert vwap.update(30.0, 200.0) == 22.5


def test_l1_imbalance_and_depth():
    assert l1_imbalance(100.0, 50.0) == pytest.approx(50.0 / 150.0)
    assert l1_imbalance(0.0, 0.0) is None

    bids = [(100.0 - i, 10.0) for i in range(10)]
    asks = [(101.0 + i, 5.0) for i in range(10)]
    # Depth 5: bids sum = 50, asks sum = 25 -> (50 - 25) / 75 = 1/3
    assert depth_imbalance(bids, asks, 5) == pytest.approx(25.0 / 75.0)
    # Depth 20 when only 10 levels exist -> None (INSUFFICIENT_DEPTH)
    assert depth_imbalance(bids, asks, 20) is None


def test_microprice_golden():
    expected = (12.0 * 100.0 + 10.0 * 50.0) / 150.0
    assert microprice(10.0, 12.0, 100.0, 50.0) == pytest.approx(expected)
    assert microprice(10.0, 12.0, 0.0, 0.0) is None


def test_cvd_golden():
    cvd = CVD()
    assert cvd.update(10.0, "BUY") == 10.0
    assert cvd.update(5.0, "SELL") == 5.0
    cvd.reset()
    assert cvd.update(20.0, "BUY") == 20.0


def test_l1_ofi_golden():
    ofi = L1OFI()
    assert ofi.update(10.0, 11.0, 100.0, 100.0) is None
    # Price unchanged, bid size drops by 10 -> ofi = -10
    assert ofi.update(10.0, 11.0, 90.0, 100.0) == -10.0
    # Bid price rises from 10 to 10.5 (bid_change = 50.0), ask size drops by 10 (ask_change = -10.0)
    # OFI = 50 - (-10) = 60
    assert ofi.update(10.5, 11.0, 50.0, 90.0) == 60.0


def test_ranked_mlofi_golden():
    mlofi = RankedMLOFI(ranks=3)
    bids1 = [(10.0, 100.0), (9.0, 50.0), (8.0, 20.0)]
    asks1 = [(11.0, 100.0), (12.0, 50.0), (13.0, 20.0)]
    assert mlofi.update(bids1, asks1, 1000) is None

    # Level changes: bid1 up to 10.5 (b_change=60), ask1 size drops from 100 to 90 (a_change=-10)
    bids2 = [(10.5, 60.0), (10.0, 100.0), (9.0, 50.0)]
    asks2 = [(11.0, 90.0), (12.0, 50.0), (13.0, 20.0)]
    val = mlofi.update(bids2, asks2, 1100)
    assert val is not None
    assert val > 0  # Net buying OFI


def test_trade_cluster_and_sweep():
    tc = TradeCluster(time_limit_ns=50_000_000)
    # Trade 1
    flushed, _c = tc.update(100.0, 10.0, "BUY", 1_000, trade_id="t1")
    assert not flushed

    # Trade 2 (within 50ms, same aggressor)
    flushed, _c = tc.update(100.5, 15.0, "BUY", 10_000_000, trade_id="t2")
    assert not flushed

    # Trade 3 (within 50ms, same aggressor, 3rd distinct price)
    flushed, _c = tc.update(101.0, 25.0, "BUY", 20_000_000, trade_id="t3")
    assert not flushed

    # Gap > 50ms triggers cluster flush
    flushed, cluster = tc.update(101.0, 5.0, "BUY", 80_000_000, trade_id="t4")
    assert flushed
    assert cluster is not None
    assert cluster["aggressor"] == "BUY"
    assert cluster["total_size"] == 50.0
    assert cluster["prices"] == 3
    assert cluster["span_ns"] == 19_999_000
    assert cluster["member_ids"] == ("t1", "t2", "t3")
    assert cluster["min_price"] == 100.0
    assert cluster["max_price"] == 101.0


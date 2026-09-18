"""Unit tests for ChandelierRiskSizer and Asymmetric TP/SL Engine."""

from decimal import Decimal
import pytest

from quantdesk.core.types import Side
from quantdesk.risk.chandelier_sizer import AsymmetricTPSL, ChandelierRiskSizer


def test_long_asymmetric_boundaries():
    sizer = ChandelierRiskSizer(
        risk_percentage=Decimal("0.02"),
        tp_perc=Decimal("0.03"),
        tp_atr_mult=Decimal("2.5"),
        sl_perc=Decimal("0.02"),
        sl_atr_mult=Decimal("2.0"),
    )

    entry = Decimal("100.00")
    atr = Decimal("2.00")
    # Chandelier line at 97.00 (below entry)
    ch_line = Decimal("97.00")

    # TP candidates: 100 * 1.03 = 103.00, 100 + 2.5 * 2.0 = 105.00 -> max is 105.00
    # SL candidates: 100 * 0.98 = 98.00, 100 - 2.0 * 2.0 = 96.00, ch_line = 97.00 -> max is 98.00
    tp, sl, dist = sizer.calculate_asymmetric_boundaries(
        side=Side.BUY,
        entry_price=entry,
        atr=atr,
        chandelier_line=ch_line,
    )

    assert tp == Decimal("105.00")
    assert sl == Decimal("98.00")
    assert dist == Decimal("2.00")


def test_short_asymmetric_boundaries():
    sizer = ChandelierRiskSizer(
        risk_percentage=Decimal("0.02"),
        tp_perc=Decimal("0.03"),
        tp_atr_mult=Decimal("2.5"),
        sl_perc=Decimal("0.02"),
        sl_atr_mult=Decimal("2.0"),
    )

    entry = Decimal("100.00")
    atr = Decimal("2.00")
    # Chandelier line at 103.00 (above entry)
    ch_line = Decimal("103.00")

    # TP candidates: 100 * 0.97 = 97.00, 100 - 2.5 * 2.0 = 95.00 -> min is 95.00
    # SL candidates: 100 * 1.02 = 102.00, 100 + 2.0 * 2.0 = 104.00, ch_line = 103.00 -> min is 102.00
    tp, sl, dist = sizer.calculate_asymmetric_boundaries(
        side=Side.SELL,
        entry_price=entry,
        atr=atr,
        chandelier_line=ch_line,
    )

    assert tp == Decimal("95.00")
    assert sl == Decimal("102.00")
    assert dist == Decimal("2.00")


def test_size_position_lot_stepping():
    sizer = ChandelierRiskSizer(
        risk_percentage=Decimal("0.02"),
        max_leverage=Decimal("5.0"),
        lot_step=Decimal("0.001"),
    )

    equity = Decimal("10000.00")  # $10,000 equity
    entry = Decimal("50000.00")   # $50,000 BTC
    atr = Decimal("500.00")
    ch_line = Decimal("49000.00")

    res = sizer.size_position(
        total_equity=equity,
        entry_price=entry,
        side=Side.BUY,
        atr=atr,
        chandelier_line=ch_line,
    )

    assert isinstance(res, AsymmetricTPSL)
    assert res.risk_dollars == Decimal("200.00")  # 2% of $10,000
    assert res.contracts > Decimal("0")
    # Must be exact multiple of lot step (0.001)
    remainder = res.contracts % Decimal("0.001")
    assert remainder == Decimal("0")


def test_max_leverage_capping():
    sizer = ChandelierRiskSizer(
        risk_percentage=Decimal("0.05"),
        max_leverage=Decimal("3.0"),  # max 3x leverage
        lot_step=Decimal("0.001"),
    )

    equity = Decimal("10000.00")
    entry = Decimal("100.00")
    # Artificially tiny stop distance of $0.10 would suggest 500 / 0.10 = 5000 contracts ($500,000 notional, 50x leverage)
    atr = Decimal("0.05")
    ch_line = Decimal("99.95")

    res = sizer.size_position(
        total_equity=equity,
        entry_price=entry,
        side=Side.BUY,
        atr=atr,
        chandelier_line=ch_line,
    )

    # Capped at 3x leverage: 3 * 10,000 / 100 = 300 contracts
    notional = res.contracts * entry
    assert notional <= equity * Decimal("3.0")
    assert res.contracts == Decimal("300.000")


def test_invalid_equity_error():
    sizer = ChandelierRiskSizer()
    with pytest.raises(ValueError, match="Total equity must be positive"):
        sizer.size_position(
            total_equity=Decimal("0"),
            entry_price=Decimal("100.0"),
            side=Side.BUY,
            atr=Decimal("2.0"),
            chandelier_line=Decimal("98.0"),
        )

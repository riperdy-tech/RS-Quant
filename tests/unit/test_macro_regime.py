import pytest
from quantdesk.features.macro_regime import EmpiricalMarketRegimeClassifier, MarketRegimeState


def test_empirical_market_regime_classifier():
    classifier = EmpiricalMarketRegimeClassifier(rolling_window=10)

    # Seed baseline volumes
    for v in [100.0, 105.0, 95.0, 102.0, 98.0, 100.0, 104.0, 96.0]:
        classifier.update_volume(v)
    for a in [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 102.0, 98.0, 101.0, 99.0]:
        classifier.update_atr(a)

    # Test High Volume Surge
    state = classifier.evaluate(
        now_ns=1000,
        latest_5m_volume=250.0,  # Big volume spike
        latest_atr=150.0,        # Big ATR spike
        bids=[["70000.0", "10.0"], ["69999.0", "15.0"]],
        asks=[["70001.0", "10.0"], ["70002.0", "15.0"]],
    )
    assert state.volume_zscore > 1.5
    assert state.volatility_ratio > 1.2
    assert state.is_high_volume_expansion is True
    assert state.regime_label == "HIGH_VOLUME_EXPANSION"

    # Test Liquidity Vacuum (Spread blowout)
    state_vacuum = classifier.evaluate(
        now_ns=2000,
        latest_5m_volume=100.0,
        latest_atr=100.0,
        bids=[["70000.0", "1.0"]],
        asks=[["70030.0", "1.0"]],  # 30 USDT spread (~4.2 bps)
    )
    assert state_vacuum.spread_bps > 3.0
    assert state_vacuum.is_liquidity_vacuum is True
    assert state_vacuum.regime_label == "LIQUIDITY_VACUUM_FREEZE"

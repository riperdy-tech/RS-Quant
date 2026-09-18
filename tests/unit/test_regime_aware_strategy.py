"""Unit tests for Regime-Aware and Whale-Positioning-Gated ImbalanceScalper."""

from __future__ import annotations

from decimal import Decimal
import pytest

from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.imbalance import ImbalanceScalper
from quantdesk.strategies.live_runner import make_live_envelope


def test_eth_long_vetoed_in_bearish_macro_regime():
    """Verify ETH long scalp is vetoed when macro regime is BEARISH_WARNING (USDT.D pumping)."""
    strat = ImbalanceScalper(instrument_id="ETHUSDT")

    # Positive micro imbalance (OBI=0.45, micro > mid, vol > 0), but macro is BEARISH_WARNING
    event = make_live_envelope(
        event_type="BookSnapshot",
        instrument_id="ETHUSDT",
        payload={},
        now_ns=1_000_000_000,
        engine_seq=1,
    )
    context = {
        "features": {
            "spread_bps": 2.0,
            "depth5_imbalance": 0.45,
            "microprice": 2701.0,
            "mid": 2700.0,
            "volume_1s_signed": 50.0,
            "atr14": 10.0,
            "macro_regime": "BEARISH_WARNING",
            "warn_bearish": True,
            "macro_usdt_d_slope": 0.08,
        }
    }

    intents = strat.on_event(event, context)
    # Long should be vetoed!
    assert len(intents) == 0


def test_eth_short_permitted_in_bearish_macro_regime():
    """Verify ETH short scalp is permitted and encouraged in BEARISH_WARNING regime."""
    strat = ImbalanceScalper(instrument_id="ETHUSDT")

    event = make_live_envelope(
        event_type="BookSnapshot",
        instrument_id="ETHUSDT",
        payload={},
        now_ns=1_000_000_000,
        engine_seq=1,
    )
    context = {
        "features": {
            "spread_bps": 2.0,
            "depth5_imbalance": -0.45,
            "microprice": 2699.0,
            "mid": 2700.0,
            "volume_1s_signed": -50.0,
            "atr14": 10.0,
            "macro_regime": "BEARISH_WARNING",
            "warn_bearish": True,
        }
    }

    intents = strat.on_event(event, context)
    assert len(intents) == 1
    assert intents[0].action == IntentAction.ENTER
    assert intents[0].side == Side.SELL


def test_whale_distribution_divergence_vetoes_long():
    """Verify long intent is rejected when retail micro OBI > 0 but Whale Net Flow Z-score < -1.5."""
    strat = ImbalanceScalper(instrument_id="BTCUSDT")

    event = make_live_envelope(
        event_type="BookSnapshot",
        instrument_id="BTCUSDT",
        payload={},
        now_ns=1_000_000_000,
        engine_seq=1,
    )
    context = {
        "features": {
            "spread_bps": 1.5,
            "depth5_imbalance": 0.50,
            "microprice": 60005.0,
            "mid": 60000.0,
            "volume_1s_signed": 20.0,
            "atr14": 50.0,
            "macro_regime": "NEUTRAL_CHOP",
            "whale_net_flow_zscore": -2.2,  # Heavy whale selling/distribution!
        }
    }

    intents = strat.on_event(event, context)
    assert len(intents) == 0  # Vetoed due to whale divergence


def test_chandelier_trailing_stop_does_not_prematurely_exit():
    """Verify Chandelier stop does NOT ratchet above entry price on immediate entry tick."""
    strat = ImbalanceScalper(instrument_id="BTCUSDT")

    # 1. Trigger BUY entry at mid = 60,000
    event1 = make_live_envelope(
        event_type="BookSnapshot",
        instrument_id="BTCUSDT",
        payload={},
        now_ns=1_000_000_000,
        engine_seq=1,
    )
    ctx1 = {
        "features": {
            "spread_bps": 1.0,
            "depth5_imbalance": 0.50,
            "microprice": 60005.0,
            "mid": 60000.0,
            "volume_1s_signed": 15.0,
            "atr14": 50.0,
            "chandelier_long_stop": 60020.0,  # Prior high Chandelier line is above entry!
        }
    }
    intents1 = strat.on_event(event1, ctx1)
    assert len(intents1) == 1
    assert intents1[0].action == IntentAction.ENTER

    # 2. Next tick 100ms later at mid = 60,002
    event2 = make_live_envelope(
        event_type="BookSnapshot",
        instrument_id="BTCUSDT",
        payload={},
        now_ns=1_100_000_000,
        engine_seq=2,
    )
    ctx2 = {
        "features": {
            "spread_bps": 1.0,
            "depth5_imbalance": 0.40,
            "microprice": 60004.0,
            "mid": 60002.0,
            "atr14": 50.0,
            "chandelier_long_stop": 60020.0,
        }
    }
    # It must NOT exit because Chandelier stop is above current price (not yet in profit)
    intents2 = strat.on_event(event2, ctx2)
    assert len(intents2) == 0
    assert strat.position_lots == Decimal("1")

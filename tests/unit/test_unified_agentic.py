from __future__ import annotations

from decimal import Decimal
import json
import pytest

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.live_runner import make_live_envelope
from quantdesk.strategies.unified_agentic import (
    UnifiedAgenticAlphaEngine,
    DirectionalBias,
    MarketRegimeType,
    AttributionTag,
)


def test_unified_engine_10_indicator_conviction():
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    features = {
        "mid": 65000.0,
        "atr14": 200.0,
        "rqk_value": 64800.0,         # +1.0 bullish deviation
        "mcginley_value": 64900.0,    # +0.5 bullish
        "squeeze_color": "BLUE",       # +1.0 expansion
        "cmf_value": 0.15,            # +1.0 positive flow
        "stc_value": 85.0,            # +0.875 oscillator high
        "qqe_line": 5.0,              # +1.0 positive
        "adx_value": 28.0,            # +0.93 strong trend
        "chandelier_dir": 1,          # +1.0 long stop active
        "volume_zscore": 1.5,         # +0.75
        "depth5_imbalance": 0.40,     # +0.20
        "donchian_high": 65200.0,
        "donchian_low": 64800.0,
    }

    conviction, signals = engine.compute_indicator_conviction(features, 65000.0)
    assert conviction > 0.40
    assert len(signals) == 10
    assert signals["squeeze"] == 1.0
    assert signals["chandelier"] == 1.0


def test_unified_engine_entry_and_sizing():
    engine = UnifiedAgenticAlphaEngine(
        instrument_id="BTCUSDT",
        target_notional=Decimal("15000.00"),
    )
    # Configure long bias
    features = {
        "mid": 65000.0,
        "atr14": 250.0,
        "consensus_score": 7.5,
        "volume_zscore": 1.2,
        "depth5_imbalance": 0.45,
        "microprice": 65001.0,
        "volume_1s_signed": 5.0,
        "rqk_value": 64800.0,
        "mcginley_value": 64900.0,
        "squeeze_color": "BLUE",
        "cmf_value": 0.15,
        "stc_value": 80.0,
        "qqe_line": 4.0,
        "adx_value": 26.0,
        "chandelier_dir": 1,
        "spread_bps": 0.12,
    }

    env = make_live_envelope("Tick", "BTCUSDT", features, now_ns=1_000_000_000, engine_seq=1)
    intents = engine.on_event(env, {"features": features})

    assert len(intents) == 1
    intent = intents[0]
    assert intent.action == IntentAction.ENTER
    assert intent.side == Side.BUY
    assert intent.desired_quantity > Decimal("0")
    assert engine.position_side == Side.BUY
    assert engine.entry_price == Decimal("65000.00")
    # Stop loss should be capped tightly at 1.2x ATR = 300 USDT (well under 2000 USDT)
    stop_dist = engine.entry_price - engine.stop_price
    assert stop_dist <= Decimal("350.00")
    # TP1 price set
    assert engine.tp1_price > engine.entry_price


def test_unified_engine_alpha_half_life_scratch():
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    features = {
        "mid": 65000.0,
        "atr14": 250.0,
        "consensus_score": 7.5,
        "volume_zscore": 1.2,
        "depth5_imbalance": 0.45,
        "microprice": 65001.0,
        "volume_1s_signed": 5.0,
        "rqk_value": 64800.0,
        "mcginley_value": 64900.0,
        "squeeze_color": "BLUE",
    }
    # Enter trade at t=0
    env_enter = make_live_envelope("Tick", "BTCUSDT", features, now_ns=1_000_000_000, engine_seq=100)
    intents = engine.on_event(env_enter, {"features": features})
    assert len(intents) == 1

    # Fast forward 200s (over 180s alpha half life), price is flat (65001), squeeze turns RED
    features_stalled = dict(features)
    features_stalled["mid"] = 65001.0
    features_stalled["squeeze_color"] = "RED"
    features_stalled["depth5_imbalance"] = -0.25

    env_tick = make_live_envelope(
        "Tick", "BTCUSDT", features_stalled, now_ns=env_enter.available_ns + 200_000_000_000, engine_seq=200
    )

    exit_intents = engine.on_event(env_tick, {"features": features_stalled})
    assert len(exit_intents) == 1
    assert exit_intents[0].action == IntentAction.EXIT
    assert exit_intents[0].reason == "alpha_half_life_scratch"
    assert engine.position_lots == Decimal("0")


def test_unified_engine_multi_speed_learning_adaptation():
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")

    # Record 5 simulated episodes with correlated returns to test Information Coefficient updating
    now_ns = 1_000_000_000
    for i in range(1, 7):
        engine.entry_feature_snapshot = {
            "rqk": 0.5 * (1 if i % 2 == 1 else -0.5),
            "mcginley": 0.4 * (1 if i % 2 == 1 else -0.4),
            "squeeze": 1.0 if i % 2 == 1 else -1.0,
            "cmf": 0.2 if i % 2 == 1 else -0.2,
            "stc": 0.8 if i % 2 == 1 else -0.8,
            "qqe": 1.0 if i % 2 == 1 else -1.0,
            "adx": 0.5,
            "chandelier": 1.0 if i % 2 == 1 else -1.0,
            "volume_delta": 0.6 if i % 2 == 1 else -0.6,
            "donchian": 0.5 if i % 2 == 1 else -0.5,
        }
        engine.last_meta_features = [0.01, 1.0, 0.1, 1.0, 0.1, 0.7, 0.05, 0.3, 0.02, 1.0, 0.3, 0.1]
        pnl = Decimal("25.00") if i % 2 == 1 else Decimal("-10.00")
        engine.record_trade_exit(
            entry_price=Decimal("65000.00"),
            exit_price=Decimal("65025.00") if pnl > 0 else Decimal("64990.00"),
            side="BUY",
            qty_units=Decimal("0.2"),
            hold_time_s=120,
            gross_pnl=pnl + Decimal("2.00"),
            fee=Decimal("2.00"),
            net_pnl=pnl,
            now_ns=now_ns + i * 1_000_000_000,
        )

    # Autoregressive weights should have adapted!
    assert engine.params.indicator_weights["squeeze"] != 1.0 or engine.params.indicator_weights["rqk"] != 1.0
    status = engine.get_agentic_status()
    assert status["meta_learner"]["replay_buffer_len"] == 6
    assert status["memory_summary"]["total_episodes"] == 6

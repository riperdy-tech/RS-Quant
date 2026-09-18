"""Unit tests for CuratedEnsembleStrategy with 2h resampling and 4-tier filtration pyramid."""

from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from quantdesk.core.events import Envelope
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.curated_ensemble import CuratedEnsembleStrategy
from quantdesk.strategies.live_runner import make_live_envelope


def create_envelope(seq: int, available_ns: int) -> Envelope:
    return make_live_envelope(
        event_type="BarClosed",
        instrument_id="BTCUSDT",
        payload={},
        now_ns=available_ns,
        engine_seq=seq,
    )


def test_strategy_warmup():
    strategy = CuratedEnsembleStrategy(macro_timeframe_seconds=7200)
    # Feed 10 bars (less than 25 required)
    for i in range(10):
        t_ns = 1700000000_000_000_000 + i * 7200_000_000_000
        env = create_envelope(i, t_ns)
        ctx: dict[str, Any] = {
            "features": {"close": 100.0, "open": 100.0, "high": 101.0, "low": 99.0, "volume": 100.0}
        }
        intents = strategy.on_event(env, ctx)
        assert len(intents) == 0


def test_long_entry_and_sizing():
    strategy = CuratedEnsembleStrategy(macro_timeframe_seconds=7200)

    # Feed 25 bars of consolidation, followed by an accelerating breakout surge
    intents = ()
    base_price = 100.0
    for i in range(40):
        t_ns = 1700000000_000_000_000 + i * 7200_000_000_000
        env = create_envelope(i, t_ns)
        if i >= 25:
            base_price += 2.0 * (i - 24)  # Accelerating breakout
        else:
            base_price += 0.1  # Tight consolidation
        ctx: dict[str, Any] = {
            "features": {
                "close": base_price,
                "open": base_price - 0.5,
                "high": base_price + 1.0,
                "low": base_price - 1.0,
                "volume": 500.0,
            },
            "equity": 10000.0,
        }
        res = strategy.on_event(env, ctx)
        if res:
            intents = res

    # An ENTER intent must have fired on the breakout
    assert len(intents) > 0
    enter_intent = intents[0]
    assert enter_intent.action == IntentAction.ENTER
    assert enter_intent.side == Side.BUY
    assert enter_intent.desired_quantity > Decimal("0")
    assert enter_intent.risk_budget is not None
    assert enter_intent.risk_budget > Decimal("0")


def test_squeeze_filter_veto():
    strategy = CuratedEnsembleStrategy(macro_timeframe_seconds=7200)

    # Mock extractor output to simulate an uptrend breakout where squeeze is RED (vetoed)
    for i in range(30):
        t_ns = 1700000000_000_000_000 + i * 7200_000_000_000
        env = create_envelope(i, t_ns)
        ctx: dict[str, Any] = {
            "features": {"close": 100.0 + i, "open": 99.0 + i, "high": 101.0 + i, "low": 98.0 + i, "volume": 100.0},
            "equity": 10000.0,
        }
        strategy.on_event(env, ctx)

    # Force squeeze_long_ok to False on the latest state
    if strategy.latest_bar_state:
        strategy.latest_bar_state.squeeze_long_ok = False

    t_ns = 1700000000_000_000_000 + 31 * 7200_000_000_000
    env = create_envelope(31, t_ns)
    ctx = {
        "features": {"close": 200.0, "open": 190.0, "high": 205.0, "low": 185.0, "volume": 100.0},
        "equity": 10000.0,
    }
    intents = strategy.on_event(env, ctx)
    # Squeeze veto must block trade entry
    assert len(intents) == 0


def test_score_sma_knife_catch_veto():
    strategy = CuratedEnsembleStrategy(macro_timeframe_seconds=7200)

    for i in range(30):
        t_ns = 1700000000_000_000_000 + i * 7200_000_000_000
        env = create_envelope(i, t_ns)
        ctx: dict[str, Any] = {
            "features": {"close": 100.0, "open": 100.0, "high": 101.0, "low": 99.0, "volume": 100.0},
            "equity": 10000.0,
        }
        strategy.on_event(env, ctx)

    # If trailing score SMA15 is 3.5 (deep bear market, <= 4.2)
    if strategy.latest_bar_state:
        strategy.latest_bar_state.trailing_score_sma15 = 3.5
        strategy.latest_bar_state.score_gate_long_ok = False

    t_ns = 1700000000_000_000_000 + 31 * 7200_000_000_000
    env = create_envelope(31, t_ns)
    ctx = {
        "features": {"close": 200.0, "open": 190.0, "high": 205.0, "low": 185.0, "volume": 100.0},
        "equity": 10000.0,
    }
    intents = strategy.on_event(env, ctx)
    # Knife-catch veto must block trade entry
    assert len(intents) == 0


def test_position_exit_on_stop():
    strategy = CuratedEnsembleStrategy(macro_timeframe_seconds=7200)
    strategy.position_lots = Decimal("1.5")
    strategy.position_side = Side.BUY
    strategy.entry_price = Decimal("100.0")
    strategy.stop_price = Decimal("95.0")
    strategy.target_price = Decimal("110.0")

    t_ns = 1700000000_000_000_000
    env = create_envelope(1, t_ns)
    # Price falls through stop price to 94.0
    ctx: dict[str, Any] = {
        "features": {"close": 94.0, "open": 96.0, "high": 97.0, "low": 93.0, "volume": 100.0}
    }
    intents = strategy.on_event(env, ctx)
    assert len(intents) == 1
    exit_intent = intents[0]
    assert exit_intent.action == IntentAction.EXIT
    assert exit_intent.side == Side.SELL
    assert exit_intent.desired_quantity == Decimal("1.5")
    assert exit_intent.reason == "stop_loss_hit"
    assert strategy.position_lots == Decimal("0")

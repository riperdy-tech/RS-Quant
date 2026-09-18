"""Unit tests for the Dynamic AI-Driven Exit & Profit-Realization Engine.

Verifies:
1. Realistic 1.2x/1.5x ATR target and stop calibration.
2. Dynamic Breakeven Ratchet (+25 bps triggers fee-free stop lock).
3. Vector 1: Volume & Order Flow Climax (L2 ask wall / CMF distribution).
4. Vector 2: Momentum Squeeze Deceleration (exhaustion histogram rollover).
5. Vector 4: Strict Alpha Horizon Time-Decay (20-minute dead chop scratch rule).
6. Max Holding Period Timeout (60-minute hard cap).
"""

from decimal import Decimal
import time

from quantdesk.core.events import Envelope
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.unified_agentic import UnifiedAgenticAlphaEngine


def _make_mock_envelope(now_ns: int) -> Envelope:
    return Envelope(
        event_type="Depth",
        schema_version=1,
        run_id="run-test",
        account_id="paper-demo",
        venue="bitget",
        environment="DEMO",
        instrument_id="BTCUSDT",
        source_channel="market",
        connection_epoch="epoch-1",
        source_message_id=None,
        source_sequence=None,
        exchange_event_ns=now_ns,
        exchange_transaction_ns=None,
        receive_wall_ns=now_ns,
        receive_monotonic_ns=now_ns,
        available_ns=now_ns,
        causation_id=None,
        correlation_id="corr-test",
        raw_ref=None,
        producer_version="v1",
        payload=b"{}",
        event_id=f"evt-{now_ns}",
        engine_seq=1,
    )


def test_realistic_atr_target_and_stop_calibration():
    """Verifies that target and stop are set to realistic 1.2x / 1.5x ATR horizons."""
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    now_ns = 1_000_000_000_000
    env = _make_mock_envelope(now_ns)

    features = {
        "mid": 75000.0,
        "microprice": 75000.0,
        "atr14": 300.0,  # 300 USDT ATR on 5m candle (~40 bps)
        "consensus_score": 7.5,
        "macro_regime": "STRONG_BULL",
        "squeeze_color": "BLUE",
        "mcginley_trend": 1,
        "cmf_trend": 1,
        "rqk_trend": 1,
        "depth5_imbalance": 0.45,
    }

    intents = engine.on_event(env, {"features": features})
    assert len(intents) == 1
    intent = intents[0]
    assert intent.action == IntentAction.ENTER
    assert intent.side == Side.BUY

    # Stop Loss: max(1.2 * 300, 75000 * 0.0035 = 262.5) = 360 USDT (74,640)
    assert engine.stop_price == Decimal("74640.00")
    # Take Profit: max(1.5 * 300, 75000 * 0.0045 = 337.5) = 450 USDT (75,450)
    assert engine.target_price == Decimal("75450.00")

    # Target distance is 450 USDT (60 bps), completely achievable in normal volatility
    target_dist = engine.target_price - engine.entry_price
    assert target_dist == Decimal("450.00")


def test_dynamic_breakeven_ratchet():
    """Verifies that reaching +25 bps profit ratchets stop to entry + 8 bps (guaranteeing fee-free win)."""
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    now_ns = 1_000_000_000_000
    env = _make_mock_envelope(now_ns)

    # Manually establish position at 75,000
    engine.position_lots = Decimal("0.1000")
    engine.position_side = Side.BUY
    engine.entry_price = Decimal("75000.00")
    engine.stop_price = Decimal("74640.00")
    engine.target_price = Decimal("75450.00")
    engine.entry_time_ns = now_ns
    engine.breakeven_active = False

    # Price advances +30 bps to 75,225
    mid_profit = 75225.0
    features = {
        "mid": mid_profit,
        "squeeze_color": "BLUE",
        "depth5_imbalance": 0.10,
    }

    intents = engine.on_event(env, {"features": features})
    assert len(intents) == 0  # Still holding runner
    assert engine.breakeven_active is True
    # Stop price must be ratcheted to entry * 1.0008 = 75060.00
    expected_be_stop = Decimal("75000.00") * Decimal("1.0008")
    assert engine.stop_price == expected_be_stop

    # Price pulls back and hits breakeven stop
    mid_pullback = 75050.0
    features_pullback = {
        "mid": mid_pullback,
        "squeeze_color": "BLUE",
        "depth5_imbalance": 0.0,
    }
    exit_intents = engine.on_event(env, {"features": features_pullback})
    assert len(exit_intents) == 1
    assert exit_intents[0].action == IntentAction.EXIT
    assert exit_intents[0].reason == "stop_loss_hit"
    assert engine.position_lots == Decimal("0")


def test_volume_climax_orderbook_wall_exit():
    """Verifies Vector 1: Exiting immediately into an ask wall when in profit."""
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    now_ns = 1_000_000_000_000
    env = _make_mock_envelope(now_ns)

    engine.position_lots = Decimal("0.1000")
    engine.position_side = Side.BUY
    engine.entry_price = Decimal("75000.00")
    engine.stop_price = Decimal("74640.00")
    engine.target_price = Decimal("75450.00")
    engine.entry_time_ns = now_ns

    # Price is up +15 bps (75,112.5), but order book displays massive ask wall (depth5_imbalance = -0.55)
    features = {
        "mid": 75112.5,
        "squeeze_color": "BLUE",
        "depth5_imbalance": -0.55,
    }

    intents = engine.on_event(env, {"features": features})
    assert len(intents) == 1
    assert intents[0].action == IntentAction.EXIT
    assert intents[0].reason == "orderbook_ask_wall_climax"
    assert engine.position_lots == Decimal("0")


def test_momentum_squeeze_deceleration_exit():
    """Verifies Vector 2: Exiting when Squeeze Momentum histogram rolls over to exhaustion."""
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    now_ns = 1_000_000_000_000
    env = _make_mock_envelope(now_ns)

    engine.position_lots = Decimal("0.1000")
    engine.position_side = Side.BUY
    engine.entry_price = Decimal("75000.00")
    engine.stop_price = Decimal("74640.00")
    engine.target_price = Decimal("75450.00")
    engine.entry_time_ns = now_ns

    # Price is up +20 bps (75,150), and Squeeze prints GREEN (contracting momentum exhaustion)
    features = {
        "mid": 75150.0,
        "squeeze_color": "GREEN",  # Bullish exhaustion
        "depth5_imbalance": 0.10,
    }

    intents = engine.on_event(env, {"features": features})
    assert len(intents) == 1
    assert intents[0].action == IntentAction.EXIT
    assert intents[0].reason == "momentum_squeeze_deceleration"
    assert engine.position_lots == Decimal("0")


def test_alpha_horizon_dead_chop_scratch():
    """Verifies Vector 4: Unconditionally scratching dead-chop positions held > 20 minutes."""
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    entry_ns = 1_000_000_000_000
    # 25 minutes later (1500 seconds)
    now_ns = entry_ns + (1500 * 1_000_000_000)
    env = _make_mock_envelope(now_ns)

    engine.position_lots = Decimal("0.1000")
    engine.position_side = Side.BUY
    engine.entry_price = Decimal("75000.00")
    engine.stop_price = Decimal("74640.00")
    engine.target_price = Decimal("75450.00")
    engine.entry_time_ns = entry_ns

    # Price is hovering in chop (+2 bps, 75,015.0), macro score is still bullish (8.0)
    features = {
        "mid": 75015.0,
        "consensus_score": 8.0,
        "macro_regime": "STRONG_BULL",
        "squeeze_color": "BLUE",
        "depth5_imbalance": 0.05,
    }

    intents = engine.on_event(env, {"features": features})
    assert len(intents) == 1
    assert intents[0].action == IntentAction.EXIT
    # Must scratch unconditionally without being blocked by bullish macro
    assert intents[0].reason == "alpha_half_life_dead_chop_scratch"
    assert engine.position_lots == Decimal("0")


def test_max_holding_period_timeout():
    """Verifies that no position can ever remain held longer than 60 minutes."""
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
    entry_ns = 1_000_000_000_000
    # 65 minutes later (3900 seconds)
    now_ns = entry_ns + (3900 * 1_000_000_000)
    env = _make_mock_envelope(now_ns)

    engine.position_lots = Decimal("0.1000")
    engine.position_side = Side.BUY
    engine.entry_price = Decimal("75000.00")
    engine.stop_price = Decimal("74640.00")
    engine.target_price = Decimal("75450.00")
    engine.entry_time_ns = entry_ns

    # Price down -18 bps
    features = {
        "mid": 74865.0,
        "consensus_score": 7.0,
        "macro_regime": "STRONG_BULL",
        "squeeze_color": "BLUE",
        "depth5_imbalance": 0.0,
    }

    intents = engine.on_event(env, {"features": features})
    assert len(intents) == 1
    assert intents[0].action == IntentAction.EXIT
    assert intents[0].reason == "max_holding_period_timeout"
    assert engine.position_lots == Decimal("0")

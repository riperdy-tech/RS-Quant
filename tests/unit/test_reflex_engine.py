"""Unit tests for Event-Driven AI Reflex Engine (§15.2)."""

from decimal import Decimal
import time

from quantdesk.strategies.live_runner import autonomous_live_engine
from quantdesk.api.routes.trading import get_reflex_status, toggle_reflex_tuner, trigger_reflex_audit


def test_reflex_status_initial():
    status = autonomous_live_engine.get_reflex_status()
    assert "auto_tuner_enabled" in status
    assert "current_atr_multiplier" in status
    assert "entry_cooldown_s" in status
    assert "depth5_threshold" in status
    assert "recent_events" in status
    assert len(status["recent_events"]) >= 1


def test_reflex_toggle():
    # Toggle off
    updated = autonomous_live_engine.toggle_reflex_tuner(False)
    assert updated["auto_tuner_enabled"] is False
    assert updated["recent_events"][0]["action"] == "TUNER_PAUSED"

    # Toggle back on
    updated = autonomous_live_engine.toggle_reflex_tuner(True)
    assert updated["auto_tuner_enabled"] is True
    assert updated["recent_events"][0]["action"] == "TUNER_ENGAGED"


def test_reflex_fee_friction_adaptation():
    # Reset baseline first
    autonomous_live_engine.manual_trigger_reflex("RESET_BASELINE")
    initial_atr = autonomous_live_engine.atr_target_multiplier

    now_ns = time.time_ns()
    # Trigger post-trade reflex where gross alpha was positive but net loss occurred due to fee
    autonomous_live_engine._trigger_post_trade_reflex(
        symbol="BTCUSDT",
        net_trade_pnl=Decimal("-2.50"),
        gross_pnl=Decimal("1.50"),
        fee=Decimal("4.00"),
        hold_time_s=120,
        now_ns=now_ns,
    )

    status = autonomous_live_engine.get_reflex_status()
    assert status["current_atr_multiplier"] > initial_atr
    assert status["maker_only_mode"] is True
    assert status["recent_events"][0]["type"] == "ADAPTIVE_FRICTION_WIDEN"


def test_reflex_volatility_chop_guard():
    autonomous_live_engine.manual_trigger_reflex("RESET_BASELINE")
    initial_cooldown = autonomous_live_engine.entry_cooldown_s
    initial_threshold = autonomous_live_engine.depth5_imbalance_threshold

    now_ns = time.time_ns()
    # Rapid stop out (< 45s hold time) with negative net PnL
    autonomous_live_engine._trigger_post_trade_reflex(
        symbol="ETHUSDT",
        net_trade_pnl=Decimal("-15.00"),
        gross_pnl=Decimal("-15.00"),
        fee=Decimal("0.00"),
        hold_time_s=10,
        now_ns=now_ns,
    )

    status = autonomous_live_engine.get_reflex_status()
    assert status["entry_cooldown_s"] > initial_cooldown
    assert status["depth5_threshold"] > initial_threshold
    assert status["recent_events"][0]["type"] == "VOLATILITY_CHOP_GUARD"


def test_reflex_profitable_trade_confirmation():
    now_ns = time.time_ns()
    autonomous_live_engine._trigger_post_trade_reflex(
        symbol="BTCUSDT",
        net_trade_pnl=Decimal("45.00"),
        gross_pnl=Decimal("45.00"),
        fee=Decimal("0.00"),
        hold_time_s=180,
        now_ns=now_ns,
    )

    status = autonomous_live_engine.get_reflex_status()
    assert status["recent_events"][0]["type"] == "PROFIT_CONFIRMATION"


def test_reflex_manual_baseline_reset():
    status = autonomous_live_engine.manual_trigger_reflex("RESET_BASELINE")
    assert status["maker_only_mode"] is True
    assert status["entry_cooldown_s"] == 60
    assert status["depth5_threshold"] == 0.35
    assert status["current_atr_multiplier"] == 3.5
    assert status["recent_events"][0]["action"] == "BASELINE_CALIBRATED"


def test_reflex_api_routes():
    from quantdesk.api.auth import Session, UserRole

    dummy_session = Session(session_id="s1", username="operator", role=UserRole.OPERATOR, csrf_token="tok1")

    # Route GET /trading/reflex-status
    status = get_reflex_status(session=dummy_session)
    assert status["auto_tuner_enabled"] is True

    # Route POST /trading/reflex-toggle
    toggled = toggle_reflex_tuner(enabled=False, session=dummy_session)
    assert toggled["auto_tuner_enabled"] is False

    toggle_back = toggle_reflex_tuner(enabled=True, session=dummy_session)
    assert toggle_back["auto_tuner_enabled"] is True

    # Route POST /trading/reflex-trigger
    audit_res = trigger_reflex_audit(action_type="MICRO_AUDIT", session=dummy_session)
    assert audit_res["recent_events"][0]["type"] == "INSTANT_MICRO_AUDIT"

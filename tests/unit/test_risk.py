"""Comprehensive tests for §11 risk limits, sizing, breakers, and emergency recovery."""
from dataclasses import replace
from decimal import Decimal

from quantdesk.config.schema import RiskConfig
from quantdesk.core.events import StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.risk.emergency import Emergency
from quantdesk.risk.limits import RiskLimits
from quantdesk.risk.manager import Risk, RiskContext
from quantdesk.risk.sizer import RiskSizer
from quantdesk.venues.instruments import InstrumentSpec


def fixture_spec(
    min_qty: str = "0.001",
    max_qty: str = "100",
    step: str = "0.001",
    min_notional: str = "5",
    tick: str = "0.1",
) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id="bitget:linear:BTC:USDT:USDT:BTCUSDT",
        valid_from_ns=0,
        known_from_ns=0,
        revision_hash="abc",
        tick_size=Decimal(tick),
        quantity_step=Decimal(step),
        contract_multiplier=Decimal("1"),
        base_unit="BTC",
        quote_unit="USDT",
        settlement_unit="USDT",
        min_quantity=Decimal(min_qty),
        max_quantity=Decimal(max_qty),
        min_notional=Decimal(min_notional),
        trading_status="TRADING",
        min_leverage=Decimal("1"),
        max_leverage=Decimal("125"),
        supported_margin_modes=("isolated",),
        supported_position_modes=("one_way",),
        funding_schedule=(),
        venue_extensions=(),
    )


# ── Daily loss & Drawdown tests ─────────────────────────────────────


def test_daily_loss_limit():
    config = RiskConfig(daily_loss_fraction=Decimal("0.05"))
    limits = RiskLimits(config)

    # 5% of 10000 = 500 max loss
    baseline = Decimal("10000")
    net_flow = Decimal("0")

    # Loss is 400 (4%)
    ok, reason = limits.check_daily_loss(baseline, Decimal("9600"), net_flow)
    assert ok
    assert reason == "OK"

    # Loss is 600 (6%)
    ok, reason = limits.check_daily_loss(baseline, Decimal("9400"), net_flow)
    assert not ok
    assert reason == "DAILY_LOSS_LIMIT_EXCEEDED"

    # Flow adjustment: deposited 2000, baseline is 10000.
    # Total expected = 12000.
    # If equity is 11400, loss is 600. 600/10000 = 6% > 5%
    ok, reason = limits.check_daily_loss(baseline, Decimal("11400"), Decimal("2000"))
    assert not ok
    assert reason == "DAILY_LOSS_LIMIT_EXCEEDED"

    # Zero or negative baseline blocks entries per §11.1
    ok, reason = limits.check_daily_loss(Decimal("0"), Decimal("1000"), Decimal("0"))
    assert not ok
    assert reason == "BASELINE_EQUITY_ZERO_OR_NEGATIVE"


def test_drawdown_limit():
    config = RiskConfig(peak_drawdown_fraction=Decimal("0.10"))
    limits = RiskLimits(config)

    peak = Decimal("10000")

    # 9% drawdown
    ok, reason = limits.check_drawdown(peak, Decimal("9100"))
    assert ok

    # 11% drawdown
    ok, reason = limits.check_drawdown(peak, Decimal("8900"))
    assert not ok
    assert reason == "PEAK_DRAWDOWN_EXCEEDED"


# ── Stale data & Clock offset tests ─────────────────────────────────


def test_stale_data_checks():
    config = RiskConfig(
        market_data_max_age_ms=500,
        mark_max_age_ms=2000,
        private_heartbeat_max_age_ms=1000,
        max_ingress_age_ms=250,
        max_clock_offset_ms=250,
    )
    limits = RiskLimits(config)

    event_ns = 2_000_000_000

    # OK
    ok, reason = limits.check_stale_data(
        event_ns,
        market_data_ns=event_ns - 100_000_000,  # 100ms
        mark_ns=event_ns - 500_000_000,
        heartbeat_ns=event_ns - 800_000_000,
        ingress_ns=event_ns - 50_000_000,
        clock_offset_ms=10,
    )
    assert ok
    assert reason == "OK"

    # Stale market data (> 500ms)
    ok, reason = limits.check_stale_data(
        event_ns,
        market_data_ns=event_ns - 600_000_000,
        mark_ns=None,
        heartbeat_ns=None,
        ingress_ns=None,
        clock_offset_ms=10,
    )
    assert not ok
    assert reason == "STALE_MARKET_DATA"

    # Stale mark price (> 2000ms)
    ok, reason = limits.check_stale_data(
        event_ns,
        market_data_ns=None,
        mark_ns=event_ns - 2_500_000_000,
        heartbeat_ns=None,
        ingress_ns=None,
        clock_offset_ms=10,
    )
    assert not ok
    assert reason == "STALE_MARK_PRICE"

    # Stale heartbeat (> 1000ms)
    ok, reason = limits.check_stale_data(
        event_ns,
        market_data_ns=None,
        mark_ns=None,
        heartbeat_ns=event_ns - 1_200_000_000,
        ingress_ns=None,
        clock_offset_ms=10,
    )
    assert not ok
    assert reason == "STALE_PRIVATE_HEARTBEAT"

    # Stale ingress (> 250ms)
    ok, reason = limits.check_stale_data(
        event_ns,
        market_data_ns=None,
        mark_ns=None,
        heartbeat_ns=None,
        ingress_ns=event_ns - 300_000_000,
        clock_offset_ms=10,
    )
    assert not ok
    assert reason == "STALE_INGRESS"

    # High clock offset (> 250ms)
    ok, reason = limits.check_stale_data(
        event_ns,
        market_data_ns=None,
        mark_ns=None,
        heartbeat_ns=None,
        ingress_ns=None,
        clock_offset_ms=300,
    )
    assert not ok
    assert reason == "CLOCK_OFFSET_TOO_HIGH"


# ── Sizer tests ─────────────────────────────────────────────────────


def test_sizer_basic_and_caps():
    config = RiskConfig(max_order_visible_depth_fraction=Decimal("0.01"))
    sizer = RiskSizer(config)
    spec = fixture_spec()

    # 100 USDT risk cash, 50 USDT stop distance, 1 USDT cost -> risk_cost = 51
    # 100 / 51 = 1.96078... base units
    # lots = 1.96078 / 0.001 = 1960 lots
    lots, reason = sizer.calculate_size(
        intent_desired_qty=None,
        risk_cash=Decimal("100"),
        stop_distance_per_base_unit=Decimal("50"),
        estimated_roundtrip_cost_per_base_unit=Decimal("1"),
        current_price=Decimal("50000"),
        spec=spec,
    )
    assert reason == "OK"
    assert lots == 1960

    # Cap by intent desired qty
    lots, reason = sizer.calculate_size(
        intent_desired_qty=Decimal("0.5"),
        risk_cash=Decimal("100"),
        stop_distance_per_base_unit=Decimal("50"),
        estimated_roundtrip_cost_per_base_unit=Decimal("1"),
        current_price=Decimal("50000"),
        spec=spec,
    )
    assert reason == "OK"
    assert lots == 500  # 0.5 / 0.001

    # Cap by depth participation: 1% of 10,000 lots = 100 lots
    lots, reason = sizer.calculate_size(
        intent_desired_qty=None,
        risk_cash=Decimal("100"),
        stop_distance_per_base_unit=Decimal("50"),
        estimated_roundtrip_cost_per_base_unit=Decimal("1"),
        current_price=Decimal("50000"),
        spec=spec,
        visible_depth_lots=10_000,
    )
    assert reason == "OK"
    assert lots == 100


def test_tiny_caps_below_venue_minimum():
    config = RiskConfig()
    sizer = RiskSizer(config)
    spec = fixture_spec(min_qty="0.01", min_notional="10")

    # Risk budget allows only 0.001 base units, but min_quantity is 0.01
    lots, reason = sizer.calculate_size(
        intent_desired_qty=None,
        risk_cash=Decimal("0.05"),
        stop_distance_per_base_unit=Decimal("50"),
        estimated_roundtrip_cost_per_base_unit=Decimal("0"),
        current_price=Decimal("50000"),
        spec=spec,
    )
    assert reason == "BELOW_MINIMUM_AFTER_RISK_CAP"
    assert lots == 0


# ── Emergency kill, soft breakers, pause/resume ─────────────────────


def test_emergency_kill_and_reset():
    emergency = Emergency()

    kill_latch = emergency.kill("account", "INCIDENT", "risk-1")
    assert kill_latch.latch_id == "kill"
    assert kill_latch.scope == "account"
    assert kill_latch.active is True
    assert kill_latch.reason == "INCIDENT"

    reset_latch = emergency.reset_kill("account", "RESOLVED", "risk-2")
    assert reset_latch.latch_id == "kill"
    assert reset_latch.active is False


def test_soft_breakers_and_pause_resume():
    emergency = Emergency()

    soft = emergency.soft_breaker("strategy-1", "VOLATILITY", "risk-1")
    assert soft.latch_id == "soft:strategy-1"
    assert soft.active is True

    clear_soft = emergency.clear_soft_breaker("strategy-1", "COOLDOWN_EXPIRED", "risk-2")
    assert clear_soft.active is False

    pause = emergency.pause_strategy("strategy-1", "OPERATOR_PAUSE", "risk-1")
    assert pause.latch_id == "pause:strategy-1"
    assert pause.active is True

    resume = emergency.resume_strategy("strategy-1", "OPERATOR_RESUME", "risk-2")
    assert resume.active is False


# ── Flatten workflow & residuals ───────────────────────────────────


def test_emergency_flatten_workflow():
    emergency = Emergency()

    # 1. Normal flatten with position
    state, kill = emergency.flatten(
        scope="account",
        workflow_id="wf-1",
        instrument_id="BTCUSDT",
        position_lots=10,
        venue_available=True,
        data_trustworthy=True,
    )
    assert kill.active is True
    assert state.status == "RUNNING"
    assert state.remaining_lots == 10

    # Partial fill
    state = emergency.update_flatten("wf-1", filled_lots=4, min_lots=1)
    assert state.status == "RUNNING"
    assert state.remaining_lots == 6

    # Complete fill
    state = emergency.update_flatten("wf-1", filled_lots=6, min_lots=1)
    assert state.status == "SUCCEEDED"
    assert state.remaining_lots == 0


def test_flatten_venue_outage_and_dust_residual():
    emergency = Emergency()

    # Outage -> FLATTEN_BLOCKED per §11.2
    state, _ = emergency.flatten(
        scope="account",
        workflow_id="wf-outage",
        instrument_id="BTCUSDT",
        position_lots=10,
        venue_available=False,
    )
    assert state.status == "FLATTEN_BLOCKED"
    assert state.residual_reason == "VENUE_UNAVAILABLE"

    # Dust residual below minimum -> RESIDUAL_BELOW_MINIMUM per §11.2
    state, _ = emergency.flatten(
        scope="account",
        workflow_id="wf-dust",
        instrument_id="BTCUSDT",
        position_lots=10,
        venue_available=True,
    )
    # Fill 9 lots, 1 lot remains, but min_lots is 2
    state = emergency.update_flatten("wf-dust", filled_lots=9, min_lots=2)
    assert state.status == "PARTIAL"
    assert state.residual_reason == "RESIDUAL_BELOW_MINIMUM"
    assert state.remaining_lots == 1


# ── Full Risk.evaluate pre-trade checks ─────────────────────────────


def test_risk_evaluate_pre_trade():
    config = RiskConfig(max_spread_bps=Decimal("10"))
    risk = Risk(config)
    spec = fixture_spec()

    intent = StrategyIntent(
        intent_id="intent-1",
        strategy_id="strat-1",
        instrument_id=spec.instrument_id,
        decision_seq=1,
        feature_snapshot_id="feat-1",
        config_hash="conf-1",
        model_hash_or_none=None,
        action=IntentAction.ENTER,
        side=Side.BUY,
        desired_quantity=Decimal("0.1"),
        risk_budget=Decimal("50"),
        price_policy="MARKET",
        expires_at_ns=10_000_000_000,
        stop_policy="FIXED",
        reason="SIGNAL",
    )

    base_context = RiskContext(
        mode="DEMO",
        live_enabled=False,
        environment="DEMO",
        reconciled=True,
        active_latches={},
        intent_id="intent-1",
        consumed_intents=set(),
        open_entry_orders_symbol=0,
        open_orders_account=0,
        spread_bps=Decimal("2"),
        event_available_ns=1_000_000_000,
        market_data_ns=999_000_000,
        mark_ns=999_000_000,
        heartbeat_ns=999_000_000,
        ingress_ns=999_000_000,
        clock_offset_ms=5,
        baseline_equity=Decimal("10000"),
        current_equity=Decimal("10000"),
        net_external_flow=Decimal("0"),
        peak_equity=Decimal("10000"),
        current_price=Decimal("50000"),
        stop_distance=Decimal("500"),
        estimated_roundtrip_cost=Decimal("1"),
        pending_notional=Decimal("0"),
        symbol_notional=Decimal("0"),
        spec=spec,
        visible_depth_lots=10_000,
        risk_version="risk-1",
    )

    # 1. Normal approval
    decision = risk.evaluate(intent, base_context)
    assert decision.approved is True
    assert decision.reason_code == "OK"
    assert decision.approved_quantity_lots > 0

    # 2. Blocked by kill latch
    latch_ctx = replace(base_context, active_latches={"kill": True})
    decision = risk.evaluate(intent, latch_ctx)
    assert decision.approved is False
    assert "LATCH_ACTIVE" in decision.reason_code

    # 3. Blocked by wide spread
    spread_ctx = replace(base_context, spread_bps=Decimal("15"))
    decision = risk.evaluate(intent, spread_ctx)
    assert decision.approved is False
    assert decision.reason_code == "SPREAD_TOO_WIDE"

    # 4. Blocked by duplicate intent
    dup_ctx = replace(base_context, consumed_intents={"intent-1"})
    decision = risk.evaluate(intent, dup_ctx)
    assert decision.approved is False
    assert decision.reason_code == "DUPLICATE_INTENT"

    # 5. Exit intents bypass entry sizing and latches
    exit_intent = replace(intent, action=IntentAction.EXIT, side=Side.SELL)
    decision = risk.evaluate(exit_intent, latch_ctx)
    assert decision.approved is True
    assert decision.reason_code == "OK"

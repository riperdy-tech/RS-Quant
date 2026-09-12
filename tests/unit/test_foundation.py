import json
import math
import subprocess
import sys
import traceback
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantdesk.config.loader import ConfigError, load_config
from quantdesk.config.schema import AccountConfig, Mode
from quantdesk.core.clock import SystemClock, VirtualClock
from quantdesk.core.events import (
    PAYLOAD_TYPES,
    Envelope,
    ExecutionReport,
    IncomingEvent,
    InstrumentSpecUpdated,
    OrderInstruction,
    StrategyIntent,
    Trade,
    canonical_bytes,
)
from quantdesk.core.ids import derive_id
from quantdesk.core.types import (
    ExecutionType,
    IntentAction,
    OrderType,
    Side,
    TimeInForce,
)

REQUIRED_PAYLOAD_TYPES = {
    "BookSnapshot",
    "BookDelta",
    "Trade",
    "Quote",
    "BarClosed",
    "MarkPrice",
    "FundingRateAnnounced",
    "InstrumentSpecUpdated",
    "ConnectionChanged",
    "BookValidityChanged",
    "DataGap",
    "ClockAdjusted",
    "RecorderHealthChanged",
    "FeatureSnapshot",
    "StrategyIntent",
    "RiskDecision",
    "IntentRejected",
    "OrderInstruction",
    "SubmitTransportResult",
    "OrderReport",
    "ExecutionReport",
    "CancelTransportResult",
    "ProtectionReport",
    "ReconciliationObservation",
    "FundingSettlement",
    "FeeAdjustment",
    "CashTransfer",
    "AccountSnapshotObserved",
    "LedgerAdjustmentApproved",
    "PositionDiscrepancy",
    "TimerFired",
    "OperatorCommand",
    "CommandResult",
    "RiskLatchChanged",
    "ModelActivated",
    "ConfigActivated",
    "SimulatedLiquidationTriggered",
    "CheckpointWritten",
    "RunBoundary",
}

PAYLOAD_CONTRACTS = {
    "BookSnapshot": {"bids", "asks", "native_sequence", "checksum"},
    "BookDelta": {"bid_updates", "ask_updates", "native_sequence", "prior_sequence"},
    "Trade": {"native_trade_id", "price_ticks", "size_lots", "aggressor_side"},
    "Quote": {"bid_price_ticks", "bid_size_lots", "ask_price_ticks", "ask_size_lots"},
    "BarClosed": {"start_ns", "end_ns", "open_ticks", "close_ticks", "volume_lots"},
    "MarkPrice": {"price", "event_ns", "source"},
    "FundingRateAnnounced": {"rate", "settlement_ns"},
    "InstrumentSpecUpdated": {
        "instrument_id",
        "revision_hash",
        "tick_size",
        "quantity_step",
        "contract_multiplier",
        "min_quantity",
        "min_notional",
        "supported_margin_modes",
        "supported_position_modes",
    },
    "ConnectionChanged": {"channel", "connected", "reason"},
    "BookValidityChanged": {"validity_epoch", "state", "reason"},
    "DataGap": {"channel", "start_sequence", "end_sequence", "reason"},
    "ClockAdjusted": {"prior_available_ns", "next_available_ns", "reason"},
    "RecorderHealthChanged": {"healthy", "durable_watermark", "reason"},
    "FeatureSnapshot": {
        "snapshot_id",
        "decision_seq",
        "available_ns",
        "config_hash",
        "schema_hash",
        "feature_names",
        "feature_values",
        "source_watermark",
    },
    "StrategyIntent": {
        "intent_id",
        "strategy_id",
        "instrument_id",
        "decision_seq",
        "feature_snapshot_id",
        "config_hash",
        "model_hash_or_none",
        "action",
        "side",
        "desired_quantity",
        "risk_budget",
        "price_policy",
        "expires_at_ns",
        "stop_policy",
        "reason",
    },
    "RiskDecision": {
        "intent_id",
        "approved",
        "reason_code",
        "approved_quantity_lots",
        "risk_version",
    },
    "IntentRejected": {"intent_id", "reason_code", "message"},
    "OrderInstruction": {
        "instruction_id",
        "client_order_id",
        "parent_intent_id",
        "account_id",
        "environment",
        "instrument_id",
        "side",
        "quantity_lots",
        "price_ticks",
        "order_type",
        "time_in_force",
        "reduce_only",
        "native_trigger_basis",
        "native_trigger_value",
        "owner_strategy_id",
        "protection_group_id",
        "expires_at_ns",
        "risk_version",
        "gateway_fence",
    },
    "SubmitTransportResult": {"instruction_id", "accepted", "venue_order_id", "error_code"},
    "OrderReport": {
        "client_order_id",
        "venue_order_id",
        "lifecycle",
        "cumulative_fill_lots",
        "event_ns",
    },
    "ExecutionReport": {
        "native_execution_id",
        "client_order_id",
        "venue_order_id",
        "side",
        "executed_lots",
        "price",
        "event_ns",
        "receipt_ns",
        "fee_amount",
        "fee_currency",
        "fee_rate",
        "maker",
        "execution_type",
        "native_realized_pnl",
    },
    "CancelTransportResult": {"client_order_id", "accepted", "error_code"},
    "ProtectionReport": {
        "protection_group_id",
        "status",
        "protected_lots",
        "observed_ns",
        "reason",
    },
    "ReconciliationObservation": {
        "observation_id",
        "start_receive_seq",
        "end_receive_seq",
        "converged",
        "discrepancies",
    },
    "FundingSettlement": {"native_transaction_id", "instrument_id", "amount", "asset"},
    "FeeAdjustment": {"native_transaction_id", "native_execution_id", "amount", "asset"},
    "CashTransfer": {"native_transaction_id", "amount", "asset", "direction", "event_ns"},
    "AccountSnapshotObserved": {
        "observation_id",
        "wallet_balances",
        "positions",
        "observed_ns",
    },
    "LedgerAdjustmentApproved": {"adjustment_id", "amount", "asset", "reason", "author"},
    "PositionDiscrepancy": {"instrument_id", "expected_lots", "observed_lots", "reason"},
    "TimerFired": {"timer_id", "due_ns", "scheduled_by_event_id", "actual_available_ns"},
    "OperatorCommand": {"command_id", "command_type", "target", "body_hash"},
    "CommandResult": {"command_id", "state", "reason", "applied_seq"},
    "RiskLatchChanged": {"latch_id", "scope", "active", "reason", "risk_version"},
    "ModelActivated": {"strategy_id", "prior_model_hash", "model_hash", "activation_seq"},
    "ConfigActivated": {"prior_config_hash", "config_hash", "activation_seq"},
    "SimulatedLiquidationTriggered": {"instrument_id", "position_lots", "mark_price", "reason"},
    "CheckpointWritten": {"checkpoint_id", "engine_seq", "deterministic_state_hash"},
    "RunBoundary": {"run_id", "boundary", "available_ns"},
    "FinancialEventObserved": {
        "financial_payload",
        "aliases",
        "source_observation_ids",
        "trading_adjustment",
    },
    "ReservationChanged": {
        "reservation_id",
        "instrument_id",
        "side",
        "remaining_lots",
        "cash_amount",
        "fee_buffer",
        "reduce_only",
        "revision",
    },
    "ConversionRateObserved": {"base_asset", "quote_asset", "rate", "event_ns", "source"},
    "ReservationBatchChanged": {"instrument_id", "reservations"},
    "OrderApproved": {"instruction", "cash_reserve", "fee_reserve"},
    "CancelRequested": {
        "instruction_id",
        "client_order_id",
        "expires_at_ns",
        "risk_version",
        "gateway_fence",
    },
    "DispatchStarted": {"instruction_id", "attempt_id"},
    "CancelAttemptObserved": {"instruction_id", "result"},
    "UnsentAborted": {"instruction_id", "reason"},
}


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_safe_default_and_exact_boundary(case):
    r = case("foundation", qty="0.001", timestamp_ns="1789200000000000001")
    assert r["mode"] == "DEMO" and r["live_enabled"] is False
    assert r["roundtrip_qty"] == "0.001"
    assert r["roundtrip_timestamp_ns"] == "1789200000000000001"
    assert r["ids_match_across_processes"] is True


def test_canonical_bytes_are_sorted_exact_and_reject_non_finite_values() -> None:
    value = {
        "z": 1,
        "timestamp_ns": 1_789_200_000_000_000_001,
        "qty": Decimal("0.0010"),
        "a": [True, None],
    }
    encoded = canonical_bytes(value)
    assert encoded == (
        b'{"a":[true,null],"qty":"0.0010","timestamp_ns":"1789200000000000001","z":1}'
    )
    assert json.loads(encoded)["qty"] == "0.0010"
    assert json.loads(encoded)["timestamp_ns"] == "1789200000000000001"
    with pytest.raises(ValueError, match="finite"):
        canonical_bytes({"bad": math.nan})
    with pytest.raises(ValueError, match="finite"):
        canonical_bytes({"bad": Decimal("Infinity")})


def test_ids_are_stable_across_processes_and_inputs_are_domain_separated() -> None:
    expected = derive_id("intent", "parent", "producer", 7)
    script = (
        "from quantdesk.core.ids import derive_id; "
        "print(derive_id('intent', 'parent', 'producer', 7))"
    )
    actual = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual == expected
    assert derive_id("event", "parent", "producer", 7) != expected
    assert derive_id("intent", "parent", "producer", 8) != expected
    with pytest.raises(ValueError, match="non-negative"):
        derive_id("intent", "parent", "producer", -1)


def test_envelopes_and_payloads_are_immutable_and_registry_is_complete() -> None:
    payload = Trade(
        native_trade_id="trade-1",
        price_ticks=10_000,
        size_lots=2,
        aggressor_side=Side.BUY,
        venue_extensions=(),
    )
    incoming = IncomingEvent(
        event_type="Trade",
        schema_version=1,
        run_id="run-1",
        account_id=None,
        venue="fixture",
        environment="DEMO",
        instrument_id="fixture:linear:BTC:USDT:USDT:BTCUSDT",
        source_channel="trades",
        connection_epoch="epoch-1",
        source_message_id="trade-1",
        source_sequence=None,
        exchange_event_ns=123,
        exchange_transaction_ns=None,
        receive_wall_ns=200,
        receive_monotonic_ns=10,
        available_ns=210,
        causation_id=None,
        correlation_id="corr-1",
        raw_ref="raw-1",
        producer_version="test",
        payload=canonical_bytes(payload),
    )
    envelope = Envelope(
        event_id="event-1",
        engine_seq=1,
        **{name: getattr(incoming, name) for name in incoming.__dataclass_fields__},
    )
    assert PAYLOAD_TYPES.keys() >= REQUIRED_PAYLOAD_TYPES
    assert PAYLOAD_TYPES.keys() == PAYLOAD_CONTRACTS.keys()
    for event_type, required_fields in PAYLOAD_CONTRACTS.items():
        actual_fields = {field.name for field in fields(PAYLOAD_TYPES.resolve(event_type))}
        assert actual_fields >= required_fields, event_type
        assert "fields" not in actual_fields, event_type
    assert PAYLOAD_TYPES.resolve("Trade") is Trade
    assert json.loads(envelope.payload) == {
        "aggressor_side": "BUY",
        "native_trade_id": "trade-1",
        "price_ticks": 10_000,
        "size_lots": 2,
        "venue_extensions": [],
    }
    with pytest.raises(FrozenInstanceError):
        envelope.engine_seq = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        payload.size_lots = 3  # type: ignore[misc]


def test_economic_payloads_require_explicit_fields_and_validate_units() -> None:
    with pytest.raises(TypeError):
        Trade()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="size_lots must be positive"):
        Trade("trade-1", 10_000, 0, Side.BUY, ())

    intent = StrategyIntent(
        intent_id="intent-1",
        strategy_id="strategy-1",
        instrument_id="fixture:linear:BTC:USDT:USDT:BTCUSDT",
        decision_seq=9_007_199_254_740_993,
        feature_snapshot_id="features-1",
        config_hash="config-1",
        model_hash_or_none=None,
        action=IntentAction.ENTER,
        side=Side.BUY,
        desired_quantity=Decimal("0.001"),
        risk_budget=None,
        price_policy="POST_ONLY_BEST",
        expires_at_ns=1_789_200_000_000_000_001,
        stop_policy="ATR_1_5",
        reason="imbalance edge",
    )
    instruction = OrderInstruction(
        instruction_id="instruction-1",
        client_order_id="demo-order-1",
        parent_intent_id=intent.intent_id,
        account_id="demo",
        environment="DEMO",
        instrument_id=intent.instrument_id,
        side=Side.BUY,
        quantity_lots=1,
        price_ticks=10_000,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.POST_ONLY,
        reduce_only=False,
        native_trigger_basis=None,
        native_trigger_value=None,
        owner_strategy_id=intent.strategy_id,
        protection_group_id="protection-1",
        expires_at_ns=intent.expires_at_ns,
        risk_version="risk-1",
        gateway_fence="fence-1",
    )
    execution = ExecutionReport(
        native_execution_id="exec-1",
        client_order_id=instruction.client_order_id,
        venue_order_id="venue-1",
        side=Side.BUY,
        executed_lots=1,
        price=Decimal("100.12500000"),
        event_ns=1_789_200_000_000_000_001,
        receipt_ns=1_789_200_000_000_000_021,
        fee_amount=Decimal("0.00010000"),
        fee_currency="USDT",
        fee_rate=Decimal("0.0002"),
        maker=True,
        execution_type=ExecutionType.TRADE,
        native_realized_pnl=None,
    )
    encoded = json.loads(canonical_bytes({"intent": intent, "execution": execution}))
    assert encoded["intent"]["desired_quantity"] == "0.001"
    assert encoded["intent"]["decision_seq"] == "9007199254740993"
    assert encoded["execution"]["price"] == "100.12500000"
    assert encoded["execution"]["event_ns"] == "1789200000000000001"
    with pytest.raises(ValueError, match="quantity_lots must be positive"):
        replace(instruction, quantity_lots=0)


def test_instrument_spec_payload_preserves_decimal_units() -> None:
    spec = InstrumentSpecUpdated(
        instrument_id="bitget:USDT-FUTURES:BTC:USDT:USDT:BTCUSDT",
        valid_from_ns=1_789_200_000_000_000_001,
        known_from_ns=1_789_200_000_000_000_101,
        revision_hash="spec-1",
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.0001"),
        contract_multiplier=Decimal("1"),
        base_unit="BTC",
        quote_unit="USDT",
        settlement_unit="USDT",
        min_quantity=Decimal("0.0001"),
        max_quantity=Decimal("1000"),
        min_notional=Decimal("5"),
        trading_status="TRADING",
        min_leverage=Decimal("1"),
        max_leverage=Decimal("125"),
        supported_margin_modes=("isolated",),
        supported_position_modes=("one_way",),
        funding_schedule=("00:00Z", "08:00Z", "16:00Z"),
        venue_extensions=(),
    )
    encoded = json.loads(canonical_bytes(spec))
    assert encoded["quantity_step"] == "0.0001"
    assert encoded["valid_from_ns"] == "1789200000000000001"
    assert encoded["supported_margin_modes"] == ["isolated"]


def test_demo_config_is_frozen_and_uses_engineering_defaults() -> None:
    config = load_config(Path("configs/demo.yaml"))
    assert config.mode is Mode.DEMO
    assert config.account.live_enabled is False
    assert config.account.virtual_equity_usdt == Decimal("10000")
    assert config.risk.max_open_orders_account == 10
    with pytest.raises(ValidationError):
        config.mode = Mode.LIVE  # type: ignore[misc]


@pytest.mark.parametrize(
    "body, expected",
    [
        (
            "mode: DEMO\naccount:\n  live_enabled: true\n",
            "static configuration cannot arm LIVE",
        ),
        (
            "mode: DEMO\nrisk:\n  max_open_orders_account: 0\n",
            "greater than 0",
        ),
        (
            "mode: DEMO\nrisk:\n  per_trade_risk_fraction: '1.1'\n",
            "less than or equal to 1",
        ),
        ("mode: DEMO\nunknown: true\n", "unknown"),
    ],
)
def test_unsafe_or_inconsistent_config_is_rejected(
    tmp_path: Path, body: str, expected: str
) -> None:
    with pytest.raises(ConfigError, match=expected):
        load_config(_write_yaml(tmp_path, body))


def test_embedded_secret_is_rejected_without_echoing_value(tmp_path: Path) -> None:
    secret = "DO-NOT-ECHO-this-private-value"
    path = _write_yaml(
        tmp_path,
        f"mode: LIVE\naccount:\n  live_enabled: false\n  api_key: {secret}\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    message = str(exc_info.value)
    assert secret not in message
    assert "account.api_key" in message
    assert "secret reference" in message


def test_unquoted_financial_decimal_is_rejected_before_precision_loss(tmp_path: Path) -> None:
    decimal_text = "0.123456789012345678901234567890123456789"
    path = _write_yaml(
        tmp_path,
        f"mode: DEMO\nrisk:\n  per_trade_risk_fraction: {decimal_text}\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "quoted decimal string" in str(exc_info.value)
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert decimal_text not in rendered


@pytest.mark.parametrize(
    "body, secret_text",
    [
        ("mode: DEMO\nunknown_field: traceback-secret-1\n", "traceback-secret-1"),
        ("mode: DEMO\naccount: [traceback-secret-2\n", "traceback-secret-2"),
    ],
)
def test_config_error_tracebacks_never_render_source_values(
    tmp_path: Path, body: str, secret_text: str
) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(_write_yaml(tmp_path, body))
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert secret_text not in rendered
    assert "pydantic_core" not in rendered
    assert "yaml.parser" not in rendered


def test_live_profile_is_structurally_disarmed_and_true_is_always_rejected(
    tmp_path: Path,
) -> None:
    live = load_config(Path("configs/live.yaml"))
    assert live.mode is Mode.LIVE
    assert live.account.live_enabled is False
    live_enabled_schema = AccountConfig.model_json_schema()["properties"]["live_enabled"]
    assert live_enabled_schema["const"] is False

    attempted = _write_yaml(
        tmp_path,
        """mode: LIVE
account:
  live_enabled: true
  credential_ref:
    provider: keyring
    name: quantdesk/bitget-uta/live
risk:
  max_order_notional_usdt: "1"
  max_total_notional_usdt: "1"
  max_daily_loss_usdt: "1"
""",
    )
    with pytest.raises(ConfigError, match="static configuration cannot arm LIVE"):
        load_config(attempted)


def test_virtual_clock_is_explicit_and_cannot_move_backward() -> None:
    clock = VirtualClock(100)
    assert clock.now_ns() == 100
    clock.advance(23)
    assert clock.now_ns() == 123
    clock.advance_to(150)
    assert clock.now_ns() == 150
    with pytest.raises(ValueError, match="backward"):
        clock.advance_to(149)
    assert SystemClock().now_ns() > 0


def test_developer_cli_reports_version_and_safe_diagnostics() -> None:
    version = subprocess.run(
        [sys.executable, "-m", "quantdesk.cli", "version"],
        check=True,
        capture_output=True,
        text=True,
    )
    diagnostics = subprocess.run(
        [sys.executable, "-m", "quantdesk.cli", "diagnostics"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(diagnostics.stdout)
    assert version.stdout.strip() == "0.1.0"
    assert report["version"] == "0.1.0"
    assert report["schema_version"] == 1
    assert report["mode"] == "DEMO"
    assert report["live_enabled"] is False
    assert "credential" not in diagnostics.stdout.casefold()

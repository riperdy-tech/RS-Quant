import json
import math
import subprocess
import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantdesk.config.loader import ConfigError, load_config
from quantdesk.config.schema import Mode
from quantdesk.core.clock import SystemClock, VirtualClock
from quantdesk.core.events import (
    PAYLOAD_TYPES,
    Envelope,
    IncomingEvent,
    Trade,
    canonical_bytes,
)
from quantdesk.core.ids import derive_id

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
        b'{"a":[true,null],"qty":"0.0010","timestamp_ns":'
        b'"1789200000000000001","z":1}'
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
    payload = Trade(fields=(("native_trade_id", "trade-1"), ("size_lots", 2)))
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
    assert PAYLOAD_TYPES.resolve("Trade") is Trade
    assert json.loads(envelope.payload)["fields"][0] == ["native_trade_id", "trade-1"]
    with pytest.raises(FrozenInstanceError):
        envelope.engine_seq = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        payload.fields = ()  # type: ignore[misc]


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
            "live_enabled may only be true in LIVE mode",
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

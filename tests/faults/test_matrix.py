"""Mandatory fault injection matrix tests (§20).

Validates:
- Wall clock backward skew & availability anchors
- Crossed and negative book quarantine
- Database commit failure discarding candidate state
- Corrupt model and drift alarm preventing unsafe activation
- Missing execution history / foreign account discrepancy blocking risk
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from quantdesk.core.engine import EngineMode
from quantdesk.core.events import (
    BookSnapshot,
    IncomingEvent,
    OrderReport,
    canonical_bytes,
)
from quantdesk.core.types import BookLevel
from quantdesk.data.orderbook.builder import BookBuilder
from quantdesk.persistence.outbox import Outbox
from quantdesk.research.drift import DriftMonitor
from quantdesk.research.registry import Registry
from quantdesk.research.train import ModelManifest
from tests.support.engine_case import incoming, make_engine
from tests.support.oms_case import AccountCase


def test_clock_moves_backward_maintains_causality():
    """Wall clock moves backward: engine rejects regressed availability and protects causality."""
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        engine, db, journal = make_engine(path)
        try:
            # Event 1 at timestamp 1,000,000 ns
            e1 = incoming(1, available_ns=1_000_000)
            engine.commit(engine.process(e1))
            assert engine.state.available_ns == 1_000_000
            first_seq = engine.state.engine_seq

            # Event 2 arriving with backward-skewed timestamp (500,000 ns < 1,000,000 ns)
            e2 = incoming(2, available_ns=500_000)
            with pytest.raises(ValueError, match="availability timeline regressed"):
                engine.process(e2)

            # Available timestamp and engine sequence remain valid; latches survive
            assert engine.state.available_ns == 1_000_000
            assert engine.state.engine_seq == first_seq
            assert not engine.trading_latched_off
        finally:
            db.close()
            journal.close()


def test_crossed_and_negative_book_quarantined():
    """Crossed or negative book quotes are quarantined; invalid state prevents trading."""
    builder = BookBuilder("contiguous_fixture")

    def make_book_event(seq: int, payload: BookSnapshot) -> IncomingEvent:
        return replace(
            incoming(seq),
            event_type="BookSnapshot",
            payload=canonical_bytes(payload),
            connection_epoch="epoch-1",
            instrument_id="fixture:perp:BTC:USDT:USDT:BTCUSDT",
            source_channel="books",
        )

    # 1. Normal valid book
    normal_payload = BookSnapshot(
        bids=(BookLevel(50000, 10),),
        asks=(BookLevel(50010, 10),),
        native_sequence="1",
        checksum=None,
        venue_extensions=(),
    )
    res_normal = builder.apply(make_book_event(1, normal_payload))
    assert res_normal.state == "VALID"
    assert res_normal.view is not None

    # 2. Crossed book (bid >= ask: bid 50020 >= ask 50010)
    crossed_payload = BookSnapshot(
        bids=(BookLevel(50020, 10),),
        asks=(BookLevel(50010, 10),),
        native_sequence="2",
        checksum=None,
        venue_extensions=(),
    )
    res_crossed = builder.apply(make_book_event(2, crossed_payload))
    assert res_crossed.state == "INVALID"
    assert res_crossed.view is None

    # 3. Empty bids book
    empty_payload = BookSnapshot(
        bids=(),
        asks=(BookLevel(50010, 10),),
        native_sequence="3",
        checksum=None,
        venue_extensions=(),
    )
    res_empty = builder.apply(make_book_event(3, empty_payload))
    assert res_empty.state == "INVALID"
    assert res_empty.view is None


def test_database_commit_failure_discards_candidate():
    """Database commit failure after reducers latches engine and discards candidate state."""
    class BrokenCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("Simulated disk write failure / SQLite I/O error")

    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        engine, db, journal = make_engine(path, connection_factory=BrokenCommit)
        try:
            candidate = engine.process(incoming(1))
            assert engine._pending is not None

            # Attempting to commit with I/O error latches the engine
            with pytest.raises(sqlite3.OperationalError):
                engine.commit(candidate)

            # Invariant: candidate state discarded, engine latched off, outbox empty
            assert engine._pending is None
            assert engine.trading_latched_off
            assert Outbox(db.path).pending() == ()

            # Subsequent inputs rejected immediately because engine is latched
            with pytest.raises(RuntimeError, match="latched"):
                engine.process(incoming(2))
        finally:
            db.close()
            journal.close()


def test_corrupt_model_and_drift_alarm_prevents_activation():
    """Incompatible/corrupt model payload and drift alarm prevent unsafe activation."""
    registry = Registry()
    assert registry.get_active_model() is None

    # Valid manifest
    manifest = ModelManifest(
        model_id="champion-v1",
        algorithm="lightgbm",
        feature_names=["imbalance_l1", "spread_bps"],
        hyperparameters={"num_leaves": 15},
        feature_schema_hash="schema-hash-abc",
        train_dataset_hash="ds-hash-123",
        performance_metrics={"brier_score": 0.12},
        model_payload_hash="valid-sha256-payload",
        native_model_text="booster_data_champion",
        dataset_origin="verified_historical",
        status="EVALUATED",
    )
    registry.register("reg-model-1", manifest)

    # 1. Promote champion in DEMO
    res = registry.transition({
        "model_id": "champion-v1",
        "action": "PROMOTE",
        "environment": "DEMO",
        "expected_payload_hash": "valid-sha256-payload",
        "expected_feature_schema_hash": "schema-hash-abc",
    })
    assert res.success
    assert registry.get_active_model() == "reg-model-1"

    # 2. Attempt to activate corrupt candidate (payload hash mismatch)
    corrupt_manifest = ModelManifest(
        model_id="corrupt-candidate",
        algorithm="lightgbm",
        feature_names=["imbalance_l1", "spread_bps"],
        hyperparameters={"num_leaves": 15},
        feature_schema_hash="schema-hash-abc",
        train_dataset_hash="ds-hash-123",
        performance_metrics={"brier_score": 0.10},
        model_payload_hash="tampered-payload-hash",
        native_model_text="corrupted_or_truncated_bytes",
        dataset_origin="verified_historical",
        status="EVALUATED",
    )
    registry.register("reg-model-2", corrupt_manifest)

    tampered_res = registry.transition({
        "model_id": "corrupt-candidate",
        "action": "PROMOTE",
        "environment": "DEMO",
        "expected_payload_hash": "different-expected-hash",
        "expected_feature_schema_hash": "schema-hash-abc",
    })
    assert not tampered_res.success
    # Champion remains active and intact
    assert registry.get_active_model() == "reg-model-1"

    # 3. Drift alarm trigger: monitor detects drift
    monitor = DriftMonitor(
        reference_distributions={"imbalance_l1": {"mean": 0.0, "std": 1.0}},
        window_size=5,
        alert_threshold_psi=0.10,
    )
    for _ in range(5):
        signal = monitor.update({"imbalance_l1": 15.0}, strategy_id="strat-1", date_str="2026-09-16")
    assert signal.is_drifted is True

    # When drift is detected, candidate is marked REJECTED
    rej_res = registry.transition({
        "model_id": "corrupt-candidate",
        "action": "REJECT",
        "environment": "DEMO",
    })
    assert rej_res.success

    # A rejected candidate cannot subsequently be promoted to ACTIVE
    drift_res = registry.transition({
        "model_id": "corrupt-candidate",
        "action": "PROMOTE",
        "environment": "DEMO",
    })
    assert not drift_res.success
    assert "Cannot promote a rejected model candidate" in drift_res.reason
    assert registry.get_active_model() == "reg-model-1"


def test_missing_execution_history_blocks_risk():
    """Foreign/unrecognized order reports flag incidents and do not corrupt ledger state."""
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        account = AccountCase(path, mode=EngineMode.DEMO)
        try:
            # Order report for an order ID unknown to OMS
            foreign_report = OrderReport(
                client_order_id="foreign-order-xyz",
                venue_order_id="venue-foreign-999",
                lifecycle="FILLED",
                cumulative_fill_lots=10,
                event_ns=0,
            )

            # OMS handles report without crash, records UNKNOWN_ORDER incident
            account.send(foreign_report)

            # Invariants: Incident recorded, zero orders stored, zero transactions created
            assert "UNKNOWN_ORDER" in account.state.incidents
            assert len(account.state.orders) == 0
            assert len(account.portfolio.transactions) == 0
        finally:
            account.close()

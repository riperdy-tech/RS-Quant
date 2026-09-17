import json
from decimal import Decimal
from pathlib import Path

import pytest

from quantdesk.core.events import Envelope
from quantdesk.research.backtest import Backtest, BacktestSpec
from quantdesk.simulation.fees import FeeProfile
from quantdesk.simulation.latency import LatencyProfile
from quantdesk.strategies.imbalance import ImbalanceScalper
from quantdesk.strategies.momentum import MomentumBreakout
from quantdesk.venues.capabilities import UnsupportedCapability


def test_purged_rows_and_reproducible_backtest(case):
    r = case("backtest_and_purged_folds")
    assert r["train_label_validation_overlaps"] == 0
    assert r["holdout_rows_used_for_selection"] == 0
    assert r["run_a_state_hash"] == r["run_b_state_hash"]
    assert r["report_contains_fees_funding_and_assumptions"] is True


def test_backtest_zero_trades_is_valid_outcome():
    """Gate: a losing or zero-trade run is still a valid software outcome;
    reports cannot silently omit costs.
    """
    spec = BacktestSpec(
        run_id="zero-trade-run",
        strategy=None,
        dataset_events=[],
        starting_balance=Decimal("10000"),
        assumptions={"calendar_convention": "crypto_24_7_365"},
    )
    bt = Backtest(run_id="zero-trade-run")
    manifest = bt.run(spec)

    assert manifest.run_id == "zero-trade-run"
    assert manifest.final_balance == Decimal("10000")
    assert manifest.metrics["total_trades"] == 0
    assert manifest.metrics["closed_trades"] == 0
    assert manifest.metrics["win_rate"] == 0.0
    assert "not meaningful" in manifest.metrics["profit_factor"]
    assert manifest.metrics["max_drawdown"] == "0"
    assert manifest.metrics["report_contains_fees_funding_and_assumptions"] is True
    assert len(manifest.trade_trace) == 0
    assert len(manifest.equity_curve) >= 1
    assert manifest.state_hash is not None and len(manifest.state_hash) == 64
    assert "<html" in manifest.html_report.lower()
    assert "zero-trade-run" in manifest.html_report


def test_backtest_capability_rejection():
    """Per §14.5 and Task 11: An L2 strategy cannot select an OHLCV-only dataset."""
    strategy = ImbalanceScalper(instrument_id="BTCUSDT")
    spec = BacktestSpec(
        run_id="cap-reject-run",
        strategy=strategy,
        dataset_events=[],
        dataset_capabilities=frozenset(["OHLCV"]),
    )
    bt = Backtest(run_id="cap-reject-run")

    with pytest.raises(UnsupportedCapability, match="requires L2/BBO capabilities"):
        bt.run(spec)


def test_backtest_bar_only_conservative_execution():
    """Per §14.5 and Task 11: Conservative execution on bar-only events."""
    base_ns = 1_700_000_000_000_000_000
    bar_interval = 60_000_000_000  # 1-minute bars

    # Create 30 closed bars with breakout price action
    bar_events: list[Envelope] = []
    prices = [50000 + i * 20 for i in range(30)]

    for i, p in enumerate(prices):
        t_ns = base_ns + (i + 1) * bar_interval
        bar_events.append(
            Envelope(
                event_type="BarClosed",
                schema_version=1,
                run_id="bar-test",
                account_id=None,
                venue="SIM",
                environment="DEMO",
                instrument_id="BTCUSDT",
                source_channel="market",
                connection_epoch="epoch-1",
                source_message_id=None,
                source_sequence=None,
                exchange_event_ns=t_ns,
                exchange_transaction_ns=None,
                receive_wall_ns=t_ns + 10_000_000,
                receive_monotonic_ns=t_ns + 10_000_000,
                available_ns=t_ns + 20_000_000,
                causation_id=None,
                correlation_id=f"b-{i}",
                raw_ref=None,
                producer_version="v1",
                payload=json.dumps(
                    {
                        "start_ns": t_ns - bar_interval,
                        "end_ns": t_ns,
                        "open_ticks": p - 10,
                        "high_ticks": p + 15,
                        "low_ticks": p - 15,
                        "close_ticks": p,
                        "volume_lots": 100,
                        "synthetic": True,
                    }
                ).encode("utf-8"),
                event_id=f"bar-{i}",
                engine_seq=i + 1,
            )
        )

    strategy = MomentumBreakout(instrument_id="BTCUSDT")
    spec = BacktestSpec(
        run_id="bar-conservative-run",
        strategy=strategy,
        dataset_events=bar_events,
        dataset_capabilities=frozenset(["OHLCV"]),
        starting_balance=Decimal("100000"),
        fill_model="conservative",
    )
    bt = Backtest(run_id="bar-conservative-run")
    manifest = bt.run(spec)

    assert manifest.run_id == "bar-conservative-run"
    assert manifest.fill_model == "conservative"
    assert manifest.starting_balance == Decimal("100000")
    assert manifest.metrics["report_contains_fees_funding_and_assumptions"] is True
    assert len(manifest.equity_curve) == len(bar_events) + 1


def test_backtest_standalone_html_report_export():
    """Verifies standalone HTML report contains complete metadata, metrics, and trades."""
    events_path = Path("fixtures/ml/demo_market_events.json")
    if not events_path.exists():
        from scripts.make_fixtures import generate_demo_fixtures

        generate_demo_fixtures()

    raw_events = json.loads(events_path.read_text())[:50]
    events = [
        Envelope(
            event_id=ev["event_id"],
            event_type=ev["event_type"],
            schema_version=1,
            run_id="demo",
            account_id=None,
            venue="SIM",
            environment="DEMO",
            instrument_id="BTCUSDT",
            source_channel="market",
            connection_epoch="epoch-1",
            source_message_id=None,
            source_sequence=None,
            exchange_event_ns=ev.get("exchange_event_ns"),
            exchange_transaction_ns=None,
            receive_wall_ns=ev.get("received_ns", ev.get("exchange_event_ns", 0)),
            receive_monotonic_ns=ev.get("received_ns", ev.get("exchange_event_ns", 0)),
            available_ns=ev["available_ns"],
            causation_id=None,
            correlation_id=ev["correlation_id"],
            raw_ref=None,
            producer_version="v1",
            payload=json.dumps(ev["payload"]).encode("utf-8")
            if isinstance(ev["payload"], dict)
            else ev["payload"],
            engine_seq=ev.get("engine_seq", 0),
        )
        for ev in raw_events
    ]

    spec = BacktestSpec(
        run_id="html-export-run",
        strategy=MomentumBreakout(instrument_id="BTCUSDT"),
        dataset_events=events,
        starting_balance=Decimal("50000"),
    )
    bt = Backtest(run_id="html-export-run")
    manifest = bt.run(spec)

    html = manifest.html_report
    assert "<!DOCTYPE html>" in html
    assert "QuantDesk Backtest Report" in html
    assert manifest.state_hash in html
    assert manifest.config_hash in html
    assert "50000" in html
    assert "Performance & Cost Summary" in html


def test_backtest_stress_profile():
    """Cost/stress profiles: higher fees and latency per §14.5."""
    base_fee = FeeProfile(maker_rate=Decimal("0.0002"), taker_rate=Decimal("0.00055"))
    stress_fee = FeeProfile(maker_rate=Decimal("0.0006"), taker_rate=Decimal("0.00165"))  # 3x fees

    base_latency = LatencyProfile.synthetic_defaults()
    stress_latency = LatencyProfile.stress_profile(multiplier=5)

    spec_base = BacktestSpec(
        run_id="base-profile",
        strategy=None,
        dataset_events=[],
        fee_profile=base_fee,
        latency_profile=base_latency,
        starting_balance=Decimal("10000"),
    )
    spec_stress = BacktestSpec(
        run_id="stress-profile",
        strategy=None,
        dataset_events=[],
        fee_profile=stress_fee,
        latency_profile=stress_latency,
        starting_balance=Decimal("10000"),
    )

    bt_base = Backtest(run_id="base-profile")
    bt_stress = Backtest(run_id="stress-profile")

    manifest_base = bt_base.run(spec_base)
    manifest_stress = bt_stress.run(spec_stress)

    assert manifest_base.config_hash != manifest_stress.config_hash
    assert manifest_base.state_hash is not None
    assert manifest_stress.state_hash is not None

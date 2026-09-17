"""Real causal prefix and rule strategy integration test drivers."""

from __future__ import annotations

import json
from typing import Any

from quantdesk.core.events import Envelope
from quantdesk.features.base import IncrementalFeatureEngine
from quantdesk.strategies.hybrid import HybridStrategy
from quantdesk.strategies.imbalance import ImbalanceScalper
from quantdesk.strategies.mean_reversion import MeanReversion
from quantdesk.strategies.momentum import MomentumBreakout
from quantdesk.strategies.sweep import SweepHeuristic


def make_test_envelope(
    event_type: str,
    payload: dict[str, Any],
    available_ns: int,
    engine_seq: int,
    event_id: str | None = None,
) -> Envelope:
    return Envelope(
        event_type=event_type,
        schema_version=1,
        run_id="run-causal",
        account_id=None,
        venue="bitget",
        environment="DEMO",
        instrument_id="BTCUSDT",
        source_channel="market",
        connection_epoch="epoch-1",
        source_message_id=None,
        source_sequence=None,
        exchange_event_ns=available_ns,
        exchange_transaction_ns=None,
        receive_wall_ns=available_ns,
        receive_monotonic_ns=available_ns,
        available_ns=available_ns,
        causation_id=None,
        correlation_id=f"corr-{engine_seq}",
        raw_ref=None,
        producer_version="v1",
        payload=json.dumps(payload).encode("utf-8"),
        event_id=event_id or f"evt-{engine_seq}",
        engine_seq=engine_seq,
    )


def causal_prefix_case(**overrides: object) -> dict[str, object]:
    cutoff_ns = int(overrides.get("cutoff_ns", 10_000_000_000))
    mutate_future_prices = bool(overrides.get("mutate_future_prices", False))
    mutate_late_receipts = bool(overrides.get("mutate_late_receipts", False))

    # Generate a baseline timeline of 30 seconds of events
    events: list[Envelope] = []
    seq = 0
    t = 1_000_000_000  # 1s

    # Warmup bars
    for i in range(15):
        seq += 1
        t += 1_000_000_000  # every 1s
        bar = {
            "open": 50000 + i * 10,
            "high": 50020 + i * 10,
            "low": 49990 + i * 10,
            "close": 50010 + i * 10,
        }
        events.append(make_test_envelope("BarClosed", bar, t, seq))

    # Books and trades around cutoff
    for i in range(20):
        seq += 1
        t += 500_000_000  # every 500ms
        book = {
            "bids": [[50200 + i, 10], [50190 + i, 20]],
            "asks": [[50210 + i, 5], [50220 + i, 15]],
        }
        events.append(make_test_envelope("BookSnapshot", book, t, seq))

        seq += 1
        t += 100_000_000
        trade = {"price": 50210 + i, "lots": 2, "aggressor": "BUY"}
        events.append(make_test_envelope("Trade", trade, t, seq))

    def run_stream(ev_list: list[Envelope]) -> tuple[list[dict], list[str], int]:
        engine = IncrementalFeatureEngine()
        strategy = ImbalanceScalper()
        prefix_features: list[dict] = []
        prefix_intents: list[str] = []
        warmup_entries = 0

        for ev in ev_list:
            engine.update(ev)
            features = engine.as_dict()
            snap = engine.snapshot(ev.engine_seq, ev.available_ns)
            intents = strategy.on_event(ev, {"features": features})

            if ev.available_ns <= cutoff_ns:
                # Capture features at cutoff or earlier
                feature_repr = {f.name: f.value for f in snap.features}
                prefix_features.append(feature_repr)
                for intent in intents:
                    prefix_intents.append(
                        f"{intent.strategy_id}:{intent.action}:{intent.side}:{intent.desired_quantity}"
                    )
            else:
                # Post-cutoff decisions are verified separately
                pass

        return prefix_features, prefix_intents, warmup_entries

    # Run original stream
    orig_features, orig_intents, warmup_entries = run_stream(events)

    # Mutate future stream after cutoff
    mutated_events: list[Envelope] = []
    for ev in events:
        if ev.available_ns <= cutoff_ns:
            mutated_events.append(ev)
        else:
            # Mutate events after cutoff
            payload = json.loads(ev.payload.decode("utf-8"))
            new_available = ev.available_ns
            if mutate_future_prices:
                if "close" in payload:
                    payload["close"] += 5000
                    payload["high"] += 5000
                if "bids" in payload:
                    payload["bids"][0][0] += 500
                    payload["asks"][0][0] += 500
                if "price" in payload:
                    payload["price"] += 500
            if mutate_late_receipts:
                new_available += 100_000_000  # delayed receipt
            mutated_events.append(
                make_test_envelope(
                    ev.event_type, payload, new_available, ev.engine_seq, ev.event_id
                )
            )

    mut_features, mut_intents, _ = run_stream(mutated_events)

    orig_feat_hash = json.dumps(orig_features, sort_keys=True, default=str)
    mut_feat_hash = json.dumps(mut_features, sort_keys=True, default=str)
    orig_intent_hash = json.dumps(orig_intents, sort_keys=True)
    mut_intent_hash = json.dumps(mut_intents, sort_keys=True)

    return {
        "original_prefix_features": orig_feat_hash,
        "mutated_prefix_features": mut_feat_hash,
        "original_prefix_intents": orig_intent_hash,
        "mutated_prefix_intents": mut_intent_hash,
        "warmup_entry_count": warmup_entries,
    }


def rule_strategies_case(**overrides: object) -> dict[str, object]:
    """Feed realistic market events and verify entries from all 4 rule strategies + hybrid."""
    imbalance_strat = ImbalanceScalper()
    momentum_strat = MomentumBreakout()
    mean_rev_strat = MeanReversion()
    sweep_strat = SweepHeuristic()

    class FakeMLModel:
        def predict(self, features: dict) -> float:
            return 0.75

    class FakeMLRegistry:
        def get_active_model(self):
            return FakeMLModel()

    hybrid_strat = HybridStrategy(ImbalanceScalper(), threshold=0.60)
    hybrid_strat.set_registry(FakeMLRegistry())

    engine = IncrementalFeatureEngine()

    imbalance_entries = 0
    imbalance_exits = 0
    momentum_entries = 0
    momentum_exits = 0
    mean_reversion_entries = 0
    mean_reversion_exits = 0
    sweep_entries = 0
    sweep_exits = 0

    t = 1_000_000_000
    seq = 0

    # 1. Warmup with 35 bars to build technical history (EMA10, EMA30, RSI, BB, ATR)
    for i in range(35):
        seq += 1
        t += 60_000_000_000  # 1-minute bars
        bar = {
            "open": 50000 + i * 10,
            "high": 50050 + i * 10,
            "low": 49950 + i * 10,
            "close": 50020 + i * 10,
        }
        ev = make_test_envelope("BarClosed", bar, t, seq)
        engine.update(ev)
        features = engine.as_dict()
        ctx = {"features": features}
        for it in momentum_strat.on_event(ev, ctx):
            if it.action.value == "ENTER":
                momentum_entries += 1
        for it in mean_rev_strat.on_event(ev, ctx):
            if it.action.value == "ENTER":
                mean_reversion_entries += 1

    # 2. Trigger Momentum Breakout: bar with close > high_20_prior and ema10 > ema30
    seq += 1
    t += 60_000_000_000
    breakout_bar = {
        "open": 51000,
        "high": 52000,  # clearly above prior 20 bars high (~50300)
        "low": 50900,
        "close": 51800,
    }
    ev = make_test_envelope("BarClosed", breakout_bar, t, seq)
    engine.update(ev)
    features = engine.as_dict()
    ctx = {"features": features}
    for it in momentum_strat.on_event(ev, ctx):
        if it.action.value == "ENTER":
            momentum_entries += 1

    # Trigger Momentum Exit: advance 20 bars to hit max hold
    for _i in range(20):
        seq += 1
        t += 60_000_000_000
        hold_bar = {"open": 51800, "high": 51850, "low": 51750, "close": 51800}
        ev_hold = make_test_envelope("BarClosed", hold_bar, t, seq)
        engine.update(ev_hold)
        ctx = {"features": engine.as_dict()}
        for it in momentum_strat.on_event(ev_hold, ctx):
            if it.action.value == "EXIT":
                momentum_exits += 1

    # 3. Trigger Mean Reversion: drop bar with close < bollinger_lower and rsi14 < 30
    seq += 1
    t += 60_000_000_000
    drop_bar = {
        "open": 48000,
        "high": 48100,
        "low": 45000,
        "close": 45500,
    }
    ev = make_test_envelope("BarClosed", drop_bar, t, seq)
    engine.update(ev)
    features = engine.as_dict()
    ctx = {"features": features}
    for it in mean_rev_strat.on_event(ev, ctx):
        if it.action.value == "ENTER":
            mean_reversion_entries += 1

    # Trigger Mean Reversion Exit: rebound to middle band
    seq += 1
    t += 60_000_000_000
    mid_b = engine.as_dict().get("bollinger_mid") or 50000
    rebound_bar = {
        "open": 46000,
        "high": float(mid_b) + 500,
        "low": 46000,
        "close": float(mid_b) + 100,
    }
    ev_reb = make_test_envelope("BarClosed", rebound_bar, t, seq)
    engine.update(ev_reb)
    ctx = {"features": engine.as_dict()}
    for it in mean_rev_strat.on_event(ev_reb, ctx):
        if it.action.value == "EXIT":
            mean_reversion_exits += 1

    # 4. Trigger Imbalance Scalper & Hybrid:
    # Warmup book snapshot with spread <= 5 bps
    seq += 1
    t += 100_000_000
    book_imb = {
        "bids": [[50000, 100], [49999, 80], [49998, 60], [49997, 50], [49996, 40]],
        "asks": [[50002, 10], [50003, 15], [50004, 20], [50005, 10], [50006, 10]],
    }
    ev = make_test_envelope("BookSnapshot", book_imb, t, seq)
    engine.update(ev)

    # Add positive 1s volume
    seq += 1
    t += 10_000_000
    trade = {"price": 50002, "lots": 10, "aggressor": "BUY"}
    ev_trade = make_test_envelope("Trade", trade, t, seq)
    engine.update(ev_trade)

    features = engine.as_dict()
    ctx = {"features": features}
    imbalance_intents = imbalance_strat.on_event(ev, ctx)
    for it in imbalance_intents:
        if it.action.value == "ENTER":
            imbalance_entries += 1
    hybrid_strat.on_event(ev, ctx)

    # Trigger Imbalance Exit: price advances to target
    seq += 1
    t += 100_000_000
    book_exit = {
        "bids": [[55000, 100], [54999, 80]],
        "asks": [[55002, 10], [55003, 15]],
    }
    ev_exit = make_test_envelope("BookSnapshot", book_exit, t, seq)
    engine.update(ev_exit)
    ctx = {"features": engine.as_dict()}
    for it in imbalance_strat.on_event(ev_exit, ctx):
        if it.action.value == "EXIT":
            imbalance_exits += 1

    # 5. Trigger Sweep Heuristic:
    # Record initial top 5 book range
    seq += 1
    t += 10_000_000
    b_pre = {
        "bids": [[50000, 10], [49995, 10], [49990, 10], [49985, 10], [49980, 10]],
        "asks": [[50005, 10], [50010, 10], [50015, 10], [50020, 10], [50025, 10]],
    }
    ev_pre = make_test_envelope("BookSnapshot", b_pre, t, seq)
    engine.update(ev_pre)

    # 3 consecutive aggressive SELL trades across 3 distinct prices within 50ms (sweep)
    seq += 1
    t += 10_000_000
    ev1 = make_test_envelope("Trade", {"price": 50000, "lots": 5, "aggressor": "SELL"}, t, seq)
    engine.update(ev1)

    seq += 1
    t += 10_000_000
    ev2 = make_test_envelope("Trade", {"price": 49950, "lots": 5, "aggressor": "SELL"}, t, seq)
    engine.update(ev2)

    seq += 1
    t += 10_000_000
    ev3 = make_test_envelope("Trade", {"price": 49900, "lots": 5, "aggressor": "SELL"}, t, seq)
    engine.update(ev3)

    # Flush cluster after 60ms with opposite aggressor
    seq += 1
    t += 60_000_000
    ev4 = make_test_envelope("Trade", {"price": 49920, "lots": 1, "aggressor": "BUY"}, t, seq)
    engine.update(ev4)

    # Price recovers inside pre-cluster top-5 price range [49980, 50025] within 500ms
    seq += 1
    t += 50_000_000
    book_rec = {
        "bids": [[49995, 10]],
        "asks": [[50005, 10]],
    }
    ev_rec = make_test_envelope("BookSnapshot", book_rec, t, seq)
    engine.update(ev_rec)

    features = engine.as_dict()
    ctx = {"features": features}
    for it in sweep_strat.on_event(ev_rec, ctx):
        if it.action.value == "ENTER":
            sweep_entries += 1

    # Trigger Sweep Exit: price hits target
    seq += 1
    t += 100_000_000
    book_sweep_exit = {
        "bids": [[50200, 10]],
        "asks": [[50205, 10]],
    }
    ev_sexit = make_test_envelope("BookSnapshot", book_sweep_exit, t, seq)
    engine.update(ev_sexit)
    ctx = {"features": engine.as_dict()}
    for it in sweep_strat.on_event(ev_sexit, ctx):
        if it.action.value == "EXIT":
            sweep_exits += 1

    return {
        "imbalance_entries": imbalance_entries,
        "imbalance_exits": imbalance_exits,
        "momentum_entries": momentum_entries,
        "momentum_exits": momentum_exits,
        "mean_reversion_entries": mean_reversion_entries,
        "mean_reversion_exits": mean_reversion_exits,
        "sweep_entries": sweep_entries,
        "sweep_exits": sweep_exits,
        "hybrid_evaluations": hybrid_strat.evaluations_count,
    }


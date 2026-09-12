import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quantdesk.core.events import canonical_bytes
from tests.support.engine_case import incoming, make_engine


@given(st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=20))
def test_receipt_order_is_never_resorted_by_exchange_time(prices):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        engine, db, journal = make_engine(Path(directory))
        try:
            for number, price in enumerate(prices, 1):
                engine.commit(engine.process(incoming(number, price=price)))
            records = engine.store.read_after(0)
            assert [r.envelope.engine_seq for r in records] == list(range(1, 3 * len(prices) + 1))
            assert [
                json.loads(r.envelope.payload)["price_ticks"]
                for r in records
                if r.origin == "INPUT"
            ] == prices
            assert len({r.envelope.event_id for r in records}) == len(records)
            for offset in range(0, len(records), 3):
                parent, a, z = records[offset : offset + 3]
                assert a.parent_id == z.parent_id == parent.envelope.event_id
                assert a.envelope.source_channel == "a"
                assert z.envelope.source_channel == "z"
                assert (
                    a.envelope.available_ns
                    == z.envelope.available_ns
                    == parent.envelope.available_ns
                )
        finally:
            db.close()
            journal.close()


def test_timer_consumption_is_an_input_and_checkpoint_safe(tmp_path):
    from quantdesk.core.checkpoint import Checkpoint

    engine, db, journal = make_engine(tmp_path)
    try:
        engine.commit(engine.process(incoming(1)))
        timer = engine.state.timers[0]
        assert timer.due_ns == 150
        assert timer.scheduled_by_event_id == engine.store.read_after(0)[0].envelope.event_id
        assert Checkpoint.from_bytes(engine.checkpoint().to_bytes()).state.timers == (timer,)
        firing = engine.next_timer_input(175)
        bad = replace(
            firing, payload=canonical_bytes({**json.loads(firing.payload), "due_ns": 999})
        )
        with pytest.raises(ValueError, match="timer"):
            engine.process(bad)
        engine.commit(engine.process(firing))
        assert engine.state.timers == ()
        assert engine.store.read_after(3)[0].origin == "INPUT"
        assert (
            json.loads(engine.store.read_after(3)[0].envelope.payload)["actual_available_ns"] == 175
        )
        with pytest.raises(ValueError, match="timer"):
            engine.process(firing)
    finally:
        db.close()
        journal.close()


def test_scheduler_equal_time_ranks_and_causal_order_survive_serialization():
    from quantdesk.core.clock import DeterministicScheduler, SourceRank

    scheduler = DeterministicScheduler()
    scheduler.schedule(incoming(1, available_ns=100), SourceRank.STRATEGY_TIMER, 1)
    scheduler.schedule(incoming(2, available_ns=100), SourceRank.CANCEL_ARRIVAL, 1)
    scheduler.schedule(incoming(3, available_ns=100), SourceRank.MARKET_EXECUTION, 1)
    scheduler.schedule(incoming(4, available_ns=100), SourceRank.MARKET_ACCOUNT, 1)
    scheduler = DeterministicScheduler.from_bytes(scheduler.to_bytes())
    assert [scheduler.pop().source_message_id for _ in range(4)] == ["4", "3", "2", "1"]
    with pytest.raises(ValueError, match="cause"):
        scheduler.schedule(
            incoming(5, available_ns=100), SourceRank.ORDER_ARRIVAL, 1, caused_at_ns=101
        )


def test_clock_wall_regression_and_new_boot_never_reverse_availability():
    from quantdesk.core.clock import AvailabilityClock

    clock = AvailabilityClock.anchor(wall_ns=1000, monotonic_ns=100, boot_epoch="a")
    assert clock.observe(wall_ns=800, monotonic_ns=150, boot_epoch="a") == 1050
    clock = AvailabilityClock.from_bytes(clock.to_bytes())
    clock.reanchor(wall_ns=900, monotonic_ns=1, boot_epoch="b")
    assert clock.observe(wall_ns=901, monotonic_ns=2, boot_epoch="b") == 1051
    assert clock.adjustments[-1].next_available_ns == 1050
    with pytest.raises(ValueError, match="epoch"):
        clock.observe(wall_ns=902, monotonic_ns=3, boot_epoch="c")


def test_single_writer_enforces_thread_scope_and_unsequenced_inputs(tmp_path):
    engine, db, journal = make_engine(tmp_path)
    try:
        with ThreadPoolExecutor(1) as workers, pytest.raises(RuntimeError, match="writer"):
            workers.submit(engine.process, incoming(1)).result()
        candidate = engine.process(incoming(1))
        engine.commit(candidate)
        with pytest.raises(TypeError, match="IncomingEvent"):
            engine.process(candidate.events[0].envelope)
        with pytest.raises(ValueError, match="run"):
            engine.process(replace(incoming(2), run_id="different"))
        with pytest.raises(ValueError, match="availability"):
            engine.process(replace(incoming(2), available_ns=1))
        with pytest.raises(ValueError, match="account"):
            engine.process(replace(incoming(2), account_id="another"))
    finally:
        db.close()
        journal.close()


def test_rng_streams_do_not_share_consumption_and_snapshot_continues():
    from quantdesk.core.checkpoint import EngineState, RNGStream

    state = replace(
        EngineState.initial("run"),
        rng_streams=(RNGStream("latency", "42"), RNGStream("metrics", "42")),
    )
    baseline, _ = state.next_random("latency", 1000000)
    _, state = state.next_random("metrics", 1000000)
    actual, state = state.next_random("latency", 1000000)
    assert actual == baseline
    restored = EngineState.from_bytes(state.to_bytes())
    assert restored.next_random("latency", 1000000) == state.next_random("latency", 1000000)

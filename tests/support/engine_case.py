"""Small executable event programs for the engine boundary, not OMS/strategy mocks."""

from __future__ import annotations

import json
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from quantdesk.core.events import IncomingEvent, canonical_bytes


def incoming(ordinal: int, *, price: int = 100, available_ns: int | None = None):
    return IncomingEvent(
        "Trade",
        1,
        "deterministic-run",
        "demo",
        "fixture",
        "DEMO",
        "BTCUSDT",
        "trades",
        "connection-1",
        str(ordinal),
        str(ordinal),
        1000 - ordinal,
        None,
        ordinal * 100,
        ordinal * 10,
        available_ns if available_ns is not None else ordinal * 100,
        None,
        f"receipt-{ordinal}",
        None,
        "fixture-v1",
        canonical_bytes(
            {
                "native_trade_id": str(ordinal),
                "price_ticks": price,
                "size_lots": 1,
                "aggressor_side": "BUY",
                "venue_extensions": (),
            }
        ),
    )


def program(*, reverse: bool = False, fail_decisions: bool = False):
    from quantdesk.core.clock import TimerRequest
    from quantdesk.core.reducers import DecisionProducer, EventDraft, FactReducer, Reduction, Stage

    def apply(event, state):
        payload = json.loads(event.payload)
        if event.event_type == "Trade":
            values = json.loads(state.get("features", "prices", b"[]"))
            values.append(payload["price_ticks"])
            changed = state.put("features", "prices", canonical_bytes(values[-4:]))
            return Reduction(changed, timers=(TimerRequest(50),))
        if event.event_type == "IntentRejected":
            ids = json.loads(state.get("strategies", "decisions", b"[]"))
            ids.append(payload["intent_id"])
            return Reduction(state.put("strategies", "decisions", canonical_bytes(ids)))
        return Reduction(state)

    def decide(event, state):
        if fail_decisions:
            raise AssertionError("recovery reran a decision producer")
        if event.event_type not in {"Trade", "TimerFired"}:
            return ()
        values = json.loads(state.get("features", "prices", b"[]"))
        return (
            EventDraft(
                "IntentRejected",
                canonical_bytes(
                    {
                        "intent_id": event.event_id,
                        "reason_code": "RESEARCH_ONLY",
                        "message": str(sum(values)),
                    }
                ),
            ),
        )

    reducers = (FactReducer("prices", Stage.FEATURES, apply),)
    producers = tuple(DecisionProducer(name, Stage.STRATEGIES, decide) for name in ("z", "a"))
    return reducers, tuple(reversed(producers)) if reverse else producers


def make_engine(
    directory: Path,
    *,
    reverse=False,
    fail_decisions=False,
    mode=None,
    connection_factory=None,
    state=None,
):
    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.persistence.db import Database
    from quantdesk.persistence.event_store import EventStore
    from quantdesk.persistence.raw_journal import RawJournal

    journal = RawJournal(directory / "raw")
    db = Database(
        directory / "engine.sqlite",
        **({"connection_factory": connection_factory} if connection_factory else {}),
    )
    reducers, producers = program(reverse=reverse, fail_decisions=fail_decisions)
    engine = Engine(
        "deterministic-run",
        EventStore(db),
        raw_watermark=lambda: journal.durable_watermark,
        reducers=reducers,
        producers=producers,
        code_hash="fixture-code-v1",
        schema_hash="fixture-schema-v1",
        mode=mode or EngineMode.DEMO,
        initial_state=state,
    )
    return engine, db, journal


def drive(engine, start=1, stop=8):
    for number in range(start, stop):
        engine.commit(engine.process(incoming(number, price=100 + number)))
        while engine.state.timers:
            engine.commit(engine.process(engine.next_timer_input()))


def process_report():
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="quantdesk-replay-") as temporary:
        engine, db, journal = make_engine(Path(temporary), reverse=True)
        try:
            drive(engine)
            return {
                "state": engine.hashes().deterministic_state_hash,
                "derived": engine.derived_history_hash,
            }
        finally:
            db.close()
            journal.close()


@contextmanager
def observe_network_boundary():
    """Any attempted network connection is forbidden and counted at the OS boundary."""
    attempts = []

    def reject_connection(*args, **kwargs):
        attempts.append("network connection attempted")
        raise AssertionError("offline replay attempted network I/O")

    with (
        patch("socket.socket.connect", reject_connection),
        patch("socket.create_connection", reject_connection),
    ):
        yield attempts


def deterministic_replay_case(**overrides):
    from tempfile import TemporaryDirectory

    from quantdesk.core.engine import EngineMode, Replay

    with (
        TemporaryDirectory(prefix="quantdesk-restart-") as temporary,
        observe_network_boundary() as calls,
    ):
        path = Path(temporary)
        engine, db, journal = make_engine(path)
        try:
            genesis = engine.checkpoint()
            checkpoints = []
            restarts = overrides.get("restarts", [3, 9, 17])
            for number in range(1, 21):
                drive(engine, number, number + 1)
                if number in restarts:
                    checkpoints.append(engine.checkpoint())
            expected = engine.hashes()
            manifest = engine.replay_manifest(genesis)
            verified = Replay(*program()).verify(manifest)
            history = engine.store.read_after(0)
            for checkpoint in checkpoints:
                recovering = type(engine)(
                    "deterministic-run",
                    engine.store,
                    raw_watermark=lambda: journal.durable_watermark,
                    reducers=program(fail_decisions=True)[0],
                    producers=program(fail_decisions=True)[1],
                    mode=EngineMode.RECOVERY,
                    code_hash="fixture-code-v1",
                    schema_hash="fixture-schema-v1",
                )
                recovering.restore(checkpoint, engine.store.read_after(checkpoint.engine_seq))
                assert recovering.hashes() == expected
                try:
                    recovering.require_network_dispatch()
                except PermissionError:
                    pass
                else:
                    raise AssertionError("recovery allowed order dispatch")
            return {
                "continuous_state_hash": expected.deterministic_state_hash,
                "restarted_state_hash": recovering.hashes().deterministic_state_hash,
                "recorded_derived_hash": engine.derived_history_hash,
                "regenerated_derived_hash": verified.regenerated_derived_hash,
                "recovery_network_order_calls": len(calls),
                "backward_engine_sequences": sum(
                    b.envelope.engine_seq <= a.envelope.engine_seq for a, b in pairwise(history)
                ),
            }
        finally:
            db.close()
            journal.close()

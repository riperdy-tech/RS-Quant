import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, replace

import pytest

from quantdesk.core.events import canonical_bytes
from quantdesk.persistence.outbox import Outbox
from tests.support.engine_case import drive, incoming, make_engine, program


def test_replay_and_restart_preserve_derived_history(case):
    r = case("deterministic_replay", restarts=[3, 9, 17])
    assert r["continuous_state_hash"] == r["restarted_state_hash"]
    assert r["recorded_derived_hash"] == r["regenerated_derived_hash"]
    assert r["recovery_network_order_calls"] == 0
    assert r["backward_engine_sequences"] == 0


def test_process_is_immutable_and_publish_waits_for_sqlite_commit(tmp_path):
    engine, db, journal = make_engine(tmp_path)
    try:
        before = engine.state
        candidate = engine.process(incoming(1))
        assert engine.state == before
        assert engine.store.read_after(0) == ()
        assert Outbox(db.path).pending() == ()
        assert json.loads(candidate.candidate_state.get("features", "prices")) == [100]
        with pytest.raises(FrozenInstanceError):
            candidate.base_state_version = 9
        with pytest.raises(FrozenInstanceError):
            candidate.candidate_state.engine_seq = 9
        receipt = engine.commit(candidate)
        assert engine.state is candidate.candidate_state
        assert receipt.first_seq == 1 and receipt.last_seq == 3 and receipt.state_version == 1
        assert [r.origin for r in engine.store.read_after(0)] == ["INPUT", "DERIVED", "DERIVED"]
    finally:
        db.close()
        journal.close()


def test_failed_commit_discards_candidate_and_latches_engine(tmp_path):
    class BrokenCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("injected disk failure")

    engine, db, journal = make_engine(tmp_path, connection_factory=BrokenCommit)
    try:
        before = engine.state
        candidate = engine.process(incoming(1))
        with pytest.raises(sqlite3.OperationalError):
            engine.commit(candidate)
        assert engine.state == before
        assert engine.trading_latched_off
        assert engine.store.read_after(0) == ()
        assert Outbox(db.path).pending() == ()
        with pytest.raises(RuntimeError, match="latched"):
            engine.process(incoming(2))
        with pytest.raises(PermissionError):
            engine.require_network_dispatch()
    finally:
        db.close()
        journal.close()


def test_candidate_cannot_be_forged_reused_or_committed_after_discard(tmp_path):
    engine, db, journal = make_engine(tmp_path)
    try:
        candidate = engine.process(incoming(1))
        with pytest.raises(ValueError, match="candidate"):
            engine.commit(replace(candidate, projection_updates=()))
        with pytest.raises(RuntimeError, match="pending"):
            engine.process(incoming(2))
        engine.discard(candidate)
        with pytest.raises(ValueError, match="candidate"):
            engine.commit(candidate)
        fresh = engine.process(incoming(1))
        assert fresh.events == candidate.events
        engine.commit(fresh)
        with pytest.raises(ValueError, match="candidate"):
            engine.commit(fresh)
    finally:
        db.close()
        journal.close()


def test_independent_processes_ignore_hash_seed_and_wall_clock():
    script = (
        "import json; from tests.support.engine_case import process_report; "
        "print(json.dumps(process_report(),sort_keys=True))"
    )
    outputs = []
    for seed in ("1", "918273"):
        outputs.append(
            subprocess.run(
                [sys.executable, "-c", script],
                env={**os.environ, "PYTHONHASHSEED": seed},
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    assert outputs[0] == outputs[1]


def test_slow_fast_replay_and_future_mutation_have_identical_prefixes(tmp_path):
    engines = [make_engine(tmp_path / str(i), reverse=bool(i)) for i in range(3)]
    try:
        for number in range(1, 9):
            for index, (engine, _, _) in enumerate(engines):
                if index == 1:
                    time.sleep(0.001)
                price = 999 if index == 2 and number > 4 else 100 + number
                engine.commit(engine.process(incoming(number, price=price)))
            if number <= 4:
                assert len({item[0].hashes().deterministic_state_hash for item in engines}) == 1
        assert engines[0][0].hashes() == engines[1][0].hashes()
        assert engines[0][0].hashes() != engines[2][0].hashes()
    finally:
        for _, db, journal in engines:
            db.close()
            journal.close()


def test_checkpoint_roundtrip_preserves_every_causal_section_and_exact_rng(tmp_path):
    from quantdesk.core.checkpoint import Checkpoint, EngineState, RNGStream

    state = EngineState.initial("deterministic-run")
    for section in state.component_names():
        state = state.put(section, "value", canonical_bytes({"exact": "0.00100", "window": [1, 2]}))
    state = replace(
        state,
        config_hash="config-v2",
        model_hashes=(("a", "model-2"),),
        ownership_epoch=71,
        risk_epoch=27,
        rng_streams=(RNGStream("latency", "7", 8),),
    )
    engine, db, journal = make_engine(tmp_path, state=state)
    try:
        drive(engine, 1, 2)
        checkpoint = engine.checkpoint()
        decoded = Checkpoint.from_bytes(checkpoint.to_bytes())
        assert decoded == checkpoint
        assert decoded.state == engine.state
        assert decoded.state.next_random("latency", 1000) == engine.state.next_random(
            "latency", 1000
        )
        damaged = replace(checkpoint, snapshot_hash="0" * 64)
        with pytest.raises(ValueError, match="hash"):
            damaged.verify("fixture-code-v1", "fixture-schema-v1")
        with pytest.raises(ValueError, match="compatib"):
            checkpoint.verify("different-code", "fixture-schema-v1")
    finally:
        db.close()
        journal.close()


def test_forensic_verification_detects_changed_derived_history_and_final_state(tmp_path):
    from quantdesk.core.engine import Replay

    engine, db, journal = make_engine(tmp_path)
    try:
        genesis = engine.checkpoint()
        drive(engine)
        manifest = engine.replay_manifest(genesis)
        replay = Replay(*program())
        assert replay.verify(manifest).matched
        records = list(manifest.events)
        records[1] = replace(records[1], envelope=replace(records[1].envelope, payload=b"{}"))
        corrupt = replace(manifest, events=tuple(records))
        assert not replay.verify(corrupt).matched
        wrong_state = replace(
            manifest,
            expected_hashes=replace(manifest.expected_hashes, deterministic_state_hash="0" * 64),
        )
        assert not replay.verify(wrong_state).matched
    finally:
        db.close()
        journal.close()


def test_recovery_rejects_missing_tampered_or_noncontiguous_tail(tmp_path):
    from quantdesk.core.engine import EngineMode

    engine, db, journal = make_engine(tmp_path)
    try:
        checkpoint = engine.checkpoint()
        drive(engine)
        tail = engine.store.read_after(0)
        recovery = type(engine)(
            "deterministic-run",
            engine.store,
            raw_watermark=lambda: journal.durable_watermark,
            reducers=program()[0],
            producers=program(fail_decisions=True)[1],
            mode=EngineMode.RECOVERY,
            code_hash="fixture-code-v1",
            schema_hash="fixture-schema-v1",
        )
        for bad in (tail[1:], tail[:-1], tuple(reversed(tail))):
            before = recovery.state
            with pytest.raises(ValueError):
                recovery.restore(checkpoint, bad)
            assert recovery.state == before
        recovery.restore(checkpoint, tail)
        assert recovery.hashes() == engine.hashes()
    finally:
        db.close()
        journal.close()


def test_counterfactual_branch_requires_fresh_run_and_rejects_historical_fills(tmp_path):
    from quantdesk.core.engine import Replay

    engine, db, journal = make_engine(tmp_path)
    try:
        genesis = engine.checkpoint()
        drive(engine)
        replay = Replay(*program())
        manifest = engine.replay_manifest(genesis)
        branch = replay.branch(manifest, logical_run_id="branch-run")
        assert branch.operation == "COUNTERFACTUAL"
        assert branch.logical_run_id == "branch-run"
        assert not branch.network_order_dispatch_allowed
        assert branch.deterministic_state_hash != engine.hashes().deterministic_state_hash
        with pytest.raises(ValueError, match="fresh"):
            replay.branch(manifest, logical_run_id="deterministic-run")
        bad = replace(incoming(1), event_type="ExecutionReport")
        with pytest.raises(ValueError, match="account"):
            replay.branch(manifest, logical_run_id="branch-run", inputs=(bad,))
    finally:
        db.close()
        journal.close()


def test_hash_scopes_separate_incidents_economics_and_execution_attempts(tmp_path):
    from quantdesk.core.checkpoint import EngineState

    state = EngineState.initial("deterministic-run")
    assert (
        state.hashes().economic_state_hash
        == replace(state, risk_epoch=7).hashes().economic_state_hash
    )
    assert (
        state.hashes().deterministic_state_hash
        != replace(state, risk_epoch=7).hashes().deterministic_state_hash
    )
    assert (
        state.hashes().economic_state_hash
        != state.put("ledger", "cash", b'"100"').hashes().economic_state_hash
    )
    engine, db, journal = make_engine(tmp_path)
    try:
        before = engine.hashes()
        incident = replace(
            incoming(1),
            event_type="RecorderHealthChanged",
            payload=canonical_bytes(
                {"healthy": False, "durable_watermark": None, "reason": "disk incident"}
            ),
        )
        engine.commit(engine.process(incident))
        after = engine.hashes()
        assert after.audit_hash != before.audit_hash
        assert after.operational_hash != before.operational_hash
        assert after.economic_state_hash == before.economic_state_hash
    finally:
        db.close()
        journal.close()


def test_restart_continues_same_history_with_pending_timers(tmp_path):
    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.persistence.db import Database
    from quantdesk.persistence.event_store import EventStore
    from quantdesk.persistence.raw_journal import RawJournal

    reference, ref_db, ref_journal = make_engine(tmp_path / "reference")
    restarted, db, journal = make_engine(tmp_path / "restart")
    try:
        reference.commit(reference.process(incoming(1)))
        restarted.commit(restarted.process(incoming(1)))
        checkpoint = restarted.checkpoint()
        db.close()
        journal.close()
        db = Database(tmp_path / "restart" / "engine.sqlite")
        journal = RawJournal(tmp_path / "restart" / "raw")
        restarted = Engine(
            "deterministic-run",
            EventStore(db),
            raw_watermark=lambda: journal.durable_watermark,
            reducers=program()[0],
            producers=program()[1],
            code_hash="fixture-code-v1",
            schema_hash="fixture-schema-v1",
            mode=EngineMode.RECOVERY,
        )
        restarted.restore(checkpoint, ())
        restarted.resume_offline()
        for engine in (reference, restarted):
            engine.commit(engine.process(engine.next_timer_input()))
            drive(engine, 2, 7)
        assert restarted.hashes() == reference.hashes()
        assert restarted.store.read_after(0) == reference.store.read_after(0)
    finally:
        db.close()
        journal.close()
        ref_db.close()
        ref_journal.close()


@pytest.mark.parametrize("fail_commit", [False, True])
def test_real_ledger_outbox_and_projection_publish_atomically(tmp_path, fail_commit):
    from decimal import Decimal

    from quantdesk.core.engine import Engine
    from quantdesk.core.reducers import FactReducer, Reduction, Stage
    from quantdesk.persistence.db import Database
    from quantdesk.persistence.event_store import (
        EconomicIdentity,
        EventStore,
        LedgerPosting,
        LedgerTransaction,
        OutboxInstruction,
        ProjectionUpdate,
    )
    from quantdesk.persistence.raw_journal import RawFrame, RawJournal

    observations = []
    path = tmp_path / "engine.sqlite"

    class CommitBoundary(sqlite3.Connection):
        def commit(self):
            observations.append((engine.state.engine_seq, len(Outbox(path).pending())))
            if fail_commit:
                raise sqlite3.OperationalError("disk failure")
            super().commit()

    def apply(event, state):
        transaction = LedgerTransaction(
            "tx-1",
            event.event_id,
            EconomicIdentity("fixture", "DEMO", "demo", "cash", "native-1", "transfer"),
            (
                LedgerPosting("cash:USDT", "USDT", Decimal("1.000000000000000000001")),
                LedgerPosting("equity:external", "USDT", Decimal("-1.000000000000000000001")),
            ),
        )
        instruction = OutboxInstruction(
            "order-1", "demo-1", b"{}", "risk-1", "epoch-1", event.engine_seq, 2000
        )
        changed = state.put("ledger", "cash", b'"1.000000000000000000001"')
        return Reduction(
            changed,
            (transaction,),
            (instruction,),
            (ProjectionUpdate("balances", "cash:USDT", changed.get("ledger", "cash")),),
        )

    with (
        RawJournal(tmp_path / "raw") as journal,
        Database(path, connection_factory=CommitBoundary) as db,
    ):
        engine = Engine(
            "deterministic-run",
            EventStore(db),
            raw_watermark=lambda: journal.durable_watermark,
            reducers=(FactReducer("cash", Stage.BOOK_ACCOUNT_OMS, apply),),
            code_hash="fixture-code-v1",
            schema_hash="fixture-schema-v1",
        )
        event = incoming(1)
        ref = journal.append(
            RawFrame(event.payload, "fixture", "DEMO", "websocket", 100, 10, "e", 1)
        )
        journal.sync()
        candidate = engine.process(replace(event, raw_ref=str(ref)))
        if fail_commit:
            with pytest.raises(sqlite3.OperationalError):
                engine.commit(candidate)
            assert engine.state.engine_seq == 0
            assert engine.store.postings("tx-1") == ()
            assert engine.store.projection("balances", "cash:USDT") is None
            assert Outbox(path).pending() == ()
        else:
            receipt = engine.commit(candidate)
            assert receipt.raw_watermark.covers(ref)
            assert len(Outbox(path).pending()) == 1
            assert engine.store.postings("tx-1")[0].amount == Decimal("1.000000000000000000001")
            assert (
                engine.store.projection("balances", "cash:USDT")[0] == b'"1.000000000000000000001"'
            )
        assert observations == [(0, 0)]


@pytest.mark.parametrize(
    "changes",
    [
        {"model_hashes": (["strategy", "model"],)},
        {"account_scope": ["venue", "DEMO", "demo"]},
    ],
)
def test_causal_state_rejects_nested_mutable_collections(changes):
    from quantdesk.core.checkpoint import EngineState

    with pytest.raises(ValueError, match="immutable"):
        replace(EngineState.initial("run"), **changes)


def test_unknown_or_malformed_payload_cannot_enter_candidate(tmp_path):
    engine, db, journal = make_engine(tmp_path)
    try:
        with pytest.raises(ValueError, match="payload"):
            engine.process(replace(incoming(1), payload=b"{}"))
        assert engine.state.engine_seq == 0
    finally:
        db.close()
        journal.close()


def test_activation_latch_and_clock_facts_update_checkpoint_and_recovery(tmp_path):
    from quantdesk.core.engine import Engine, EngineMode

    engine, db, journal = make_engine(tmp_path)
    try:
        checkpoint = engine.checkpoint()
        facts = (
            (
                "ConfigActivated",
                {
                    "prior_config_hash": "unconfigured",
                    "config_hash": "config-2",
                    "activation_seq": 1,
                },
            ),
            (
                "ModelActivated",
                {
                    "strategy_id": "a",
                    "prior_model_hash": None,
                    "model_hash": "model-2",
                    "activation_seq": 2,
                },
            ),
            (
                "RiskLatchChanged",
                {
                    "latch_id": "kill",
                    "scope": "account",
                    "active": True,
                    "reason": "operator halt",
                    "risk_version": "risk-2",
                },
            ),
            (
                "ClockAdjusted",
                {
                    "prior_available_ns": 300,
                    "next_available_ns": 400,
                    "reason": "ANCHOR:boot-2:400:1",
                },
            ),
        )
        for number, (kind, payload) in enumerate(facts, 1):
            engine.commit(
                engine.process(
                    replace(incoming(number), event_type=kind, payload=canonical_bytes(payload))
                )
            )
        assert engine.state.config_hash == "config-2"
        assert engine.state.model_hashes == (("a", "model-2"),)
        assert engine.state.risk_epoch == 1
        assert json.loads(engine.state.get("risk_latches", "kill"))["active"] is True
        assert engine.state.clock_anchors
        recovery = Engine(
            "deterministic-run",
            engine.store,
            raw_watermark=lambda: journal.durable_watermark,
            reducers=program()[0],
            producers=program(fail_decisions=True)[1],
            code_hash="fixture-code-v1",
            schema_hash="fixture-schema-v1",
            mode=EngineMode.RECOVERY,
        )
        recovery.restore(checkpoint, engine.store.read_after(0))
        assert recovery.state == engine.state
    finally:
        db.close()
        journal.close()


def test_reentrant_processing_cannot_commit_inside_an_uncommitted_reducer(tmp_path):
    from quantdesk.core.engine import Engine
    from quantdesk.core.reducers import FactReducer, Reduction, Stage

    original, db, journal = make_engine(tmp_path)
    attempted = []

    def apply(event, state):
        if not attempted:
            attempted.append(True)
            with pytest.raises(RuntimeError, match="processing"):
                engine.process(incoming(2))
        return Reduction(state)

    try:
        engine = Engine(
            "deterministic-run",
            original.store,
            raw_watermark=lambda: journal.durable_watermark,
            reducers=(FactReducer("reentrancy", Stage.VALIDATE, apply),),
            code_hash="fixture-code-v1",
            schema_hash="fixture-schema-v1",
        )
        engine.commit(engine.process(incoming(1)))
        assert engine.state.input_ordinal == 1
        assert len(engine.store.read_after(0)) == 1
    finally:
        db.close()
        journal.close()


def test_mutable_nested_effects_cannot_escape_into_a_candidate(tmp_path):
    from quantdesk.core.engine import Engine
    from quantdesk.core.reducers import FactReducer, Reduction, Stage
    from quantdesk.persistence.event_store import ProjectionUpdate

    original, db, journal = make_engine(tmp_path)

    def apply(event, state):
        return Reduction(
            state, projection_updates=(ProjectionUpdate("balances", "cash", bytearray(b'"1"')),)
        )

    try:
        engine = Engine(
            "deterministic-run",
            original.store,
            raw_watermark=lambda: journal.durable_watermark,
            reducers=(FactReducer("mutation", Stage.BOOK_ACCOUNT_OMS, apply),),
            code_hash="fixture-code-v1",
            schema_hash="fixture-schema-v1",
        )
        with pytest.raises(TypeError, match="immutable"):
            engine.process(incoming(1))
        assert engine.state.engine_seq == 0
        assert engine.store.read_after(0) == ()
    finally:
        db.close()
        journal.close()


def test_recovery_requires_checkpoint_raw_journal_even_without_raw_references(tmp_path):
    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.persistence.raw_journal import RawJournal

    engine, db, journal = make_engine(tmp_path)
    try:
        checkpoint = engine.checkpoint()
        drive(engine, 1, 3)
        with RawJournal(tmp_path / "wrong-raw") as wrong:
            recovery = Engine(
                "deterministic-run",
                engine.store,
                raw_watermark=lambda: wrong.durable_watermark,
                reducers=program()[0],
                producers=program()[1],
                code_hash="fixture-code-v1",
                schema_hash="fixture-schema-v1",
                mode=EngineMode.RECOVERY,
            )
            with pytest.raises(ValueError, match="watermark"):
                recovery.restore(checkpoint, engine.store.read_after(0))
    finally:
        db.close()
        journal.close()

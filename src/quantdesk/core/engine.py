"""Single-writer candidate/commit processing and three separate replay operations.

All reducers apply each fact in (stage, id) order before decision producers run.
Emissions are drained breadth-first in (stage, producer id, emission ordinal)
order. One external input and its complete causal closure form one transaction.
No transport is attached to this engine: committed outbox work still requires the
separate current-risk/ownership gateway authorization implemented by the router.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from hashlib import sha256
from threading import get_ident

from quantdesk.core.checkpoint import Checkpoint, EngineState, HashScopes, SourceCursor
from quantdesk.core.clock import DeterministicScheduler, ScheduledTimer
from quantdesk.core.events import (
    PAYLOAD_TYPES,
    Envelope,
    IncomingEvent,
    TimerFired,
    canonical_bytes,
)
from quantdesk.core.ids import derive_id
from quantdesk.core.reducers import (
    DecisionProducer,
    FactReducer,
    Reduction,
    apply_engine_fact,
    require_immutable,
)
from quantdesk.persistence.event_store import (
    CommitReceipt,
    EventRecord,
    EventStore,
    LedgerTransaction,
    OutboxInstruction,
    PersistenceTransition,
    ProjectionUpdate,
)
from quantdesk.persistence.manifests import DurableWatermark, RawRef

_OPERATIONAL = frozenset(
    {
        "ConnectionChanged",
        "DataGap",
        "ClockAdjusted",
        "RecorderHealthChanged",
        "OperatorCommand",
        "CommandResult",
        "RiskLatchChanged",
        "ConfigActivated",
        "ModelActivated",
        "CheckpointWritten",
        "RunBoundary",
        "ReconciliationObservation",
        "PositionDiscrepancy",
    }
)
_BRANCH_MARKET = frozenset(
    {
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
        "RunBoundary",
    }
)
_RESERVED = (
    "logical_run_id",
    "account_scope",
    "engine_seq",
    "state_version",
    "input_ordinal",
    "last_input_id",
    "available_ns",
    "next_scheduled_ordinal",
    "timers",
    "source_cursors",
    "audit_hash",
    "operational_hash",
    "derived_history_hash",
)


class EngineMode(StrEnum):
    DEMO = "DEMO"
    RECOVERY = "RECOVERY"
    VERIFY = "VERIFY"
    COUNTERFACTUAL = "COUNTERFACTUAL"


def _chain(previous: str, value: object) -> str:
    return sha256(bytes.fromhex(previous) + canonical_bytes(value)).hexdigest()


def _incoming(envelope: Envelope) -> IncomingEvent:
    return IncomingEvent(
        **{field.name: getattr(envelope, field.name) for field in fields(IncomingEvent)}
    )


def _require_watermark(current: DurableWatermark, prior: DurableWatermark) -> None:
    if (
        current.journal_id != prior.journal_id
        or current.frame_ordinal < prior.frame_ordinal
        or current.chunk_index < prior.chunk_index
        or (current.chunk_index == prior.chunk_index and current.end_offset < prior.end_offset)
    ):
        raise ValueError("raw durability watermark regressed or changed journal")


@dataclass(frozen=True, slots=True)
class Transition:
    input_event_id: str
    base_state_version: int
    events: tuple[EventRecord, ...]
    oms_change: bytes
    ledger_change: tuple[LedgerTransaction, ...]
    risk_change: bytes
    book_feature_strategy_patch: tuple[tuple[str, bytes], ...]
    outbox_instructions: tuple[OutboxInstruction, ...]
    projection_updates: tuple[ProjectionUpdate, ...]
    candidate_state: EngineState

    def persistence(self) -> PersistenceTransition:
        return PersistenceTransition(
            self.input_event_id,
            self.base_state_version,
            self.events,
            self.ledger_change,
            self.outbox_instructions,
            self.projection_updates,
        )


@dataclass(frozen=True, slots=True)
class ReplayManifest:
    checkpoint: Checkpoint
    events: tuple[EventRecord, ...]
    expected_hashes: HashScopes
    raw_watermark: DurableWatermark
    expected_derived_hash: str
    source_rank_table: tuple[tuple[str, int], ...]
    code_hash: str
    schema_hash: str


@dataclass(frozen=True, slots=True)
class ReplayReport:
    operation: str
    logical_run_id: str
    matched: bool
    deterministic_state_hash: str
    economic_state_hash: str
    operational_hash: str
    audit_hash: str
    regenerated_derived_hash: str
    mismatches: tuple[str, ...]
    events: tuple[EventRecord, ...]
    network_order_dispatch_allowed: bool = False


class Engine:
    def __init__(
        self,
        logical_run_id: str,
        store: EventStore | None,
        *,
        raw_watermark: Callable[[], DurableWatermark],
        reducers: Iterable[FactReducer] = (),
        producers: Iterable[DecisionProducer] = (),
        code_hash: str,
        schema_hash: str,
        mode: EngineMode = EngineMode.DEMO,
        initial_state: EngineState | None = None,
        execution_attempt_id: str = "local",
        max_events_per_transition: int = 4096,
    ) -> None:
        if not code_hash or not schema_hash or max_events_per_transition < 1:
            raise ValueError(
                "engine requires code/schema compatibility and a bounded event closure"
            )
        self.store = store
        self.code_hash = code_hash
        self.schema_hash = schema_hash
        self._mode = EngineMode(mode)
        self.execution_attempt_id = execution_attempt_id
        self._raw_watermark = raw_watermark
        self._state = initial_state or EngineState.initial(logical_run_id)
        require_immutable(self._state)
        if self._state.logical_run_id != logical_run_id:
            raise ValueError("initial state belongs to another logical run")
        self._reducers = tuple(sorted(reducers, key=lambda r: (r.stage, r.reducer_id)))
        self._producers = tuple(sorted(producers, key=lambda p: (p.stage, p.producer_id)))
        for identifiers in (
            tuple(r.reducer_id for r in self._reducers),
            tuple(p.producer_id for p in self._producers),
        ):
            if any(not key for key in identifiers) or len(set(identifiers)) != len(identifiers):
                raise ValueError("reducer/producer IDs must be unique and nonempty")
        self._thread_id = get_ident()
        self._pending: Transition | None = None
        self._processing = False
        self._latched = False
        self._restored = False
        self._history: tuple[EventRecord, ...] = ()
        self._max_events = max_events_per_transition
        self._ready = store is None or store.state_watermark() == (
            self._state.state_version,
            self._state.engine_seq,
        ) == (0, 0)

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def mode(self) -> EngineMode:
        return self._mode

    @property
    def trading_latched_off(self) -> bool:
        return self._latched

    @property
    def network_order_dispatch_allowed(self) -> bool:
        # There is deliberately no mainnet-arming capability at this layer.
        return False

    @property
    def derived_history_hash(self) -> str:
        return self.state.derived_history_hash

    def require_network_dispatch(self) -> None:
        raise PermissionError(f"network order dispatch prohibited in {self.mode.value}")

    def hashes(self) -> HashScopes:
        return self.state.hashes()

    def _assert_owner(self) -> None:
        if get_ident() != self._thread_id:
            raise RuntimeError("single account writer called from another thread")

    def _input_envelope(self, event: IncomingEvent) -> Envelope:
        if type(event) is not IncomingEvent:
            raise TypeError("process requires an unsequenced IncomingEvent")
        return Envelope(
            **{field.name: getattr(event, field.name) for field in fields(IncomingEvent)},
            event_id=derive_id(
                "event", self.state.logical_run_id, "ingress", self.state.input_ordinal
            ),
            engine_seq=self.state.engine_seq + 1,
        )

    def _validate_fact(self, event: Envelope, state: EngineState) -> None:
        if event.run_id != state.logical_run_id:
            raise ValueError("event belongs to another logical run")
        if event.engine_seq != state.engine_seq + 1:
            raise ValueError("event engine sequences must be contiguous")
        if type(event.available_ns) is not int or event.available_ns < state.available_ns:
            raise ValueError("event availability timeline regressed")
        if event.schema_version != 1 or not isinstance(event.payload, bytes):
            raise ValueError("unsupported event schema/payload")
        payload_type = PAYLOAD_TYPES.resolve(event.event_type)
        payload = json.loads(event.payload)
        if (
            not isinstance(payload, dict)
            or canonical_bytes(payload) != event.payload
            or set(payload) != {field.name for field in fields(payload_type)}
        ):
            raise ValueError("event payload must match its canonical typed schema")
        if state.account_scope is not None:
            venue, environment, account = state.account_scope
            if (event.venue, event.environment) != (venue, environment) or (
                account is not None and event.account_id is not None and event.account_id != account
            ):
                raise ValueError("event belongs to another account writer")

    def _begin_fact(self, state: EngineState, record: EventRecord) -> EngineState:
        event = record.envelope
        self._validate_fact(event, state)
        if record.origin == "INPUT":
            expected_id = derive_id("event", state.logical_run_id, "ingress", state.input_ordinal)
            if event.event_id != expected_id:
                raise ValueError("input event identity does not match ingress ordinal")
            state = self._consume_timer(state, event)
            cursors = {(r.channel, r.connection_epoch): r for r in state.source_cursors}
            cursor = SourceCursor(
                event.source_channel,
                event.connection_epoch,
                event.source_message_id,
                event.source_sequence,
                state.input_ordinal + 1,
            )
            cursors[(cursor.channel, cursor.connection_epoch)] = cursor
            state = replace(
                state,
                state_version=state.state_version + 1,
                input_ordinal=state.input_ordinal + 1,
                last_input_id=event.event_id,
                source_cursors=tuple(cursors[key] for key in sorted(cursors)),
            )
        elif record.origin != "DERIVED" or not record.parent_id:
            raise ValueError("invalid fact origin/parent")
        scope = state.account_scope
        if scope is None or scope[2] is None:
            scope = (event.venue, event.environment, event.account_id)
        return replace(
            state,
            engine_seq=event.engine_seq,
            available_ns=event.available_ns,
            account_scope=scope,
            audit_hash=_chain(state.audit_hash, record),
            operational_hash=(
                _chain(state.operational_hash, record)
                if event.event_type in _OPERATIONAL
                else state.operational_hash
            ),
            derived_history_hash=(
                _chain(state.derived_history_hash, record)
                if record.origin == "DERIVED"
                else state.derived_history_hash
            ),
        )

    @staticmethod
    def _consume_timer(state: EngineState, event: Envelope) -> EngineState:
        if event.event_type != "TimerFired":
            return state
        payload = json.loads(event.payload)
        timers = {timer.timer_id: timer for timer in state.timers}
        timer = timers.get(payload.get("timer_id"))
        if (
            timer is None
            or int(payload["due_ns"]) != timer.due_ns
            or payload["scheduled_by_event_id"] != timer.scheduled_by_event_id
            or int(payload["actual_available_ns"]) != event.available_ns
            or event.available_ns < timer.due_ns
        ):
            raise ValueError("timer firing is missing, premature, or inconsistent with schedule")
        del timers[timer.timer_id]
        return replace(state, timers=tuple(sorted(timers.values())))

    def _reduce(
        self, state: EngineState, record: EventRecord
    ) -> tuple[EngineState, tuple[Reduction, ...]]:
        state = self._begin_fact(state, record)
        event = record.envelope
        state = apply_engine_fact(event, state)
        changes: list[Reduction] = []
        for reducer in self._reducers:
            reduction = reducer.apply(event, state)
            require_immutable(reduction)
            if not isinstance(reduction, Reduction) or any(
                getattr(reduction.state, name) != getattr(state, name) for name in _RESERVED
            ):
                raise ValueError("fact reducer changed engine-owned causal metadata")
            if any(
                type(getattr(reduction, name)) is not tuple
                for name in (
                    "ledger_transactions",
                    "outbox_instructions",
                    "projection_updates",
                    "timers",
                    "cancel_timers",
                )
            ):
                raise ValueError("reducer effects must be immutable tuples")
            state = reduction.state
            timers = {timer.timer_id: timer for timer in state.timers}
            for timer_id in reduction.cancel_timers:
                if timer_id not in timers:
                    raise ValueError("cannot cancel an unknown timer")
                del timers[timer_id]
            scheduled_ordinal = state.next_scheduled_ordinal
            for ordinal, request in enumerate(reduction.timers):
                timer_id = derive_id(
                    "timer", f"{state.logical_run_id}:{event.event_id}", reducer.reducer_id, ordinal
                )
                timers[timer_id] = ScheduledTimer(
                    event.available_ns + request.delay_ns,
                    scheduled_ordinal,
                    timer_id,
                    event.event_id,
                    event.instrument_id,
                    event.correlation_id,
                )
                scheduled_ordinal += 1
            state = replace(
                state,
                timers=tuple(sorted(timers.values())),
                next_scheduled_ordinal=scheduled_ordinal,
            )
            changes.append(reduction)
        return state, tuple(changes)

    def process(self, input_event: IncomingEvent) -> Transition:
        self._assert_owner()
        if self._processing:
            raise RuntimeError("single writer is already processing an input")
        self._processing = True
        try:
            return self._process_candidate(input_event)
        finally:
            self._processing = False

    def _process_candidate(self, input_event: IncomingEvent) -> Transition:
        self._assert_owner()
        if self._latched:
            raise RuntimeError("engine is latched off after persistence/reducer failure")
        if not self._ready:
            raise RuntimeError("existing history requires checkpoint/tail recovery")
        if self.mode == EngineMode.RECOVERY:
            raise RuntimeError("recovery consumes recorded facts through restore")
        if self._pending is not None:
            raise RuntimeError("a pending candidate must be committed or discarded")
        root = self._input_envelope(input_event)
        state = self.state
        queue = deque((EventRecord(root, "INPUT", root.causation_id),))
        events: list[EventRecord] = []
        changes: list[Reduction] = []
        while queue:
            if len(events) >= self._max_events:
                raise ValueError("derived event closure exceeded configured bound")
            record = queue.popleft()
            # Queued drafts do not receive a sequence until selected by the writer.
            event = replace(record.envelope, engine_seq=state.engine_seq + 1)
            record = replace(record, envelope=event)
            state, reductions = self._reduce(state, record)
            events.append(record)
            changes.extend(reductions)
            for producer in self._producers:
                drafts = producer.decide(event, state)
                require_immutable(drafts)
                if type(drafts) is not tuple:
                    raise ValueError("decision producer must return immutable drafts")
                for ordinal, draft in enumerate(drafts):
                    child_id = derive_id(
                        "event",
                        f"{state.logical_run_id}:{event.event_id}",
                        producer.producer_id,
                        ordinal,
                    )
                    child = replace(
                        event,
                        event_id=child_id,
                        event_type=draft.event_type,
                        schema_version=draft.schema_version,
                        payload=draft.payload,
                        instrument_id=draft.instrument_id or event.instrument_id,
                        causation_id=event.event_id,
                        source_channel=producer.producer_id,
                        source_message_id=None,
                        source_sequence=None,
                        raw_ref=None,
                        producer_version=self.code_hash,
                    )
                    queue.append(EventRecord(child, "DERIVED", event.event_id))
        candidate = Transition(
            root.event_id,
            self.state.state_version,
            tuple(events),
            canonical_bytes(state.oms),
            tuple(t for change in changes for t in change.ledger_transactions),
            canonical_bytes((state.risk_latches, state.risk_epoch)),
            tuple(
                (name, canonical_bytes(getattr(state, name)))
                for name in ("books", "features", "strategies")
            ),
            tuple(i for change in changes for i in change.outbox_instructions),
            tuple(p for change in changes for p in change.projection_updates),
            state,
        )
        self._pending = candidate
        return candidate

    def _validate_candidate(self, transition: Transition) -> None:
        self._assert_owner()
        if (
            self._pending is not transition
            or transition.base_state_version != self.state.state_version
        ):
            raise ValueError("candidate is foreign, discarded, or already committed")

    def discard(self, transition: Transition) -> None:
        self._validate_candidate(transition)
        self._pending = None

    def commit(self, transition: Transition) -> CommitReceipt:
        self._validate_candidate(transition)
        try:
            watermark = self._raw_watermark()
            if self.store is not None:
                receipt = self.store.commit(transition.persistence(), watermark)
            else:
                # Verification reduces real components without any database or transport writes.
                for record in transition.events:
                    ref = record.envelope.raw_ref
                    if ref is not None and not watermark.covers(RawRef.parse(ref)):
                        raise ValueError("replay raw reference exceeds durable watermark")
                receipt = CommitReceipt(
                    transition.events[0].envelope.engine_seq,
                    transition.events[-1].envelope.engine_seq,
                    transition.candidate_state.state_version,
                    watermark,
                    tuple(i.instruction_id for i in transition.outbox_instructions),
                )
        except BaseException:
            self._pending = None
            self._latched = True
            raise
        self._state = transition.candidate_state
        self._pending = None
        if self.store is None:
            self._history += transition.events
        return receipt

    def next_timer_input(self, actual_available_ns: int | None = None) -> IncomingEvent:
        self._assert_owner()
        if not self.state.timers:
            raise ValueError("no scheduled timer")
        timer = self.state.timers[0]
        available = (
            max(timer.due_ns, self.state.available_ns)
            if actual_available_ns is None
            else actual_available_ns
        )
        if available < max(timer.due_ns, self.state.available_ns):
            raise ValueError("timer availability precedes schedule/current timeline")
        venue, environment, account = self.state.account_scope or (None, "DEMO", None)
        return IncomingEvent(
            "TimerFired",
            1,
            self.state.logical_run_id,
            account,
            venue,
            environment,
            timer.instrument_id,
            "timer",
            "logical-clock-v1",
            timer.timer_id,
            None,
            None,
            None,
            available,
            available,
            available,
            timer.scheduled_by_event_id,
            timer.correlation_id,
            None,
            self.code_hash,
            canonical_bytes(
                TimerFired(timer.timer_id, timer.due_ns, timer.scheduled_by_event_id, available)
            ),
        )

    def checkpoint(self) -> Checkpoint:
        self._assert_owner()
        if self._pending is not None or self._processing or not self._ready or self._latched:
            raise RuntimeError("checkpoint requires a healthy committed state boundary")
        watermark = self.store.raw_watermark() if self.store is not None else None
        if watermark is not None:
            _require_watermark(self._raw_watermark(), watermark)
        checkpoint = Checkpoint.create(
            self.state, self.code_hash, self.schema_hash, watermark or self._raw_watermark()
        )
        if self.store is not None:
            if self.store.state_watermark() != (self.state.state_version, self.state.engine_seq):
                raise ValueError("checkpoint state does not match committed watermark")
            checkpoint_id = "checkpoint-" + checkpoint.snapshot_hash
            existing = self.store.latest_checkpoint()
            if existing is None or existing.checkpoint_id != checkpoint_id:
                self.store.save_checkpoint(
                    checkpoint_id, checkpoint.to_bytes(), checkpoint.raw_watermark
                )
        return checkpoint

    def restore(self, checkpoint: Checkpoint, tail: Iterable[EventRecord]) -> None:
        self._assert_owner()
        if self.mode != EngineMode.RECOVERY:
            raise PermissionError(
                "state restore requires recovery mode; network dispatch prohibited"
            )
        if self._pending is not None:
            raise RuntimeError("cannot restore with a pending candidate")
        checkpoint.verify(self.code_hash, self.schema_hash)
        if checkpoint.state.logical_run_id != self.state.logical_run_id:
            raise ValueError("checkpoint belongs to another logical run")
        records = tuple(tail)
        if self.store is not None:
            if records != self.store.read_after(checkpoint.engine_seq):
                raise ValueError("recovery tail is incomplete or differs from committed history")
            if checkpoint.engine_seq > self.store.state_watermark()[1]:
                raise ValueError("checkpoint exceeds committed event watermark")
            persisted = self.store.database.connection.execute(
                "SELECT snapshot FROM checkpoint_manifest WHERE checkpoint_id=?",
                ("checkpoint-" + checkpoint.snapshot_hash,),
            ).fetchone()
            if persisted is None or bytes(persisted[0]) != checkpoint.to_bytes():
                raise ValueError("checkpoint is not verified in this event store")
        state = checkpoint.state
        parents: set[str] = set()
        watermark = self._raw_watermark()
        _require_watermark(watermark, checkpoint.raw_watermark)
        persisted_watermark = self.store.raw_watermark() if self.store is not None else None
        if persisted_watermark is not None:
            _require_watermark(watermark, persisted_watermark)
        for record in records:
            if record.origin == "INPUT":
                parents.clear()
            elif (
                record.parent_id not in parents or record.envelope.causation_id != record.parent_id
            ):
                raise ValueError("recovery derived fact has missing cause")
            ref = record.envelope.raw_ref
            if ref is not None and not watermark.covers(RawRef.parse(ref)):
                raise ValueError("recovery raw reference exceeds durable watermark")
            state, _ = self._reduce(state, record)
            parents.add(record.envelope.event_id)
        if self.store is not None and self.store.state_watermark() != (
            state.state_version,
            state.engine_seq,
        ):
            raise ValueError("recovery state watermark does not match committed history")
        self._state = state
        self._ready = True
        self._latched = False
        self._restored = True

    def resume_offline(self) -> None:
        """Explicitly resume a verified recovery in credential-free DEMO only.

        Production recovery remains disarmed until later runtime readiness and
        operator controls grant a separate gateway capability. This method cannot
        create that capability or transform a non-DEMO account into a demo account.
        """
        self._assert_owner()
        if self.mode != EngineMode.RECOVERY or not self._restored or self._latched:
            raise RuntimeError("offline resume requires completed state recovery")
        if self.state.account_scope is not None and self.state.account_scope[1] != "DEMO":
            raise PermissionError("offline resume requires a DEMO account")
        self._mode = EngineMode.DEMO

    def replay_manifest(self, checkpoint: Checkpoint) -> ReplayManifest:
        self._assert_owner()
        checkpoint.verify(self.code_hash, self.schema_hash)
        if self._pending is not None:
            raise RuntimeError("manifest requires a committed state boundary")
        events = (
            self.store.read_after(checkpoint.engine_seq)
            if self.store is not None
            else tuple(r for r in self._history if r.envelope.engine_seq > checkpoint.engine_seq)
        )
        return ReplayManifest(
            checkpoint,
            events,
            self.hashes(),
            self._raw_watermark(),
            self.derived_history_hash,
            DeterministicScheduler.rank_table(),
            self.code_hash,
            self.schema_hash,
        )


class Replay:
    """Offline forensic verification and a fresh counterfactual event program.

    Counterfactual branches accept market facts only. Task 10 supplies a fresh
    simulator for hypothetical account reports; observed historical fills are
    never silently imported into that account. Existing audit history is untouched.
    """

    def __init__(
        self, reducers: Iterable[FactReducer] = (), producers: Iterable[DecisionProducer] = ()
    ):
        self._reducers = tuple(reducers)
        self._producers = tuple(producers)

    def _engine(self, manifest: ReplayManifest, state: EngineState, mode: EngineMode) -> Engine:
        manifest.checkpoint.verify(manifest.code_hash, manifest.schema_hash)
        _require_watermark(manifest.raw_watermark, manifest.checkpoint.raw_watermark)
        if manifest.source_rank_table != DeterministicScheduler.rank_table():
            raise ValueError("incompatible replay source rank table")
        return Engine(
            state.logical_run_id,
            None,
            raw_watermark=lambda: manifest.raw_watermark,
            reducers=self._reducers,
            producers=self._producers,
            initial_state=state,
            code_hash=manifest.code_hash,
            schema_hash=manifest.schema_hash,
            mode=mode,
            execution_attempt_id="offline-replay",
        )

    @staticmethod
    def _report(engine: Engine, operation: str, mismatches: tuple[str, ...] = ()) -> ReplayReport:
        hashes = engine.hashes()
        return ReplayReport(
            operation,
            engine.state.logical_run_id,
            not mismatches,
            hashes.deterministic_state_hash,
            hashes.economic_state_hash,
            hashes.operational_hash,
            hashes.audit_hash,
            engine.derived_history_hash,
            mismatches,
            engine._history,
        )

    def regenerate(self, manifest: ReplayManifest) -> ReplayReport:
        engine = self._engine(manifest, manifest.checkpoint.state, EngineMode.VERIFY)
        for record in manifest.events:
            if record.origin == "INPUT":
                engine.commit(engine.process(_incoming(record.envelope)))
            elif record.origin != "DERIVED":
                raise ValueError("invalid replay event origin")
        return self._report(engine, "FORENSIC_REGENERATION")

    def verify(self, manifest: ReplayManifest) -> ReplayReport:
        regenerated = self.regenerate(manifest)
        mismatches: list[str] = []
        if regenerated.events != manifest.events:
            mismatches.append("CANONICAL_EVENT_HISTORY_MISMATCH")
        if regenerated.regenerated_derived_hash != manifest.expected_derived_hash:
            mismatches.append("DERIVED_HISTORY_HASH_MISMATCH")
        for field in fields(HashScopes):
            if getattr(regenerated, field.name) != getattr(manifest.expected_hashes, field.name):
                mismatches.append(field.name.upper() + "_MISMATCH")
        return replace(
            regenerated,
            operation="FORENSIC_VERIFICATION",
            matched=not mismatches,
            mismatches=tuple(mismatches),
        )

    def branch(
        self,
        manifest: ReplayManifest,
        *,
        logical_run_id: str,
        inputs: Iterable[IncomingEvent] | None = None,
        initial_state: EngineState | None = None,
    ) -> ReplayReport:
        if not logical_run_id or logical_run_id == manifest.checkpoint.state.logical_run_id:
            raise ValueError("counterfactual operation requires a fresh logical run ID")
        state = initial_state or EngineState.initial(logical_run_id)
        if state.logical_run_id != logical_run_id or state.engine_seq or state.input_ordinal:
            raise ValueError("counterfactual account requires a fresh initial state")
        engine = self._engine(manifest, state, EngineMode.COUNTERFACTUAL)
        if inputs is None:
            selected = tuple(
                _incoming(r.envelope)
                for r in manifest.events
                if r.origin == "INPUT" and r.envelope.event_type in _BRANCH_MARKET
            )
        else:
            selected = tuple(inputs)
        for event in selected:
            if event.event_type not in _BRANCH_MARKET:
                raise ValueError(
                    "historical account/decision inputs cannot enter a counterfactual run"
                )
            # Drain existing timers only after all market facts at an equal instant.
            while engine.state.timers and engine.state.timers[0].due_ns < event.available_ns:
                engine.commit(engine.process(engine.next_timer_input()))
            engine.commit(engine.process(replace(event, run_id=logical_run_id, causation_id=None)))
        # Future timers are retained at dataset end; no invented future market data.
        return self._report(engine, "COUNTERFACTUAL")

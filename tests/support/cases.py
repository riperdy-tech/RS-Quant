import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from quantdesk.config.loader import load_config
from quantdesk.core.events import canonical_bytes
from quantdesk.core.ids import derive_id
from tests.support.accounting_case import accounting_worked_example_case
from tests.support.data_case import book_gap_and_rebuild_case
from tests.support.engine_case import deterministic_replay_case
from tests.support.oms_case import partial_fill_cancel_race_case

type CaseDriver = Callable[..., dict[str, object]]
_CASES: dict[str, CaseDriver] = {}


def register_case(name: str, driver: CaseDriver) -> None:
    if not name or name in _CASES:
        raise ValueError(f"case name must be non-empty and unique: {name!r}")
    _CASES[name] = driver


def run_case(name: str, **overrides: object) -> dict[str, object]:
    try:
        driver = _CASES[name]
    except KeyError as exc:
        raise KeyError(f"unknown acceptance case: {name}") from exc
    return driver(**overrides)


def _foundation_case(**overrides: object) -> dict[str, object]:
    qty = Decimal(str(overrides["qty"]))
    timestamp_ns = int(str(overrides["timestamp_ns"]))
    config = load_config(Path("configs/demo.yaml"))
    boundary = json.loads(canonical_bytes({"qty": qty, "timestamp_ns": timestamp_ns}))
    expected_id = derive_id("event", "parent-1", "foundation", 0)
    script = (
        "from quantdesk.core.ids import derive_id; "
        "print(derive_id('event', 'parent-1', 'foundation', 0))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "mode": config.mode.value,
        "live_enabled": config.account.live_enabled,
        "roundtrip_qty": boundary["qty"],
        "roundtrip_timestamp_ns": boundary["timestamp_ns"],
        "ids_match_across_processes": completed.stdout.strip() == expected_id,
    }


register_case("foundation", _foundation_case)
register_case("accounting_worked_example", accounting_worked_example_case)
register_case("partial_fill_cancel_race", partial_fill_cancel_race_case)
register_case("book_gap_and_rebuild", book_gap_and_rebuild_case)


def _crash_before_commit_case(**overrides: object) -> dict[str, object]:
    from quantdesk.core.events import Envelope
    from quantdesk.persistence.db import Database
    from quantdesk.persistence.event_store import (
        EventRecord,
        EventStore,
        OutboxInstruction,
        PersistenceTransition,
    )
    from quantdesk.persistence.manifests import RawRef
    from quantdesk.persistence.outbox import Outbox
    from quantdesk.persistence.raw_journal import RawFrame, RawJournal

    if overrides.get("fail_at") != "sqlite_commit":
        raise ValueError("crash_before_commit requires fail_at=sqlite_commit")

    class CommitFailure(sqlite3.Connection):
        def commit(self) -> None:
            raise sqlite3.OperationalError("injected SQLite commit failure")

    gateway_submissions: list[str] = []
    with TemporaryDirectory(prefix="quantdesk-crash-") as temporary:
        directory = Path(temporary)
        path = directory / "engine.sqlite"
        with (
            RawJournal(directory / "raw") as journal,
            Database(path, connection_factory=CommitFailure) as db,
        ):
            ref = journal.append(
                RawFrame(b'{"price":"100"}', "bitget", "DEMO", "websocket", 1000, 900, "epoch-1", 1)
            )
            watermark = journal.sync()
            complete_written = len(journal.references())
            envelope = Envelope(
                "RunBoundary",
                1,
                "run",
                "demo",
                "bitget",
                "DEMO",
                None,
                "fixture",
                "epoch-1",
                None,
                None,
                None,
                None,
                1000,
                900,
                1000,
                None,
                "event-1",
                str(ref),
                "test-1",
                canonical_bytes({"run_id": "run", "boundary": "start", "available_ns": 1000}),
                "event-1",
                1,
            )
            transition = PersistenceTransition(
                "event-1",
                0,
                (EventRecord(envelope, "INPUT"),),
                outbox_instructions=(
                    OutboxInstruction(
                        "instruction-1", "client-1", b"{}", "risk-1", "fence-1", 1, 2000
                    ),
                ),
            )
            with suppress(sqlite3.OperationalError):
                EventStore(db).commit(transition, watermark)
            # External gateway boundary observes only the actual committed reader.
            for instruction in Outbox(path).pending():
                gateway_submissions.append(instruction.instruction_id)
        with RawJournal(directory / "raw") as recovered, Database(path) as reopened:
            events = EventStore(reopened).read_after(0)
            return {
                "gateway_calls": len(gateway_submissions),
                "visible_outbox_instructions": len(Outbox(path).pending()),
                "complete_raw_frames_recovered": recovered.recovery.complete_frames,
                "complete_raw_frames_written": complete_written,
                "references_beyond_raw_watermark": sum(
                    record.envelope.raw_ref is not None
                    and not recovered.durable_watermark.covers(
                        RawRef.parse(record.envelope.raw_ref)
                    )
                    for record in events
                ),
            }


register_case("crash_before_commit", _crash_before_commit_case)
register_case("deterministic_replay", deterministic_replay_case)

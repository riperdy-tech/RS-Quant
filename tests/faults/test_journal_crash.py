import os
import sqlite3
from pathlib import Path

import pytest

from quantdesk.persistence.db import Database
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.outbox import Outbox
from quantdesk.persistence.raw_journal import JournalCorruption, RawJournal
from tests.unit.test_persistence import frame, transition


def test_every_torn_tail_boundary_recovers_only_complete_uncommitted_frames(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    with RawJournal(baseline) as journal:
        first = journal.append(frame())
        journal.sync()
        journal.append(frame(b'{"price":"101"}'))
        full = journal.active_path.read_bytes()
        active_name = journal.active_path.name
    import shutil

    for cut in range(first.end_offset, len(full)):
        directory = tmp_path / str(cut)
        shutil.copytree(baseline, directory)
        (directory / active_name).write_bytes(full[:cut])
        with RawJournal(directory) as recovered:
            assert recovered.recovery.complete_frames == 1
            assert recovered.recovery.truncated_bytes == cut - first.end_offset
            assert recovered.read(first).payload == b'{ "price": "100.00" }'


def test_corrupt_complete_active_frame_fails_closed(tmp_path: Path) -> None:
    with RawJournal(tmp_path) as journal:
        ref = journal.append(frame())
        journal.append(frame(b'{"price":"101"}'))
        path = journal.active_path
    data = bytearray(path.read_bytes())
    data[ref.end_offset - 5] ^= 1
    path.write_bytes(data)
    with pytest.raises(JournalCorruption):
        RawJournal(tmp_path)


def test_truncated_durable_frame_is_corruption_not_recoverable_tail(tmp_path: Path) -> None:
    with RawJournal(tmp_path) as journal:
        ref = journal.append(frame())
        journal.sync()
        path = journal.active_path
    path.write_bytes(path.read_bytes()[: ref.end_offset - 1])
    with pytest.raises(JournalCorruption):
        RawJournal(tmp_path)


def test_corrupted_sealed_chunk_is_never_accepted_as_tail(tmp_path: Path) -> None:
    with RawJournal(tmp_path) as journal:
        journal.append(frame())
        journal.seal()
    path = next(tmp_path.glob("*.zst"))
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    path.write_bytes(data)
    with pytest.raises(JournalCorruption):
        RawJournal(tmp_path)


def test_fsync_failure_never_advances_durable_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        ref = journal.append(frame())

        def disk_failure(fd: int) -> None:
            raise OSError("injected fsync failure")

        monkeypatch.setattr(os, "fsync", disk_failure)
        with pytest.raises(OSError):
            journal.sync()
        assert not journal.durable_watermark.covers(ref)
        with pytest.raises(ValueError):
            EventStore(db).commit(transition(str(ref)), journal.durable_watermark)
        assert Outbox(db.path).pending() == ()


def test_outbox_is_invisible_inside_transaction_and_sqlite_commit_failure_rolls_back(
    tmp_path: Path,
) -> None:
    observations: list[int] = []
    path = tmp_path / "engine.sqlite"

    class FailingCommit(sqlite3.Connection):
        def commit(self) -> None:
            observations.append(len(Outbox(path).pending()))
            raise sqlite3.OperationalError("injected commit failure")

    with (
        RawJournal(tmp_path / "raw") as journal,
        Database(path, connection_factory=FailingCommit) as db,
    ):
        ref = journal.append(frame())
        with pytest.raises(sqlite3.OperationalError):
            EventStore(db).commit(transition(str(ref)), journal.sync())
        assert observations == [0]
        assert EventStore(db).read_after(0) == ()
        assert EventStore(db).postings("deposit-1") == ()
        assert EventStore(db).projection("balances", "cash:USDT") is None
        assert Outbox(path).pending() == ()


def test_crash_does_not_publish_uncommitted_order(case) -> None:
    r = case("crash_before_commit", fail_at="sqlite_commit")
    assert r["gateway_calls"] == 0
    assert r["visible_outbox_instructions"] == 0
    assert r["complete_raw_frames_recovered"] == r["complete_raw_frames_written"]
    assert r["references_beyond_raw_watermark"] == 0


def test_interrupted_chunk_manifest_publication_retains_complete_active_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_replace = os.replace

    def interrupted_replace(source, destination):
        if Path(destination).name == "chunk-00000000.json":
            raise OSError("interrupted manifest publication")
        return original_replace(source, destination)

    with RawJournal(tmp_path) as journal:
        ref = journal.append(frame())
        monkeypatch.setattr(os, "replace", interrupted_replace)
        with pytest.raises(OSError):
            journal.seal()
    monkeypatch.undo()
    with RawJournal(tmp_path) as recovered:
        assert recovered.read(ref).payload == b'{ "price": "100.00" }'
        assert recovered.durable_watermark.covers(ref)
        recovered.seal()
    with RawJournal(tmp_path) as reopened:
        assert reopened.recovery.complete_frames == 1


def test_missing_sealed_manifest_cannot_discard_history(tmp_path: Path) -> None:
    with RawJournal(tmp_path) as journal:
        journal.append(frame())
        journal.seal()
    next(tmp_path.glob("chunk-*.json")).unlink()
    with pytest.raises(JournalCorruption):
        RawJournal(tmp_path)


def test_backup_publish_failure_leaves_no_usable_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quantdesk.persistence.backup import create_backup

    def failed_publish(source, destination):
        raise OSError("injected backup publish failure")

    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        EventStore(db).commit(transition(), journal.sync())
        monkeypatch.setattr(os, "rename", failed_publish)
        with pytest.raises(OSError):
            create_backup(db, journal, tmp_path / "backups", "broken")
        assert list((tmp_path / "backups").iterdir()) == []
        assert len(EventStore(db).read_after(0)) == 1

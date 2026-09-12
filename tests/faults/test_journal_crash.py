import os
import sqlite3
from pathlib import Path

import pytest

from quantdesk.persistence.db import Database
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.outbox import Outbox
from quantdesk.persistence.raw_journal import AESGCMCipher, JournalCorruption, RawJournal
from tests.unit.test_persistence import frame, transition


@pytest.mark.parametrize(
    "payload",
    [
        b'{"price":"101"}',
        b'{"symbol":"QDJ1"}',
        b"\x00QDJ1\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x01{}x\x00\x00\x00\x00binary",
    ],
)
def test_every_torn_tail_boundary_recovers_only_complete_uncommitted_frames(
    tmp_path: Path, payload: bytes
) -> None:
    baseline = tmp_path / "baseline"
    with RawJournal(baseline) as journal:
        first = journal.append(frame())
        journal.sync()
        journal.append(frame(payload))
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


def test_damaged_interior_length_followed_by_valid_frame_is_not_a_torn_tail(tmp_path: Path) -> None:
    import struct

    with RawJournal(tmp_path) as journal:
        journal.append(frame())
        journal.sync()
        second = journal.append(frame(b'{"symbol":"QDJ1"}'))
        journal.append(frame(b'{"price":"102"}'))
        active = journal.active_path
    damaged = bytearray(active.read_bytes())
    # Corrupt only the second frame's payload length, so it appears torn while
    # the following third frame has a complete valid header/metadata/CRC.
    struct.pack_into(">Q", damaged, second.start_offset + 8, len(damaged) * 2)
    active.write_bytes(damaged)
    with pytest.raises(JournalCorruption, match="interior"):
        RawJournal(tmp_path)
    assert active.read_bytes() == damaged


def test_crc_valid_payload_fragment_with_incomplete_metadata_is_not_a_later_frame(
    tmp_path: Path,
) -> None:
    import struct
    import zlib

    metadata = b'{"frame_ordinal":3}'
    fragment = struct.pack(">4sIQ", b"QDJ1", len(metadata), 1) + metadata + b"x"
    fragment += struct.pack(">I", zlib.crc32(fragment))
    with RawJournal(tmp_path) as journal:
        first = journal.append(frame())
        journal.sync()
        journal.append(frame(b"opaque-prefix" + fragment + b"opaque-suffix"))
        active = journal.active_path
    active.write_bytes(active.read_bytes()[:-1])
    with RawJournal(tmp_path) as recovered:
        assert recovered.recovery.complete_frames == 1
        assert recovered.read(first).payload == b'{ "price": "100.00" }'
        assert active.stat().st_size == first.end_offset


@pytest.mark.parametrize("embedded", [True, False])
def test_deeply_nested_metadata_parser_failure_has_safe_recovery_semantics(
    tmp_path: Path, embedded: bool
) -> None:
    import struct
    import zlib

    metadata = b"[" * 10_000 + b"0" + b"]" * 10_000
    fragment = struct.pack(">4sIQ", b"QDJ1", len(metadata), 1) + metadata + b"x"
    fragment += struct.pack(">I", zlib.crc32(fragment))
    with RawJournal(tmp_path) as journal:
        first = journal.append(frame())
        journal.sync()
        if embedded:
            journal.append(frame(b"opaque-prefix" + fragment + b"opaque-suffix"))
        active = journal.active_path
    if embedded:
        active.write_bytes(active.read_bytes()[:-1])
        with RawJournal(tmp_path) as recovered:
            assert recovered.recovery.complete_frames == 1
            assert recovered.read(first).payload == b'{ "price": "100.00" }'
            assert active.stat().st_size == first.end_offset
    else:
        complete_history = active.read_bytes() + fragment
        active.write_bytes(complete_history)
        with pytest.raises(JournalCorruption, match="metadata"):
            RawJournal(tmp_path)
        assert active.read_bytes() == complete_history


@pytest.mark.parametrize(
    "change",
    [
        {"venue": ""},
        {"receive_wall_ns": True},
        {"private": "false"},
        {"private": True, "key_id": None},
        {"message_ordinal": -1},
    ],
)
def test_complete_raw_frame_rejects_invalid_metadata_without_mutating_history(
    tmp_path: Path, change: dict[str, object]
) -> None:
    import json
    import struct
    import zlib

    with RawJournal(tmp_path) as journal:
        ref = journal.append(frame())
        active = journal.active_path
    original = active.read_bytes()
    _, metadata_length, payload_length = struct.unpack_from(">4sIQ", original)
    metadata = json.loads(original[16 : 16 + metadata_length])
    metadata.update(change)
    encoded_metadata = json.dumps(metadata).encode()
    payload = original[16 + metadata_length : 16 + metadata_length + payload_length]
    invalid = (
        struct.pack(">4sIQ", b"QDJ1", len(encoded_metadata), len(payload))
        + encoded_metadata
        + payload
    )
    invalid += struct.pack(">I", zlib.crc32(invalid))
    assert ref.start_offset == 0
    active.write_bytes(invalid)
    with pytest.raises(JournalCorruption, match="metadata"):
        RawJournal(tmp_path)
    assert active.read_bytes() == invalid


def test_truncated_durable_frame_is_corruption_not_recoverable_tail(tmp_path: Path) -> None:
    with RawJournal(tmp_path) as journal:
        ref = journal.append(frame())
        journal.sync()
        path = journal.active_path
    path.write_bytes(path.read_bytes()[: ref.end_offset - 1])
    with pytest.raises(JournalCorruption):
        RawJournal(tmp_path)


@pytest.mark.parametrize("damaged", [False, True])
def test_missing_watermark_preserves_acknowledged_history_bytes(
    tmp_path: Path, damaged: bool
) -> None:
    with RawJournal(tmp_path) as journal:
        journal.append(frame())
        journal.sync()
        active = journal.active_path
    (tmp_path / "watermark.json").unlink()
    if damaged:
        active.write_bytes(active.read_bytes()[:-1])
    preserved = active.read_bytes()
    with pytest.raises(JournalCorruption, match="watermark"):
        RawJournal(tmp_path)
    assert active.read_bytes() == preserved
    assert not (tmp_path / "watermark.json").exists()


def test_initial_zero_watermark_exists_before_unsynced_writes(tmp_path: Path) -> None:
    from quantdesk.persistence.manifests import DurableWatermark

    with RawJournal(tmp_path) as journal:
        marker = tmp_path / "watermark.json"
        assert marker.is_file()
        initial = DurableWatermark.from_bytes(marker.read_bytes())
        assert initial.frame_ordinal == 0
        assert initial.end_offset == 0
        journal.append(frame())
        assert DurableWatermark.from_bytes(marker.read_bytes()) == initial


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
            create_backup(
                db,
                journal,
                tmp_path / "backups",
                "broken",
                cipher=AESGCMCipher("backup-key", b"b" * 32),
            )
        assert list((tmp_path / "backups").iterdir()) == []
        assert len(EventStore(db).read_after(0)) == 1

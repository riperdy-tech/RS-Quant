"""Persistence behavior gates: real files/SQLite, boundary-only fault injection."""

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from quantdesk.core.events import (
    Envelope,
    OperatorCommand,
    OrderInstruction,
    PositionDiscrepancy,
    ProtectionReport,
    Quote,
    canonical_bytes,
)
from quantdesk.core.types import OrderType, Side, TimeInForce
from quantdesk.persistence.backup import create_backup, restore_backup
from quantdesk.persistence.db import Database, WriterOwnershipError
from quantdesk.persistence.event_store import (
    EconomicIdentity,
    EventRecord,
    EventStore,
    LedgerPosting,
    LedgerTransaction,
    OutboxInstruction,
    PersistenceTransition,
    ProjectionUpdate,
)
from quantdesk.persistence.migrations import migrate
from quantdesk.persistence.outbox import Outbox
from quantdesk.persistence.raw_journal import (
    AESGCMCipher,
    CaptureRejected,
    JournalCorruption,
    RawFrame,
    RawJournal,
)


def frame(payload: bytes = b'{ "price": "100.00" }', **changes: object) -> RawFrame:
    return replace(
        RawFrame(payload, "bitget", "DEMO", "websocket", 1000, 900, "epoch-1", 1),
        **changes,
    )


def event(event_id: str, seq: int, raw_ref: str | None = None) -> EventRecord:
    envelope = Envelope(
        event_type="RunBoundary",
        schema_version=1,
        run_id="demo-run",
        account_id="demo",
        venue="bitget",
        environment="DEMO",
        instrument_id=None,
        source_channel="fixture",
        connection_epoch="epoch-1",
        source_message_id=None,
        source_sequence=None,
        exchange_event_ns=None,
        exchange_transaction_ns=None,
        receive_wall_ns=1000,
        receive_monotonic_ns=900,
        available_ns=1000,
        causation_id=None,
        correlation_id=event_id,
        raw_ref=raw_ref,
        producer_version="test-1",
        payload=canonical_bytes({"run_id": "demo-run", "boundary": "start", "available_ns": 1000}),
        event_id=event_id,
        engine_seq=seq,
    )
    return EventRecord(envelope, "INPUT")


def deposit(transaction_id: str = "deposit-1", native_id: str = "cash-1") -> LedgerTransaction:
    return LedgerTransaction(
        transaction_id,
        "event-1",
        EconomicIdentity("bitget", "DEMO", "demo", "cash", native_id, "transfer"),
        (
            LedgerPosting("cash:USDT", "USDT", Decimal("1000")),
            LedgerPosting("equity:external", "USDT", Decimal("-1000")),
        ),
    )


def transition(raw_ref: str | None = None) -> PersistenceTransition:
    return PersistenceTransition(
        "event-1",
        0,
        (event("event-1", 1, raw_ref),),
        ledger_transactions=(deposit(),),
        outbox_instructions=(
            OutboxInstruction(
                "instruction-1",
                "client-1",
                b'{"side":"BUY"}',
                "risk-1",
                "fence-1",
                1,
                2000,
            ),
        ),
        projection_updates=(ProjectionUpdate("balances", "cash:USDT", b'"1000"'),),
    )


def test_raw_payload_fidelity_and_restart_durable_watermark(tmp_path: Path) -> None:
    directory = tmp_path / "raw"
    with RawJournal(directory) as journal:
        ref = journal.append(frame())
        assert not journal.durable_watermark.covers(ref)
        watermark = journal.sync()
        assert watermark.covers(ref)
    with RawJournal(directory) as recovered:
        assert recovered.durable_watermark == watermark
        assert recovered.read(ref).payload == b'{ "price": "100.00" }'
        assert recovered.recovery.complete_frames == 1


def test_private_cipher_boundary_and_transport_secrets_are_never_persisted(tmp_path: Path) -> None:
    cipher = AESGCMCipher("test-key", b"k" * 32)
    payload = b'{"balance":"17.123","apiKey":"KEY-SECRET","nested":{"signature":"SIG"}}'
    with RawJournal(tmp_path / "raw", cipher=cipher) as journal:
        ref = journal.append(
            frame(
                payload,
                private=True,
                url="https://example.test/orders?symbol=BTC&token=QUERY-SECRET",
                headers=(("Authorization", "HEADER-SECRET"), ("Cookie", "COOKIE-SECRET")),
            )
        )
        journal.seal()
        restored = journal.read(ref)
        assert json.loads(restored.payload) == {"balance": "17.123", "nested": {}}
        assert restored.url == "https://example.test/orders?symbol=BTC"
        assert restored.headers == ()
    persisted = b"".join(path.read_bytes() for path in (tmp_path / "raw").iterdir())
    for secret in (
        b"KEY-SECRET",
        b"SIG",
        b"QUERY-SECRET",
        b"HEADER-SECRET",
        b"COOKIE-SECRET",
        b"17.123",
        b"k" * 32,
    ):
        assert secret not in persisted
    with (
        pytest.raises(JournalCorruption),
        RawJournal(tmp_path / "raw", cipher=AESGCMCipher("test-key", b"x" * 32)) as bad,
    ):
        bad.read(ref)


def test_safe_private_payload_bytes_are_preserved_after_decryption(tmp_path: Path) -> None:
    with RawJournal(tmp_path, cipher=AESGCMCipher("key-1", b"a" * 32)) as journal:
        ref = journal.append(frame(private=True))
        journal.sync()
        assert journal.read(ref).payload == b'{ "price": "100.00" }'


@pytest.mark.parametrize("private", [False, True])
def test_redaction_preserves_nested_numeric_tokens_and_json_types(
    tmp_path: Path, private: bool
) -> None:
    payload = (
        b'{"price":0.123456789012345678901,"timestamp":1789200000000000001,'
        b'"apiKey":"secret","nested":[{"fee":-1.2300e-12,"signature":"hidden",'
        b'"id":999999999999999999999999999999,"ratio":4.500E+03,"zero":-0.0}],'
        b'"quoted":"1789200000000000001"}'
    )
    expected = (
        b'{"price":0.123456789012345678901,"timestamp":1789200000000000001,'
        b'"nested":[{"fee":-1.2300e-12,"id":999999999999999999999999999999,'
        b'"ratio":4.500E+03,"zero":-0.0}],"quoted":"1789200000000000001"}'
    )
    with RawJournal(tmp_path, cipher=AESGCMCipher("test-key", b"k" * 32)) as journal:
        ref = journal.append(frame(payload, private=private))
        journal.sync()
        assert journal.read(ref).payload == expected
    with RawJournal(tmp_path, cipher=AESGCMCipher("test-key", b"k" * 32)) as reopened:
        decoded = json.loads(reopened.read(ref).payload, parse_float=Decimal)
        assert decoded["price"] == Decimal("0.123456789012345678901")
        assert isinstance(decoded["timestamp"], int)
        assert decoded["nested"][0]["id"] == 999999999999999999999999999999
        assert isinstance(decoded["quoted"], str)


@pytest.mark.parametrize(
    "payload", [b'{"op":"login","args":["secret"]}', b'{"method":"auth","password":"secret"}']
)
def test_authentication_frames_are_rejected_before_disk(tmp_path: Path, payload: bytes) -> None:
    with RawJournal(tmp_path) as journal:
        with pytest.raises(CaptureRejected):
            journal.append(frame(payload))
        assert journal.recovery.complete_frames == 0
        assert journal.active_path.read_bytes() == b""


def test_private_capture_requires_explicit_encryption_boundary(tmp_path: Path) -> None:
    with RawJournal(tmp_path) as journal:
        with pytest.raises(CaptureRejected):
            journal.append(frame(private=True))
        assert journal.active_path.stat().st_size == 0


def test_sealed_chunks_are_chained_and_automatically_rotated(tmp_path: Path) -> None:
    with RawJournal(tmp_path, chunk_target_bytes=1) as journal:
        first = journal.append(frame())
        second = journal.append(frame(b'{"price":"101"}', message_ordinal=2))
        journal.seal()
        manifests = journal.manifests()
        assert len(manifests) == 2
        assert manifests[1].previous_hash == manifests[0].sha256
        assert journal.read(first).payload == b'{ "price": "100.00" }'
        assert journal.read(second).payload == b'{"price":"101"}'


def test_atomic_transition_roundtrip_signed_postings_and_committed_outbox(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        ref = journal.append(frame())
        store = EventStore(db)
        observer = Outbox(tmp_path / "engine.sqlite")
        assert observer.pending() == ()
        receipt = store.commit(transition(str(ref)), journal.sync())
        assert (receipt.first_seq, receipt.last_seq, receipt.state_version) == (1, 1, 1)
        assert receipt.outbox_ids == ("instruction-1",)
        assert tuple(row.envelope for row in store.read_after(0)) == (
            event("event-1", 1, str(ref)).envelope,
        )
        assert store.postings("deposit-1") == (
            LedgerPosting("cash:USDT", "USDT", Decimal("1000")),
            LedgerPosting("equity:external", "USDT", Decimal("-1000")),
        )
        assert [row.instruction_id for row in observer.pending()] == ["instruction-1"]
        assert store.projection("balances", "cash:USDT") == (b'"1000"', 1)


def test_canonical_commit_rejects_unsynced_or_other_journal_raw_refs(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, RawJournal(tmp_path / "other") as other:
        ref = journal.append(frame())
        with Database(tmp_path / "engine.sqlite") as db:
            store = EventStore(db)
            for watermark in (journal.durable_watermark, other.sync()):
                with pytest.raises(ValueError, match="watermark"):
                    store.commit(transition(str(ref)), watermark)
                assert store.read_after(0) == ()
                assert Outbox(db.path).pending() == ()


@pytest.mark.parametrize(
    "amount", [Decimal("-999"), Decimal("NaN"), Decimal("Infinity"), "-1000", 1.0]
)
def test_invalid_or_unbalanced_postings_roll_back_every_effect(
    tmp_path: Path, amount: object
) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        bad = replace(
            deposit(),
            postings=(
                LedgerPosting("cash:USDT", "USDT", Decimal("1000")),
                LedgerPosting("equity:external", "USDT", amount),
            ),
        )
        with pytest.raises((ValueError, TypeError)):
            EventStore(db).commit(replace(transition(), ledger_transactions=(bad,)), journal.sync())
        assert EventStore(db).read_after(0) == ()
        assert Outbox(db.path).pending() == ()


def test_unknown_assets_and_unauthorized_adjustments_fail_closed(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db, allowed_assets=frozenset({"USDT"}))
        bad_asset = replace(
            deposit(),
            postings=(
                LedgerPosting("cash:XYZ", "XYZ", Decimal("1")),
                LedgerPosting("equity:external", "XYZ", Decimal("-1")),
            ),
        )
        adjustment = replace(
            deposit(), identity=replace(deposit().identity, component_type="adjustment")
        )
        for transaction in (bad_asset, adjustment):
            with pytest.raises(ValueError):
                store.commit(
                    replace(transition(), ledger_transactions=(transaction,)), journal.sync()
                )
        assert store.read_after(0) == ()


def test_exact_balance_validation_is_independent_of_callers_decimal_precision(
    tmp_path: Path,
) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        postings = (
            LedgerPosting("cash:USDT", "USDT", Decimal("100000000000000000000000000001")),
            LedgerPosting("equity:external", "USDT", Decimal("-100000000000000000000000000000")),
        )
        with localcontext() as context:
            context.prec = 5
            with pytest.raises(ValueError, match="balance"):
                EventStore(db).commit(
                    replace(
                        transition(), ledger_transactions=(replace(deposit(), postings=postings),)
                    ),
                    journal.sync(),
                )


def test_economic_and_event_uniqueness_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "engine.sqlite"
    with RawJournal(tmp_path / "raw") as journal:
        watermark = journal.sync()
        with Database(path) as db:
            EventStore(db).commit(transition(), watermark)
        with Database(path) as db:
            store = EventStore(db)
            with pytest.raises((sqlite3.IntegrityError, ValueError)):
                store.commit(transition(), watermark)
            repeated = replace(deposit("duplicate", "cash-1"), event_id="event-2")
            with pytest.raises(sqlite3.IntegrityError):
                store.commit(
                    PersistenceTransition(
                        "event-2", 1, (event("event-2", 2),), ledger_transactions=(repeated,)
                    ),
                    watermark,
                )
            assert len(store.read_after(0)) == 1
            assert store.postings("duplicate") == ()


def test_cross_source_alias_prevents_double_application(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        alias = replace(deposit().identity, native_id="REST-cash-1")
        store.commit(
            replace(transition(), ledger_transactions=(replace(deposit(), aliases=(alias,)),)),
            journal.sync(),
        )
        repeated = replace(deposit("second", "REST-cash-1"), event_id="event-2")
        with pytest.raises(sqlite3.IntegrityError):
            store.commit(
                PersistenceTransition(
                    "event-2", 1, (event("event-2", 2),), ledger_transactions=(repeated,)
                ),
                journal.sync(),
            )
        assert len(store.read_after(0)) == 1


def test_single_writer_and_stale_transition_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "engine.sqlite"
    with RawJournal(tmp_path / "raw") as journal, Database(path) as db:
        with pytest.raises(WriterOwnershipError):
            Database(path)
        store = EventStore(db)
        store.commit(transition(), journal.sync())
        with pytest.raises(ValueError, match="version"):
            store.commit(
                PersistenceTransition("event-2", 0, (event("event-2", 2),)), journal.sync()
            )


def test_migrate_old_database_preserves_history_and_adds_constraints(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path, isolation_level=None)
    migrate(old, target_version=1)
    old.execute(
        "INSERT INTO projection_watermarks(name, state_version, engine_seq) "
        "VALUES ('engine', 7, 11)"
    )
    old.close()
    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.connection.execute(
            "SELECT state_version, engine_seq FROM projection_watermarks WHERE name='engine'"
        ).fetchone() == (7, 11)
        assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_backup_copies_wal_and_raw_watermarks_and_restores_isolated(tmp_path: Path) -> None:
    cipher = AESGCMCipher("backup-key", b"b" * 32)
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        ref = journal.append(frame())
        store = EventStore(db)
        store.commit(transition(str(ref)), journal.sync())
        checkpoint = store.save_checkpoint(
            "checkpoint-1", b'{"state_version":1}', journal.durable_watermark
        )
        bundle = create_backup(db, journal, tmp_path / "backups", "backup-1", cipher=cipher)
        assert bundle.manifest.engine_seq == 1
        assert bundle.manifest.state_version == 1
        assert bundle.manifest.checkpoint_id == checkpoint.checkpoint_id
        store.commit(PersistenceTransition("event-2", 1, (event("event-2", 2),)), journal.sync())
        restored = restore_backup(bundle.path, tmp_path / "restored", cipher=cipher)
        with (
            Database(restored / "engine.sqlite") as copy_db,
            RawJournal(restored / "raw") as copy_raw,
        ):
            assert [row.envelope.event_id for row in EventStore(copy_db).read_after(0)] == [
                "event-1"
            ]
            assert copy_raw.read(ref).payload == b'{ "price": "100.00" }'
            assert EventStore(copy_db).latest_checkpoint().snapshot == b'{"state_version":1}'
            assert json.loads((restored / "restore.json").read_bytes())["live_enabled"] is False
        with pytest.raises(FileExistsError):
            restore_backup(bundle.path, tmp_path / "restored")


def test_backup_rejects_corruption_without_publishing_restore(tmp_path: Path) -> None:
    cipher = AESGCMCipher("backup-key", b"b" * 32)
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        EventStore(db).commit(transition(), journal.sync())
        bundle = create_backup(db, journal, tmp_path / "backups", "backup-1", cipher=cipher)
    with (bundle.path / "engine.sqlite.enc").open("ab") as stream:
        stream.write(b"corrupted")
    with pytest.raises(ValueError, match="hash"):
        restore_backup(bundle.path, tmp_path / "restore", cipher=cipher)
    assert not (tmp_path / "restore").exists()


def test_private_backup_requires_cipher_and_never_writes_plaintext_account_database(
    tmp_path: Path,
) -> None:
    cipher = AESGCMCipher("journal-key", b"j" * 32)
    backup_cipher = AESGCMCipher("backup-key", b"b" * 32)
    with (
        RawJournal(tmp_path / "raw", cipher=cipher) as journal,
        Database(tmp_path / "engine.sqlite") as db,
    ):
        ref = journal.append(frame(b'{"balance":"PRIVATE-BALANCE"}', private=True))
        private_event = replace(
            event("event-1", 1, str(ref)),
            envelope=replace(
                event("event-1", 1, str(ref)).envelope, payload=b'"PRIVATE-ACCOUNT-HISTORY"'
            ),
        )
        EventStore(db).commit(
            replace(transition(str(ref)), events=(private_event,)), journal.sync()
        )
        with pytest.raises(ValueError, match="encrypt"):
            create_backup(db, journal, tmp_path / "backups", "unprotected")
        assert not (tmp_path / "backups" / "unprotected").exists()
        bundle = create_backup(db, journal, tmp_path / "backups", "protected", cipher=backup_cipher)
        persisted = b"".join(path.read_bytes() for path in bundle.path.rglob("*") if path.is_file())
        assert b"PRIVATE-ACCOUNT-HISTORY" not in persisted
        assert b"PRIVATE-BALANCE" not in persisted
        with pytest.raises(ValueError):
            restore_backup(bundle.path, tmp_path / "missing-key")
        restored = restore_backup(bundle.path, tmp_path / "restored", cipher=backup_cipher)
        with Database(restored / "engine.sqlite") as restored_db:
            assert (
                EventStore(restored_db).read_after(0)[0].envelope.payload
                == b'"PRIVATE-ACCOUNT-HISTORY"'
            )


def test_cash_transaction_uniqueness_is_account_wide_not_instrument_scoped(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        store.commit(transition(), journal.sync())
        repeated = replace(
            deposit("second"),
            event_id="event-2",
            identity=replace(deposit().identity, instrument="BTCUSDT"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.commit(
                PersistenceTransition(
                    "event-2", 1, (event("event-2", 2),), ledger_transactions=(repeated,)
                ),
                journal.sync(),
            )
        assert len(store.read_after(0)) == 1


def test_account_database_rejects_history_for_a_different_account(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        store.commit(transition(), journal.sync())
        other_event = replace(
            event("event-2", 2), envelope=replace(event("event-2", 2).envelope, account_id="other")
        )
        with pytest.raises(ValueError, match="account"):
            store.commit(PersistenceTransition("event-2", 1, (other_event,)), journal.sync())
        assert len(store.read_after(0)) == 1


def test_public_event_with_private_account_fields_still_requires_encrypted_backup(
    tmp_path: Path,
) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        account_event = replace(
            event("event-1", 1),
            envelope=replace(event("event-1", 1).envelope, event_type="AccountSnapshotObserved"),
        )
        EventStore(db).commit(PersistenceTransition("event-1", 0, (account_event,)), journal.sync())
        with pytest.raises(ValueError, match="encrypt"):
            create_backup(db, journal, tmp_path / "backups", "account-data")


def test_audit_and_checkpoint_history_cannot_be_updated_or_deleted(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        EventStore(db).commit(transition(), journal.sync())
        EventStore(db).save_checkpoint("checkpoint-1", b"{}", journal.durable_watermark)
        for table in (
            "events",
            "ledger_transactions",
            "ledger_postings",
            "economic_identities",
            "checkpoint_manifest",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.connection.execute(f"DELETE FROM {table}")
        assert len(EventStore(db).read_after(0)) == 1


def test_sealed_payloads_do_not_accumulate_in_writer_memory(tmp_path: Path) -> None:
    import gc
    import tracemalloc

    tracemalloc.start()
    try:
        with RawJournal(tmp_path, chunk_target_bytes=200_000) as journal:
            initial, _ = tracemalloc.get_traced_memory()
            first = None
            for index in range(32):
                ref = journal.append(frame(bytes([index]) * 250_000))
                if first is None:
                    first = ref
            gc.collect()
            retained, _ = tracemalloc.get_traced_memory()
            assert retained - initial < 2_000_000
            assert journal.read(first).payload == b"\0" * 250_000
    finally:
        tracemalloc.stop()


def test_private_append_forces_prompt_durability(tmp_path: Path) -> None:
    with RawJournal(tmp_path, cipher=AESGCMCipher("key", b"k" * 32)) as journal:
        ref = journal.append(frame(private=True))
        assert journal.durable_watermark.covers(ref)


def test_backup_preserves_checkpoint_and_committed_tail(tmp_path: Path) -> None:
    cipher = AESGCMCipher("backup-key", b"b" * 32)
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        first = journal.append(frame())
        store.commit(transition(str(first)), journal.sync())
        store.save_checkpoint("checkpoint-1", b'{"state":1}', journal.durable_watermark)
        second = journal.append(frame(b'{"price":"101"}'))
        store.commit(
            PersistenceTransition("event-2", 1, (event("event-2", 2, str(second)),)), journal.sync()
        )
        bundle = create_backup(db, journal, tmp_path / "backups", "with-tail", cipher=cipher)
    restored = restore_backup(bundle.path, tmp_path / "restored", cipher=cipher)
    with Database(restored / "engine.sqlite") as db, RawJournal(restored / "raw") as journal:
        store = EventStore(db)
        checkpoint = store.latest_checkpoint()
        assert checkpoint.engine_seq == 1
        assert [record.envelope.event_id for record in store.read_after(checkpoint.engine_seq)] == [
            "event-2"
        ]
        assert journal.read(second).payload == b'{"price":"101"}'


def test_execution_identity_is_instrument_scoped_and_approved_adjustment_is_valid(
    tmp_path: Path,
) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        first = replace(
            deposit(),
            identity=replace(deposit().identity, instrument="BTCUSDT", component_type="execution"),
        )
        store.commit(replace(transition(), ledger_transactions=(first,)), journal.sync())
        second = replace(
            first,
            transaction_id="second",
            event_id="event-2",
            identity=replace(first.identity, instrument="ETHUSDT"),
        )
        store.commit(
            PersistenceTransition(
                "event-2", 1, (event("event-2", 2),), ledger_transactions=(second,)
            ),
            journal.sync(),
        )
        adjustment = replace(
            deposit("approved", "adjustment-1"),
            event_id="event-3",
            identity=replace(
                deposit().identity, native_id="adjustment-1", component_type="adjustment"
            ),
            approved_by="operator-reviewed-command",
        )
        store.commit(
            PersistenceTransition(
                "event-3", 2, (event("event-3", 3),), ledger_transactions=(adjustment,)
            ),
            journal.sync(),
        )
        assert [posting.amount for posting in store.postings("approved")] == [
            Decimal("1000"),
            Decimal("-1000"),
        ]


def test_failed_migration_rolls_back_schema_and_keeps_old_history(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path, isolation_level=None) as old:
        migrate(old, target_version=1)
        saved_event = event("old-event", 1).envelope
        old.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "old-event",
                "RunBoundary",
                canonical_bytes(saved_event),
                saved_event.payload,
                "INPUT",
                None,
                None,
            ),
        )

    class FailedMigration(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("CREATE INDEX outbox_pending"):
                raise sqlite3.OperationalError("injected migration failure")
            return super().execute(sql, parameters)

    with sqlite3.connect(path, isolation_level=None, factory=FailedMigration) as connection:
        with pytest.raises(sqlite3.OperationalError):
            migrate(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT event_id FROM events").fetchall() == [("old-event",)]
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='checkpoint_manifest'"
            ).fetchall()
            == []
        )
    with Database(path) as db:
        assert EventStore(db).read_after(0)[0].envelope.event_id == "old-event"


def test_raw_writer_cannot_be_used_from_a_different_thread(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with RawJournal(tmp_path) as journal, ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(journal.append, frame())
        with pytest.raises(WriterOwnershipError):
            future.result()
        assert journal.references() == ()


def test_backup_refuses_a_different_journal_even_without_raw_event_references(
    tmp_path: Path,
) -> None:
    with (
        RawJournal(tmp_path / "raw") as journal,
        RawJournal(tmp_path / "other") as other,
        Database(tmp_path / "engine.sqlite") as db,
    ):
        EventStore(db).commit(transition(), journal.sync())
        with pytest.raises(ValueError, match="watermark"):
            create_backup(
                db,
                other,
                tmp_path / "backups",
                "wrong-raw",
                cipher=AESGCMCipher("backup-key", b"b" * 32),
            )
        assert not (tmp_path / "backups" / "wrong-raw").exists()


@pytest.mark.parametrize(
    "payload",
    [
        ProtectionReport("PRIVATE-PROTECTION-ID", "ACTIVE", 2, 1000, None),
        PositionDiscrepancy("PRIVATE-INSTRUMENT", 2, 3, "observed account position"),
        OperatorCommand("PRIVATE-COMMAND-ID", "PAUSE", (("account_id", "demo"),), "body-hash"),
        OrderInstruction(
            "PRIVATE-INSTRUCTION-ID",
            "PRIVATE-CLIENT-ID",
            "intent-1",
            "demo",
            "DEMO",
            "BTCUSDT",
            Side.BUY,
            1,
            100,
            OrderType.LIMIT,
            TimeInForce.GTC,
            False,
            None,
            None,
            "strategy-1",
            None,
            2000,
            "risk-1",
            "fence-1",
        ),
    ],
)
def test_canonical_only_private_facts_require_encrypted_backup(tmp_path: Path, payload) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        private_event = replace(
            event("event-1", 1),
            envelope=replace(
                event("event-1", 1).envelope,
                event_type=type(payload).__name__,
                payload=canonical_bytes(payload),
            ),
        )
        EventStore(db).commit(PersistenceTransition("event-1", 0, (private_event,)), journal.sync())
        with pytest.raises(ValueError, match="encrypt"):
            create_backup(db, journal, tmp_path / "backups", "plaintext")
        assert not (tmp_path / "backups" / "plaintext").exists()
        cipher = AESGCMCipher("backup-key", b"b" * 32)
        bundle = create_backup(db, journal, tmp_path / "backups", "encrypted", cipher=cipher)
        persisted = b"".join(file.read_bytes() for file in bundle.path.rglob("*") if file.is_file())
        assert b"PRIVATE-" not in persisted


@pytest.mark.parametrize("source", ["positions", "checkpoint", "unknown_type", "unknown_table"])
def test_backup_confidentiality_fails_closed_for_account_state_and_unknown_data(
    tmp_path: Path, source: str
) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        public_event = replace(
            event("event-1", 1),
            envelope=replace(
                event("event-1", 1).envelope,
                account_id=None,
                event_type="Quote",
                payload=canonical_bytes(Quote(100, 1, 101, 1, None, ())),
            ),
        )
        updates = (
            (ProjectionUpdate("positions", "BTCUSDT", b'"PRIVATE-POSITION"'),)
            if source == "positions"
            else ()
        )
        if source == "unknown_type":
            public_event = replace(
                public_event,
                envelope=replace(public_event.envelope, event_type="FutureAccountFact"),
            )
        store = EventStore(db)
        store.commit(
            PersistenceTransition("event-1", 0, (public_event,), projection_updates=updates),
            journal.sync(),
        )
        if source == "checkpoint":
            store.save_checkpoint("checkpoint-1", b'"PRIVATE-STATE"', journal.durable_watermark)
        if source == "unknown_table":
            db.connection.execute("CREATE TABLE future_account_state(value BLOB)")
            db.connection.execute(
                "INSERT INTO future_account_state VALUES (?)", (b"PRIVATE-FUTURE",)
            )
        with pytest.raises(ValueError, match="encrypt"):
            create_backup(db, journal, tmp_path / "backups", "plaintext")
        assert not (tmp_path / "backups" / "plaintext").exists()


def test_accountless_market_only_history_can_be_backed_up_without_a_cipher(tmp_path: Path) -> None:
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        public_event = replace(
            event("event-1", 1),
            envelope=replace(
                event("event-1", 1).envelope,
                account_id=None,
                event_type="Quote",
                payload=canonical_bytes(Quote(100, 1, 101, 1, None, ())),
            ),
        )
        EventStore(db).commit(PersistenceTransition("event-1", 0, (public_event,)), journal.sync())
        bundle = create_backup(db, journal, tmp_path / "backups", "public")
        assert (bundle.path / "engine.sqlite").is_file()
        restored = restore_backup(bundle.path, tmp_path / "restored")
        with Database(restored / "engine.sqlite") as restored_db:
            assert EventStore(restored_db).read_after(0)[0] == public_event

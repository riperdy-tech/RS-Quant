"""Reusable consistent backup bundles, independent of the later backup UI.

Called synchronously by the owning account writer at a transition boundary.
No retention/deletion schedule or key-recovery policy is implemented here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from quantdesk.core.events import canonical_bytes
from quantdesk.persistence.db import Database, open_reader
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.manifests import DurableWatermark, RawRef, atomic_write, sync_directory
from quantdesk.persistence.migrations import migrate
from quantdesk.persistence.raw_journal import JournalCipher, RawJournal

_BACKUP_AAD = b"QuantDesk SQLite backup v1"
_PUBLIC_EVENT_TYPES = frozenset(
    {
        "BookSnapshot",
        "BookDelta",
        "Trade",
        "Quote",
        "BarClosed",
        "MarkPrice",
        "FundingRateAnnounced",
        "InstrumentSpecUpdated",
    }
)
_PUBLIC_INFRASTRUCTURE_TABLES = frozenset({"events", "projection_watermarks", "store_metadata"})

type _Schema = tuple[int, tuple[tuple[str, str, str, str | None], ...]]


def _schema_snapshot(connection: sqlite3.Connection) -> _Schema:
    objects = tuple(
        (str(row[0]), str(row[1]), str(row[2]), row[3])
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        )
    )
    return int(connection.execute("PRAGMA user_version").fetchone()[0]), objects


@cache
def _known_schema() -> _Schema:
    # Use the actual authoritative migrations, including exact columns, declared
    # types, constraints and schema objects; no table-name-only exemptions.
    reference = sqlite3.connect(":memory:", isolation_level=None)
    try:
        migrate(reference)
        return _schema_snapshot(reference)
    finally:
        reference.close()


def _database_requires_encryption(connection: sqlite3.Connection) -> bool:
    """Allow plaintext only for accountless public market facts and bookkeepers.

    Everything else is confidential by default: private/control/decision events,
    unknown future event types, all populated account projections, ledger/outbox,
    command results and opaque checkpoints. New schema tables fail closed until
    explicitly reviewed; DEMO does not waive this data-confidentiality policy.
    """
    if _schema_snapshot(connection) != _known_schema():
        return True
    for event_type, envelope_bytes in connection.execute("SELECT type, envelope_json FROM events"):
        if event_type not in _PUBLIC_EVENT_TYPES:
            return True
        try:
            envelope = json.loads(envelope_bytes)
        except (ValueError, TypeError):
            return True
        if not isinstance(envelope, dict) or envelope.get("account_id", "unknown") is not None:
            return True
    if connection.execute(
        "SELECT 1 FROM store_metadata WHERE name <> 'raw_watermark' LIMIT 1"
    ).fetchone():
        return True
    if connection.execute(
        "SELECT 1 FROM projection_watermarks WHERE name <> 'engine' LIMIT 1"
    ).fetchone():
        return True
    for (table,) in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'"):
        if table not in _PUBLIC_INFRASTRUCTURE_TABLES:
            quoted_table = '"' + table.replace('"', '""') + '"'
            if connection.execute(f"SELECT 1 FROM {quoted_table} LIMIT 1").fetchone():
                return True
    return False


@dataclass(frozen=True, slots=True)
class BackupManifest:
    version: int
    engine_seq: int
    state_version: int
    raw_watermark: DurableWatermark
    checkpoint_id: str | None
    checkpoint_sha256: str | None
    files: tuple[tuple[str, str], ...]
    encryption_key_id: str | None = None


@dataclass(frozen=True, slots=True)
class BackupBundle:
    path: Path
    manifest: BackupManifest


def _read_manifest(path: Path) -> BackupManifest:
    value = json.loads((path / "backup.json").read_bytes())
    if value["version"] != 1:
        raise ValueError("unsupported backup manifest")
    return BackupManifest(
        1,
        int(value["engine_seq"]),
        int(value["state_version"]),
        DurableWatermark.from_bytes(canonical_bytes(value["raw_watermark"])),
        value["checkpoint_id"],
        value["checkpoint_sha256"],
        tuple((str(name), str(digest)) for name, digest in value["files"]),
        value.get("encryption_key_id"),
    )


def _confined_file(directory: Path, relative: str) -> Path:
    candidate = (directory / relative).resolve()
    if candidate == directory or not candidate.is_relative_to(directory):
        raise ValueError("backup manifest path escapes bundle")
    return candidate


def _database_bytes(path: Path, manifest: BackupManifest, cipher: JournalCipher | None) -> bytes:
    if manifest.encryption_key_id is None:
        return (path / "engine.sqlite").read_bytes()
    if cipher is None or cipher.key_id != manifest.encryption_key_id:
        raise ValueError("backup encryption key is unavailable")
    return cipher.decrypt((path / "engine.sqlite.enc").read_bytes(), _BACKUP_AAD)


def validate_backup(path: Path, *, cipher: JournalCipher | None = None) -> BackupManifest:
    path = path.resolve()
    manifest = _read_manifest(path)
    names = [name for name, _ in manifest.files]
    database_filename = "engine.sqlite.enc" if manifest.encryption_key_id else "engine.sqlite"
    if len(set(names)) != len(names) or database_filename not in names:
        raise ValueError("invalid backup file manifest")
    for name, digest in manifest.files:
        if hashlib.sha256(_confined_file(path, name).read_bytes()).hexdigest() != digest:
            raise ValueError("backup file hash mismatch")
    if manifest.encryption_key_id is None:
        connection = open_reader(path / "engine.sqlite")
    else:
        serialized = _database_bytes(path, manifest, cipher)
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(serialized)
            connection.execute("PRAGMA query_only=ON")
        except BaseException:
            connection.close()
            raise
    try:
        if manifest.encryption_key_id is None and _database_requires_encryption(connection):
            raise ValueError("private account backup requires encryption")
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("backup SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("backup SQLite foreign key check failed")
        row = connection.execute(
            "SELECT state_version, engine_seq FROM projection_watermarks WHERE name='engine'"
        ).fetchone()
        if (row or (0, 0)) != (manifest.state_version, manifest.engine_seq):
            raise ValueError("backup projection watermark mismatch")
        raw_row = connection.execute(
            "SELECT value FROM store_metadata WHERE name='raw_watermark'"
        ).fetchone()
        if raw_row:
            committed_raw = DurableWatermark.from_bytes(raw_row[0])
            if (
                committed_raw.journal_id != manifest.raw_watermark.journal_id
                or committed_raw.frame_ordinal > manifest.raw_watermark.frame_ordinal
                or committed_raw.chunk_index > manifest.raw_watermark.chunk_index
                or (
                    committed_raw.chunk_index == manifest.raw_watermark.chunk_index
                    and committed_raw.end_offset > manifest.raw_watermark.end_offset
                )
            ):
                raise ValueError("backup database/raw watermark mismatch")
        if (
            connection.execute("SELECT COALESCE(MAX(engine_seq), 0) FROM events").fetchone()[0]
            != manifest.engine_seq
        ):
            raise ValueError("backup event watermark mismatch")
        refs = [
            RawRef.parse(row[0])
            for row in connection.execute("SELECT raw_ref FROM events WHERE raw_ref IS NOT NULL")
        ]
        if any(not manifest.raw_watermark.covers(ref) for ref in refs):
            raise ValueError("backup reference beyond raw watermark")
        checkpoint = connection.execute(
            "SELECT checkpoint_id, sha256, snapshot, engine_seq, raw_watermark "
            "FROM checkpoint_manifest ORDER BY engine_seq DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if (checkpoint[0] if checkpoint else None) != manifest.checkpoint_id:
            raise ValueError("backup checkpoint manifest mismatch")
        if checkpoint:
            if (
                checkpoint[1] != manifest.checkpoint_sha256
                or hashlib.sha256(checkpoint[2]).hexdigest() != checkpoint[1]
                or checkpoint[3] > manifest.engine_seq
            ):
                raise ValueError("backup checkpoint hash/watermark mismatch")
            checkpoint_raw = DurableWatermark.from_bytes(checkpoint[4])
            if (
                checkpoint_raw.journal_id != manifest.raw_watermark.journal_id
                or checkpoint_raw.frame_ordinal > manifest.raw_watermark.frame_ordinal
            ):
                raise ValueError("backup checkpoint raw watermark mismatch")
    finally:
        connection.close()
    with RawJournal(path / "raw") as journal:
        if manifest.encryption_key_id is None and journal.has_private_frames:
            raise ValueError("private raw backup requires encryption")
        if journal.durable_watermark != manifest.raw_watermark:
            raise ValueError("backup raw manifest watermark mismatch")
        known = set(journal.references())
        if any(ref not in known for ref in refs):
            raise ValueError("backup raw reference is not a complete frame")
    return manifest


def create_backup(
    database: Database,
    journal: RawJournal,
    backup_directory: Path,
    name: str,
    *,
    cipher: JournalCipher | None = None,
) -> BackupBundle:
    database.assert_owner()
    if database.connection.in_transaction:
        raise ValueError("backup requires a committed transition boundary")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("invalid backup name")
    if cipher is None and (
        journal.has_private_frames or _database_requires_encryption(database.connection)
    ):
        raise ValueError("private account backup requires an injected encryption boundary")
    backup_directory = backup_directory.resolve()
    if backup_directory == journal.directory or backup_directory.is_relative_to(journal.directory):
        raise ValueError("backup destination must be isolated from raw journal")
    backup_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = backup_directory / name
    if destination.exists():
        raise FileExistsError(destination)
    journal.seal()
    store = EventStore(database)
    state_version, seq = store.state_watermark()
    checkpoint = store.latest_checkpoint()
    temporary = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=backup_directory))
    try:
        # Always rebuild the consistent image in memory before writing a bundle.
        # Current-row classification cannot classify deleted cells/free pages;
        # VACUUM removes that residual history without modifying the source DB.
        filename = "engine.sqlite.enc" if cipher else "engine.sqlite"
        target_connection = sqlite3.connect(":memory:")
        try:
            database.connection.backup(target_connection)
            # A WAL backup into :memory: retains WAL flags. Rebuilding also makes
            # a standalone image suitable for deserialize and removes free pages.
            target_connection.execute("PRAGMA journal_mode=OFF")
            target_connection.execute("VACUUM")
            if target_connection.execute("PRAGMA integrity_check").fetchone() != (
                "ok",
            ) or target_connection.execute("PRAGMA freelist_count").fetchone() != (0,):
                raise ValueError("sanitized SQLite export validation failed")
            serialized = target_connection.serialize()
            atomic_write(
                temporary / filename,
                cipher.encrypt(serialized, _BACKUP_AAD) if cipher else serialized,
            )
        finally:
            target_connection.close()
        with (temporary / filename).open("r+b") as stream:
            os.fsync(stream.fileno())
        raw_destination = temporary / "raw"
        raw_destination.mkdir(mode=0o700)
        raw_files = [
            journal.directory / "journal.json",
            journal.directory / "watermark.json",
            journal.active_path,
        ]
        for chunk in journal.manifests():
            raw_files.extend(
                (
                    journal.directory / chunk.filename,
                    journal.directory / f"chunk-{chunk.chunk_index:08d}.json",
                )
            )
        for source in raw_files:
            atomic_write(raw_destination / source.name, source.read_bytes())
        files = tuple(
            (file.relative_to(temporary).as_posix(), hashlib.sha256(file.read_bytes()).hexdigest())
            for file in sorted(temporary.rglob("*"))
            if file.is_file()
        )
        manifest = BackupManifest(
            1,
            seq,
            state_version,
            journal.durable_watermark,
            checkpoint.checkpoint_id if checkpoint else None,
            checkpoint.sha256 if checkpoint else None,
            files,
            cipher.key_id if cipher else None,
        )
        atomic_write(temporary / "backup.json", canonical_bytes(manifest))
        validate_backup(temporary, cipher=cipher)
        # The caller selects an isolated destination; never overwrite a backup.
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(temporary, destination)
        sync_directory(backup_directory)
        return BackupBundle(destination, manifest)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def restore_backup(
    backup_path: Path, destination: Path, *, cipher: JournalCipher | None = None
) -> Path:
    backup_path = backup_path.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(backup_path):
        raise ValueError("restore destination must be isolated from backup")
    manifest = validate_backup(backup_path, cipher=cipher)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        for name, _ in manifest.files:
            target = _confined_file(temporary, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, _confined_file(backup_path, name).read_bytes())
        atomic_write(temporary / "backup.json", canonical_bytes(manifest))
        validate_backup(temporary, cipher=cipher)
        if manifest.encryption_key_id:
            atomic_write(temporary / "engine.sqlite", _database_bytes(temporary, manifest, cipher))
            # Keep encrypted provenance copy; restored active data is protected by
            # the destination account directory access policy, like normal SQLite.
        atomic_write(
            temporary / "restore.json",
            canonical_bytes(
                {
                    "mode": "RECOVERY",
                    "live_enabled": False,
                    "entries_paused": True,
                    "requires_reconciliation": True,
                }
            ),
        )
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(temporary, destination)
        sync_directory(destination.parent)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

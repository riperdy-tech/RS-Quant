"""Integration tests for consistent backup, encryption, retention policies, and isolated restore (§16.3)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from quantdesk.core.checkpoint import Checkpoint
from quantdesk.core.engine import EngineMode
from quantdesk.persistence.backup import (
    RetentionPolicy,
    apply_retention_cleanup,
    create_backup,
    preview_retention_cleanup,
    restore_backup,
    validate_backup,
)
from quantdesk.persistence.raw_journal import AESGCMCipher
from tests.support.oms_case import AccountCase, instruction


def test_consistent_sqlite_backup_and_restore():
    """Verifies consistent SQLite backup creation, encrypted bundle validation, and isolated restore (§16.3)."""
    cipher = AESGCMCipher("backup-int-key-01", b"01234567890123456789012345678901")

    with TemporaryDirectory(prefix="quantdesk-backup-int-") as temp_dir:
        base_dir = Path(temp_dir)
        account_dir = base_dir / "account"
        backup_dir = base_dir / "backups"
        restore_dir = base_dir / "restored_account"

        # 1. Populate real account engine with orders and committed transitions
        account = AccountCase(account_dir, mode=EngineMode.DEMO)
        try:
            account.engine.checkpoint()
            inst1 = instruction(client="order-bk-01", quantity_lots=5, price_ticks=50000)
            account.approve(inst1)
            inst2 = instruction(client="order-bk-02", quantity_lots=10, price_ticks=50100)
            account.approve(inst2)

            account.engine.checkpoint()
            original_hashes = account.engine.hashes()

            # 2. Create consistent backup
            bundle = create_backup(
                account.db,
                account.journal,
                backup_dir,
                "backup-bundle-01",
                cipher=cipher,
            )
            assert bundle.path.exists()
            assert (bundle.path / "backup.json").exists()
            assert (bundle.path / "engine.sqlite.enc").exists()

            # 3. Validate backup manifest
            manifest = validate_backup(bundle.path, cipher=cipher)
            assert manifest.version == 1
            assert manifest.encryption_key_id == "backup-int-key-01"

            # 4. Restore into isolated directory
            restored_path = restore_backup(bundle.path, restore_dir, cipher=cipher)
            assert restored_path.exists()

            # 5. Verify disarmed restore metadata
            restore_json = restored_path / "restore.json"
            assert restore_json.exists()
            restore_meta = json.loads(restore_json.read_text(encoding="utf-8"))
            assert restore_meta["mode"] == "RECOVERY"
            assert restore_meta["live_enabled"] is False
            assert restore_meta["entries_paused"] is True
            assert restore_meta["requires_reconciliation"] is True

            # 6. Load recovered engine and assert economic state hash equality
            recovered_account = AccountCase(restored_path, mode=EngineMode.RECOVERY)
            try:
                latest_cp = recovered_account.store.latest_checkpoint()
                assert latest_cp is not None
                cp = Checkpoint.from_bytes(latest_cp.snapshot)
                tail = recovered_account.store.read_after(cp.engine_seq)
                recovered_account.engine.restore(cp, tail)

                restored_hashes = recovered_account.engine.hashes()
                assert restored_hashes.economic_state_hash == original_hashes.economic_state_hash
            finally:
                recovered_account.close()

        finally:
            account.close()


def test_backup_retention_pinning_and_deletion_preview():
    """Verifies retention cleanup preserves pinned data and correctly previews deletions (§16.3)."""
    with TemporaryDirectory(prefix="quantdesk-retention-") as temp_dir:
        base_dir = Path(temp_dir)
        logs_dir = base_dir / "logs"
        raw_dir = base_dir / "raw"
        logs_dir.mkdir(parents=True)
        raw_dir.mkdir(parents=True)

        now = time.time()
        twenty_days_ago = now - (20 * 86400)
        forty_days_ago = now - (40 * 86400)

        # 1. Log files (14 days threshold)
        recent_log = logs_dir / "app_recent.log"
        recent_log.write_text("recent log", encoding="utf-8")

        old_log = logs_dir / "app_old.log"
        old_log.write_text("old log", encoding="utf-8")
        import os
        os.utime(old_log, (twenty_days_ago, twenty_days_ago))

        pinned_old_log = logs_dir / "incident_CRITICAL_01.log"
        pinned_old_log.write_text("critical incident", encoding="utf-8")
        os.utime(pinned_old_log, (twenty_days_ago, twenty_days_ago))

        # 2. Raw market segments (30 days threshold)
        recent_raw = raw_dir / "segment_recent.parquet"
        recent_raw.write_bytes(b"recent-raw-data")

        old_raw = raw_dir / "segment_old.parquet"
        old_raw.write_bytes(b"old-raw-data-123456789")
        os.utime(old_raw, (forty_days_ago, forty_days_ago))

        pinned_old_raw = raw_dir / "checkpoint_pinned_audit.parquet"
        pinned_old_raw.write_bytes(b"pinned-raw-data")
        os.utime(pinned_old_raw, (forty_days_ago, forty_days_ago))

        # Policy with pinned identifiers
        policy = RetentionPolicy(
            logs_max_days=14,
            unpinned_market_segments_max_days=30,
            pinned_identifiers=frozenset({"incident_CRITICAL", "checkpoint_pinned"}),
        )

        # 3. Preview deletion
        preview = preview_retention_cleanup(base_dir, policy, now_epoch_sec=now)
        candidate_names = {p.name for p in preview.candidates_to_delete}
        assert "app_old.log" in candidate_names
        assert "segment_old.parquet" in candidate_names
        assert "recent.log" not in candidate_names
        assert "incident_CRITICAL_01.log" not in candidate_names
        assert "checkpoint_pinned_audit.parquet" not in candidate_names
        assert preview.bytes_to_reclaim > 0

        preserved_names = {p.name for p in preview.pinned_files_preserved}
        assert "incident_CRITICAL_01.log" in preserved_names
        assert "checkpoint_pinned_audit.parquet" in preserved_names
        assert "app_recent.log" in preserved_names

        # 4. Apply retention cleanup
        applied = apply_retention_cleanup(base_dir, policy, now_epoch_sec=now)
        assert len(applied.candidates_to_delete) == 2
        assert not old_log.exists()
        assert not old_raw.exists()
        assert recent_log.exists()
        assert pinned_old_log.exists()
        assert pinned_old_raw.exists()


def test_restore_safeguards_and_isolation():
    """Verifies restore refuses existing destinations and unisolated locations (§16.3)."""
    cipher = AESGCMCipher("backup-safe-key", b"01234567890123456789012345678901")

    with TemporaryDirectory(prefix="quantdesk-safeguards-") as temp_dir:
        base_dir = Path(temp_dir)
        account_dir = base_dir / "account"
        backup_dir = base_dir / "backups"

        account = AccountCase(account_dir, mode=EngineMode.DEMO)
        try:
            account.engine.checkpoint()
            bundle = create_backup(
                account.db,
                account.journal,
                backup_dir,
                "backup-01",
                cipher=cipher,
            )
        finally:
            account.close()

        # Cannot restore to an already existing destination directory
        existing_dest = base_dir / "existing"
        existing_dest.mkdir()
        with pytest.raises(FileExistsError):
            restore_backup(bundle.path, existing_dest, cipher=cipher)

        # Cannot restore inside backup directory
        sub_dest = bundle.path / "restore_sub"
        with pytest.raises(ValueError, match="must be isolated from backup"):
            restore_backup(bundle.path, sub_dest, cipher=cipher)

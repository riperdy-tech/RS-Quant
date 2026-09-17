"""Case driver for second launch protection and backup restoration (§16.1, §16.2, §16.3)."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from quantdesk.core.checkpoint import Checkpoint
from quantdesk.core.engine import EngineMode
from quantdesk.launcher.app import LauncherApp
from quantdesk.persistence.backup import create_backup, restore_backup
from quantdesk.persistence.raw_journal import AESGCMCipher
from tests.support.oms_case import AccountCase, instruction


def launcher_second_instance_and_restore_case(**overrides: Any) -> dict[str, Any]:
    """Tests single instance ownership and consistent backup restoration with real local components."""
    cipher = AESGCMCipher("supervisor-key-01", b"01234567890123456789012345678901")

    with TemporaryDirectory(prefix="quantdesk-supervisor-test-") as temp_dir:
        base_dir = Path(temp_dir)
        account_dir = base_dir / "account"
        backup_dest = base_dir / "backups"
        restore_dest = base_dir / "restored"

        # 1. Start primary launcher instance (owns account lock and supervisor)
        launcher1 = LauncherApp(
            account_dir=account_dir,
            account_name="demo",
            mode="DEMO",
            headless=True,
            auto_open_browser=False,
            preferred_port=8123,
            spawn_api=False,
        )
        try:
            r1 = launcher1.start()
            assert r1["action"] == "STARTED_NEW_INSTANCE"

            # 2. Attempt second launch on same account directory
            launcher2 = LauncherApp(
                account_dir=account_dir,
                account_name="demo",
                mode="DEMO",
                headless=True,
                auto_open_browser=False,
                preferred_port=8123,
                spawn_api=False,
            )
            try:
                r2 = launcher2.start()

                account_writer_process_count = r2["account_writer_process_count"]
                second_launch_action = r2["action"]

                # 3. Setup real engine state with ledger transactions and checkpoint
                account = AccountCase(account_dir, mode=EngineMode.DEMO)
                try:
                    # Checkpoint baseline
                    account.engine.checkpoint()

                    # Execute real order to generate financial positions & ledger state
                    inst = instruction(client="sup-client-01", quantity_lots=10, price_ticks=50000)
                    account.approve(inst)

                    # Checkpoint current active state
                    account.engine.checkpoint()
                    backup_economic_state_hash = account.engine.hashes().economic_state_hash

                    # 4. Take consistent SQLite backup with cipher
                    bundle = create_backup(
                        account.db,
                        account.journal,
                        backup_dest,
                        "backup-state-01",
                        cipher=cipher,
                    )

                    # 5. Restore into completely new isolated directory with cipher
                    restored_path = restore_backup(bundle.path, restore_dest, cipher=cipher)

                    # 6. Verify restore metadata disarms live trading (§16.3)
                    restore_meta = json.loads((restored_path / "restore.json").read_text(encoding="utf-8"))
                    restored_live_armed = bool(restore_meta.get("live_enabled", False))

                    # 7. Recover engine in the restored directory and compare state hashes
                    recovered_account = AccountCase(restored_path, mode=EngineMode.RECOVERY)
                    try:
                        latest_cp = recovered_account.store.latest_checkpoint()
                        assert latest_cp is not None, "Checkpoint must exist in restored event store"
                        restored_checkpoint = Checkpoint.from_bytes(latest_cp.snapshot)
                        tail_events = recovered_account.store.read_after(restored_checkpoint.engine_seq)
                        recovered_account.engine.restore(restored_checkpoint, tail_events)
                        restored_economic_state_hash = recovered_account.engine.hashes().economic_state_hash
                    finally:
                        recovered_account.close()

                finally:
                    account.close()
            finally:
                launcher2.stop()
        finally:
            launcher1.stop()

        return {
            "account_writer_process_count": account_writer_process_count,
            "second_launch_action": second_launch_action,
            "backup_economic_state_hash": backup_economic_state_hash,
            "restored_economic_state_hash": restored_economic_state_hash,
            "restored_live_armed": restored_live_armed,
        }

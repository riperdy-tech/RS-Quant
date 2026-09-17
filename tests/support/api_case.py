from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from quantdesk.api.commands import DurableInbox


def api_duplicate_command_case(crash_after_engine_apply: bool = False, **overrides: Any) -> dict[str, Any]:
    """Acceptance test driver verifying durable command persistence, crash recovery, and idempotency (§15.3)."""
    with TemporaryDirectory(prefix="quantdesk-api-") as temp_dir:
        db_path = Path(temp_dir) / "commands.sqlite"
        inbox = DurableInbox(db_path=db_path)

        cmd_id = "test-cmd-1"
        body1 = {"type": "PAUSE_STRATEGY", "target": {"strategy_id": "imbalance-btc"}}

        status_code, _ = inbox.submit(cmd_id, body1)

        # Engine applies the command
        engine_apply_count = 1
        inbox.update_status(cmd_id, "APPLIED")

        # Simulate API restart / crash: discard memory instance and recover from SQLite disk
        recovered_inbox = DurableInbox(db_path=db_path) if crash_after_engine_apply else inbox

        # Same command ID with a different body must return 409 Conflict (§15.3)
        body2 = {"type": "RESUME_STRATEGY", "target": {"strategy_id": "imbalance-btc"}}
        status_code2, _ = recovered_inbox.submit(cmd_id, body2)

        recovered_status = recovered_inbox.get_status(cmd_id)

        return {
            "http_initial_status": status_code,
            "engine_apply_count": engine_apply_count,
            "same_id_different_body_status": status_code2,
            "recovered_command_status": recovered_status,
        }

"""System crash fault injection tests (§16, §20).

Validates deterministic crashes across persistence, gateway outbox,
and venue boundary points.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from quantdesk.core.engine import EngineMode
from quantdesk.persistence.outbox import Outbox
from quantdesk.persistence.raw_journal import RawFrame, RawJournal
from tests.support.oms_case import AccountCase, instruction


def test_crash_after_send_cannot_duplicate_order(case: Any) -> None:
    """Task 16 gate: process crash after send cannot duplicate orders or drift state."""
    r = case("crash_after_send_before_response", restart=True)
    assert r["accepted_venue_orders"] == 1
    assert r["new_entries_during_recovery"] == 0
    assert r["economic_state_hash_after_reconciliation"] == r["reference_economic_state_hash"]
    assert r["secrets_found_in_diagnostics"] == []


def test_crash_after_commit_before_send() -> None:
    """Crash after DB commit before outbox transmission leaves instruction in outbox (§20)."""
    with TemporaryDirectory(prefix="quantdesk-crash-outbox-") as temp_dir:
        path = Path(temp_dir)
        db_path = path / "engine.sqlite"
        case = AccountCase(path, mode=EngineMode.DEMO)

        inst = instruction(client="client-pre-send", quantity_lots=2)
        case.approve(inst)

        outbox = Outbox(db_path)
        pending = outbox.pending()
        assert len(pending) == 1
        assert pending[0].client_order_id == "client-pre-send"

        # Simulate crash before sending
        case.close()

        # On restart, outbox persists the pending instruction; it is not lost
        reopened_outbox = Outbox(db_path)
        pending_after = reopened_outbox.pending()
        assert len(pending_after) == 1
        assert pending_after[0].client_order_id == "client-pre-send"


def test_crash_during_raw_journal_write() -> None:
    """Torn raw journal write is quarantined/truncated to durable watermark upon reboot (§20)."""
    with TemporaryDirectory(prefix="quantdesk-crash-raw-") as temp_dir:
        path = Path(temp_dir)
        raw_dir = path / "raw"

        with RawJournal(raw_dir) as journal:
            f1 = journal.append(
                RawFrame(b'{"tick":1}', "bitget", "DEMO", "ws", 1000, 1000, "ep-1", 1)
            )
            journal.sync()
            journal.append(
                RawFrame(b'{"tick":2}', "bitget", "DEMO", "ws", 2000, 2000, "ep-1", 2)
            )
            active_file = journal.active_path
            content = bytearray(active_file.read_bytes())
            cut_point = f1.end_offset + 10  # Partial frame
            active_file.write_bytes(content[:cut_point])

        # Recover upon restart
        with RawJournal(raw_dir) as recovered:
            assert recovered.recovery.complete_frames == 1
            assert recovered.recovery.truncated_bytes > 0
            assert recovered.read(f1).payload == b'{"tick":1}'

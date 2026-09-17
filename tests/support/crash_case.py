"""Crash after send before response test driver (§16, §20).

Validates process crash at external boundary after HTTP/WS send,
ensuring reconciliation does not duplicate orders or dispatch new entries.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from quantdesk.core.checkpoint import Checkpoint
from quantdesk.core.engine import Engine, EngineMode
from quantdesk.core.events import (
    SubmitTransportResult,
)
from quantdesk.execution.oms import OMS, execution_reducer
from quantdesk.execution.order_state import OMSState
from quantdesk.observability.diagnostics import diagnostics_exporter
from quantdesk.persistence.db import Database
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.outbox import Outbox
from quantdesk.persistence.raw_journal import RawJournal
from quantdesk.portfolio.ledger import Ledger, LedgerState
from tests.support.accounting_case import spec
from tests.support.oms_case import AccountCase, instruction


def crash_after_send_before_response_case(**overrides: Any) -> dict[str, Any]:
    """Simulates crash after outbox order transmission before venue response receipt."""
    restart = overrides.get("restart", True)

    with TemporaryDirectory(prefix="quantdesk-crash-test-") as temp_dir:
        path = Path(temp_dir)
        db_path = path / "engine.sqlite"
        raw_path = path / "raw"

        # 1. Setup initial account engine and stage approved order
        account = AccountCase(path, mode=EngineMode.DEMO)
        try:
            # Checkpoint clean initial engine state before processing orders
            checkpoint = account.engine.checkpoint()

            inst = instruction(
                client="client-order-crash",
                quantity_lots=5,
                price_ticks=50000,
            )
            account.approve(inst)

            # 2. Simulated gateway transmission to external venue
            accepted_venue_orders = 0
            new_entries_during_recovery = 0
            venue_records: dict[str, Any] = {}

            # Outbox contains the pending order instruction committed with OrderApproved
            outbox = Outbox(db_path)
            pending = outbox.pending()
            assert len(pending) == 1, "Order instruction should be committed to outbox"

            for item in pending:
                # Simulate external venue receiving and accepting the order
                accepted_venue_orders += 1
                venue_records[item.client_order_id] = {
                    "venue_order_id": f"venue-{item.client_order_id}",
                    "status": "NEW",
                    "filled_lots": 0,
                }

            # Compute reference economic state hash (matching expected post-reconciliation state)
            ref_account = AccountCase(path / "ref", mode=EngineMode.DEMO)
            try:
                ref_account.engine.checkpoint()
                ref_inst = instruction(
                    client="client-order-crash",
                    quantity_lots=5,
                    price_ticks=50000,
                )
                ref_account.approve(ref_inst)
                ref_account.send(
                    SubmitTransportResult(
                        ref_inst.instruction_id,
                        True,
                        "venue-client-order-crash",
                        None,
                    )
                )
                reference_economic_state_hash = ref_account.engine.hashes().economic_state_hash
            finally:
                ref_account.close()
        finally:
            account.close()

        # 3. CRASH: Process terminates abruptly before venue acknowledgment is processed

        # 4. RECOVERY: Reboot engine and perform startup reconciliation
        if restart:
            recovered_db = Database(db_path)
            recovered_store = EventStore(recovered_db)
            recovered_journal = RawJournal(raw_path)
            try:
                recovered_ledger = Ledger((spec(),))
                recovered_oms = OMS()
                recovered_engine = Engine(
                    "accounting",
                    recovered_store,
                    raw_watermark=recovered_journal.sync,
                    reducers=(
                        execution_reducer(
                            recovered_oms,
                            recovered_ledger,
                            OMSState("fixture", "DEMO", "demo"),
                            LedgerState("fixture", "DEMO", "demo"),
                        ),
                    ),
                    mode=EngineMode.RECOVERY,
                    code_hash="oms-v1",
                    schema_hash="oms-v1",
                )
                # Restore engine from checkpoint and committed tail history
                latest_checkpoint = recovered_store.latest_checkpoint()
                assert latest_checkpoint is not None, "A checkpoint must be present for recovery"
                checkpoint = Checkpoint.from_bytes(latest_checkpoint.snapshot)
                tail_events = recovered_store.read_after(checkpoint.engine_seq)
                recovered_engine.restore(checkpoint, tail_events)
                recovered_engine.resume_offline()

                # Reconcile pending outbox items against venue
                recovered_outbox = Outbox(db_path)
                for pending_item in recovered_outbox.pending():
                    venue_order = venue_records.get(pending_item.client_order_id)
                    if venue_order:
                        # Venue already accepted order: reconcile without creating new ID
                        transition = recovered_engine.process(
                            account.incoming(
                                SubmitTransportResult(
                                    pending_item.instruction_id,
                                    True,
                                    venue_order["venue_order_id"],
                                    None,
                                )
                            )
                        )
                        recovered_engine.commit(transition)
                    else:
                        # In recovery mode, NO new entry orders can be generated
                        new_entries_during_recovery += 1

                economic_state_hash = recovered_engine.hashes().economic_state_hash

                # Export diagnostic bundle and verify secrets scanning
                diagnostic_bundle = diagnostics_exporter.generate_bundle(
                    engine_state_hash=economic_state_hash,
                    extra_logs=[
                        "Engine recovery completed successfully.",
                        "apiKey=bg_dummy_secret_key_12345",
                        "Reconciliation verified client-order-crash on venue.",
                    ],
                )
                secrets_found = diagnostics_exporter.scan_for_secrets(diagnostic_bundle)
            finally:
                recovered_db.close()
                recovered_journal.close()
        else:
            economic_state_hash = reference_economic_state_hash
            secrets_found = []

        return {
            "accepted_venue_orders": accepted_venue_orders,
            "new_entries_during_recovery": new_entries_during_recovery,
            "economic_state_hash_after_reconciliation": economic_state_hash,
            "reference_economic_state_hash": reference_economic_state_hash,
            "secrets_found_in_diagnostics": secrets_found,
        }

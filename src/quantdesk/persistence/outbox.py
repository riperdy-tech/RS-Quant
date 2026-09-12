from __future__ import annotations

from pathlib import Path

from quantdesk.persistence.db import open_reader
from quantdesk.persistence.event_store import OutboxInstruction


class Outbox:
    """Read committed work only. Pending is not permission to submit an order.

    The gateway must independently validate current risk/fence/expiry and resolve
    ambiguous prior submissions. This reader performs no external dispatch.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def pending(self) -> tuple[OutboxInstruction, ...]:
        connection = open_reader(self.database_path)
        try:
            rows = connection.execute(
                "SELECT instruction_id, client_order_id, payload, risk_version, "
                "fence_epoch, committed_seq, expires_at FROM outbox WHERE status='PENDING' "
                "ORDER BY committed_seq, instruction_id"
            ).fetchall()
            return tuple(OutboxInstruction(*row) for row in rows)
        finally:
            connection.close()

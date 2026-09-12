"""Real account engine/OMS/ledger; transport alone is replaceable at the boundary."""

import json
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from quantdesk.core.engine import Engine
from quantdesk.core.events import (
    IncomingEvent,
    OrderInstruction,
    OrderReport,
    SubmitTransportResult,
)
from quantdesk.core.types import OrderType, Side, TimeInForce
from quantdesk.execution.intents import CancelRequested, OrderApproved
from quantdesk.execution.oms import OMS, execution_reducer
from quantdesk.execution.order_state import OMSState
from quantdesk.persistence.db import Database
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.raw_journal import RawJournal
from quantdesk.portfolio.ledger import Ledger, LedgerState
from tests.support.accounting_case import INSTRUMENT, envelope, fill, spec


def instruction(client="client", **changes):
    return replace(
        OrderInstruction(
            f"instruction-{client}",
            client,
            f"intent-{client}",
            "demo",
            "DEMO",
            INSTRUMENT,
            Side.BUY,
            5,
            10000,
            OrderType.LIMIT,
            TimeInForce.GTC,
            False,
            None,
            None,
            "strategy-a",
            None,
            10000,
            "risk-1",
            "1",
        ),
        **changes,
    )


class AccountCase:
    def __init__(self, path: Path):
        self.db = Database(path / "engine.sqlite")
        self.store = EventStore(self.db)
        self.journal = RawJournal(path / "raw")
        self.ledger = Ledger((spec(),))
        self.oms = OMS()
        self.engine = Engine(
            "accounting",
            self.store,
            raw_watermark=self.journal.sync,
            reducers=(
                execution_reducer(
                    self.oms,
                    self.ledger,
                    OMSState("fixture", "DEMO", "demo"),
                    LedgerState("fixture", "DEMO", "demo"),
                ),
            ),
            code_hash="oms-v1",
            schema_hash="oms-v1",
        )
        self.time = 0

    def incoming(self, payload):
        self.time += 1
        event = envelope(payload, self.time)
        return IncomingEvent(**{f.name: getattr(event, f.name) for f in fields(IncomingEvent)})

    def send(self, payload):
        transition = self.engine.process(self.incoming(payload))
        return self.engine.commit(transition)

    def approve(self, value=None):
        value = value or instruction()
        self.send(OrderApproved(value, Decimal("50"), Decimal("5")))
        return value

    @property
    def state(self):
        return OMSState.from_bytes(self.engine.state.get("oms", "state-v1"))

    @property
    def portfolio(self):
        return LedgerState.from_bytes(self.engine.state.get("ledger", "state-v1"))

    def close(self):
        self.db.close()
        self.journal.close()


def partial_fill_cancel_race_case(**overrides):
    fixture = json.loads(
        Path("fixtures/market_scenarios/partial_fill_cancel_race.json").read_text()
    )
    with TemporaryDirectory() as directory:
        case = AccountCase(Path(directory))
        try:
            lots = int(overrides.get("order_lots", fixture["order_lots"]))
            executed = int(overrides.get("fill_lots", fixture["fill_lots"]))
            value = case.approve(instruction(quantity_lots=lots))
            case.send(SubmitTransportResult(value.instruction_id, True, "venue-order", None))
            case.send(CancelRequested("cancel-1", "client", 10000, "risk-1", "1"))
            for _ in range(int(overrides.get("duplicates", fixture["duplicates"]))):
                case.send(fill(fixture["execution_id"], lots=executed))
            case.send(OrderReport("client", fixture["venue_order_id"], "CANCELED", executed, 0))
            order = case.state.order("client")
            return {
                "lifecycle": order.lifecycle.value,
                "accounted_fill_lots": order.accounted_fill_lots,
                "canceled_remainder_lots": order.canceled_remainder_lots,
                "ledger_execution_count": len(case.portfolio.transactions),
            }
        finally:
            case.close()

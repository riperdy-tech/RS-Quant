import json
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from quantdesk.core.events import (
    CashTransfer,
    Envelope,
    ExecutionReport,
    FundingSettlement,
    MarkPrice,
    canonical_bytes,
)
from quantdesk.core.types import EventPayload, ExecutionType, Side
from quantdesk.portfolio.arithmetic import display_amount
from quantdesk.portfolio.ledger import Ledger, LedgerState
from quantdesk.venues.instruments import InstrumentSpec

INSTRUMENT = "fixture:USDT-FUTURES:TEST:USDT:USDT:TESTUSDT"


def spec(**changes: object) -> InstrumentSpec:
    return replace(
        InstrumentSpec.create(
            instrument_id=INSTRUMENT,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal(1),
            max_leverage=Decimal(20),
        ),
        **changes,
    )


def envelope(payload: EventPayload, seq: int = 1, instrument: str | None = INSTRUMENT) -> Envelope:
    return Envelope(
        type(payload).__name__,
        1,
        "accounting",
        "demo",
        "fixture",
        "DEMO",
        instrument,
        "fixture",
        "epoch-1",
        None,
        None,
        None,
        None,
        seq,
        seq,
        seq,
        None,
        f"report-{seq}",
        None,
        "accounting-test-v1",
        canonical_bytes(payload),
        f"report-{seq}",
        seq,
    )


def fill(
    native: str,
    side: Side = Side.BUY,
    lots: int = 1,
    price: str = "100",
    fee: str = "0",
    asset: str = "USDT",
) -> ExecutionReport:
    return ExecutionReport(
        native,
        "client",
        "venue-order",
        side,
        lots,
        Decimal(price),
        0,
        0,
        Decimal(fee),
        asset,
        None,
        None,
        ExecutionType.TRADE,
        None,
    )


def accounting_worked_example_case(**overrides: object) -> dict[str, object]:
    fixture = json.loads(Path("fixtures/accounting/worked_example.json").read_text())
    ledger = Ledger((spec(),))
    state = LedgerState("fixture", "DEMO", "demo")
    inputs: list[EventPayload] = [
        CashTransfer("deposit", Decimal(fixture["deposit"]), "USDT", "IN", 0)
    ]
    inputs.extend(
        fill(row["native_id"], Side(row["side"]), row["lots"], row["price"], row["fee"])
        for row in fixture["fills"]
    )
    inputs += [
        FundingSettlement("funding-1", INSTRUMENT, Decimal(fixture["funding"]), "USDT", 0),
        MarkPrice(Decimal(fixture["mark"]), 0, "synthetic"),
    ]
    transactions = []
    seq = 0
    for payload in inputs:
        for _ in range(2 if overrides.get("duplicate_every_report") else 1):
            seq += 1
            change = ledger.apply(envelope(payload, seq), state)
            state = change.state
            transactions.extend(change.transactions)
    view = ledger.snapshot(state)
    assert view.equity is not None
    return {
        "position_base_qty": str(state.position(INSTRUMENT).base_quantity),
        "equity_display_usdt": display_amount(view.equity),
        "total_fees_usdt": str(view.total_fees),
        "unbalanced_transactions": sum(
            any(
                sum((Fraction(p.amount) for p in tx.postings if p.asset == asset), Fraction()) != 0
                for asset in {p.asset for p in tx.postings}
            )
            for tx in transactions
        ),
    }

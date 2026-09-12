from decimal import Decimal
from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

from quantdesk.core.events import CashTransfer, MarkPrice
from quantdesk.core.types import Side
from quantdesk.portfolio.ledger import Ledger, LedgerState
from tests.support.accounting_case import INSTRUMENT, envelope, fill, spec


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(-10, 10), st.integers(1, 10000), st.integers(-20, 100)),
        min_size=1,
        max_size=50,
    )
)
def test_random_fills_transfers_balance_dedupe_and_restart(
    steps: list[tuple[int, int, int]],
) -> None:
    ledger = Ledger((spec(),))
    state = LedgerState("fixture", "DEMO", "demo")
    restarted = state
    expected_lots = 0
    sequence = 0
    for index, (lots, price, fee_cents) in enumerate(steps):
        sequence += 1
        payload = (
            fill(
                str(index),
                Side.BUY if lots > 0 else Side.SELL,
                abs(lots),
                str(price),
                str(Decimal(fee_cents) / 100),
            )
            if lots
            else CashTransfer(str(index), Decimal(price), "USDT", "IN", sequence)
        )
        event = envelope(payload, sequence)
        change = ledger.apply(event, state)
        expected_lots += lots
        state = change.state
        restarted = ledger.apply(event, LedgerState.from_bytes(restarted.to_bytes())).state
        assert state == restarted
        assert state.position(INSTRUMENT).signed_lots == expected_lots
        for transaction in change.transactions:
            for asset in {posting.asset for posting in transaction.postings}:
                assert (
                    sum(
                        (
                            Fraction(posting.amount)
                            for posting in transaction.postings
                            if posting.asset == asset
                        ),
                        Fraction(),
                    )
                    == 0
                )
        repeated = ledger.apply(event, state)
        assert repeated.duplicate and repeated.state == state and repeated.transactions == ()
        sequence += 1
        marked = envelope(MarkPrice(Decimal(price), sequence, "property-fixture"), sequence)
        state = ledger.apply(marked, state).state
        restarted = ledger.apply(marked, restarted).state
        view = ledger.snapshot(state)
        assert view.equity is not None and view.equity.is_finite()
        assert view.net_pnl is not None and view.net_pnl.is_finite()

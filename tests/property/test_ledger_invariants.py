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


@settings(max_examples=100)
@given(
    st.integers(-3, 3),
    st.lists(st.tuples(st.booleans(), st.integers(1, 3), st.booleans()), max_size=4),
)
def test_margin_extrema_match_all_valid_partial_fill_orderings(
    initial_lots: int, orders: list[tuple[bool, int, bool]]
) -> None:
    from quantdesk.portfolio.events import ReservationChanged
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger = Ledger((spec(),))
    state = LedgerState("fixture", "DEMO", "demo")
    if initial_lots:
        state = ledger.apply(
            envelope(
                fill("initial", Side.BUY if initial_lots > 0 else Side.SELL, abs(initial_lots))
            ),
            state,
        ).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 2, "fixture"), 2), state).state
    pending = tuple(
        ReservationChanged(
            str(index),
            INSTRUMENT,
            Side.BUY if buy else Side.SELL,
            lots,
            Decimal(0),
            Decimal(0),
            reduce_only,
            1,
        )
        for index, (buy, lots, reduce_only) in enumerate(orders)
    )
    # Independent oracle: explore every feasible one-lot fill and stopping point.
    first = (initial_lots, tuple(lots for _, lots, _ in orders))
    frontier = [first]
    reached = {first}
    while frontier:
        current, remaining = frontier.pop()
        for index, (buy, _, reduce_only) in enumerate(orders):
            side = 1 if buy else -1
            if not remaining[index] or (reduce_only and current * side >= 0):
                continue
            tail = tuple(lots - (offset == index) for offset, lots in enumerate(remaining))
            next_state = (current + side, tail)
            if next_state not in reached:
                reached.add(next_state)
                frontier.append(next_state)
    model = MarginSpec(
        spec(),
        Decimal(1),
        Decimal(0),
        (MarginTier(Decimal(100000), Decimal("0.01"), Decimal(0)),),
        "tier",
        2,
        10,
    )
    estimate = Margin.estimate(ledger.snapshot(state), pending, model)
    assert estimate.worst_long_lots == max(lots for lots, _ in reached)
    assert estimate.worst_short_lots == min(lots for lots, _ in reached)

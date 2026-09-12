from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from quantdesk.core.events import OrderReport
from quantdesk.execution.intents import OrderApproved
from quantdesk.execution.oms import OMS
from quantdesk.execution.order_state import Lifecycle, OMSState
from quantdesk.portfolio.ledger import Ledger, LedgerState
from tests.support.accounting_case import INSTRUMENT, envelope, fill, spec
from tests.support.oms_case import instruction


@given(st.lists(st.integers(min_value=0, max_value=4), min_size=1, max_size=40))
def test_random_duplicates_reorderings_preserve_unique_late_economic_facts(ordering):
    oms, ledger = OMS(), Ledger((spec(),))
    state = OMSState("fixture", "DEMO", "demo")
    portfolio = LedgerState("fixture", "DEMO", "demo")
    state = oms.approve(
        OrderApproved(instruction(), Decimal(50), Decimal(5)), state, portfolio, 1
    ).state
    facts = (
        fill("a", lots=2),
        fill("b", lots=1),
        OrderReport("client", "venue-order", "CANCELED", 3, 0),
        OrderReport("client", "venue-order", "OPEN", 0, 0),
        OrderReport("client", "venue-order", "PARTIALLY_FILLED", 2, 0),
    )
    # Every permutation prefix is followed by the complete fact set, in arbitrary receipt order.
    for sequence, index in enumerate([*ordering, 2, 0, 3, 1, 4], 2):
        fact = facts[index]
        event = envelope(fact, sequence)
        state = oms.apply(event, state).state
        if index in (0, 1):
            portfolio = ledger.apply(event, portfolio).state
    order = state.order("client")
    assert order.lifecycle == Lifecycle.CANCELED
    assert order.accounted_fill_lots == 3 and order.canceled_remainder_lots == 2
    assert order.reserved_lots == 0
    assert portfolio.position(INSTRUMENT).signed_lots == 3
    assert len(portfolio.transactions) == 2
    assert OMSState.from_bytes(state.to_bytes()) == state

"""Composition adapter; the engine still owns ordering, transaction, and recovery."""

from dataclasses import replace

from quantdesk.core.checkpoint import EngineState
from quantdesk.core.events import Envelope
from quantdesk.core.reducers import FactReducer, Reduction, Stage
from quantdesk.portfolio.ledger import Ledger, LedgerState

_ACCOUNTING_EVENTS = frozenset(
    {
        "ExecutionReport",
        "FundingSettlement",
        "FeeAdjustment",
        "CashTransfer",
        "LedgerAdjustmentApproved",
        "FinancialEventObserved",
        "MarkPrice",
        "FundingRateAnnounced",
        "ReservationChanged",
        "ConversionRateObserved",
        "TimerFired",
    }
)
_PUBLIC_OBSERVATIONS = frozenset({"MarkPrice", "FundingRateAnnounced", "TimerFired"})


def accounting_reducer(ledger: Ledger, initial_state: LedgerState) -> FactReducer:
    def apply(event: Envelope, state: EngineState) -> Reduction:
        data = state.get("ledger", "state-v1", b"")
        portfolio = LedgerState.from_bytes(data) if data else initial_state
        if event.event_type not in _ACCOUNTING_EVENTS:
            advanced = replace(portfolio, available_ns=event.available_ns)
            return Reduction(state.put("ledger", "state-v1", advanced.to_bytes()))
        accounted_event = event
        if event.account_id is None and event.event_type in _PUBLIC_OBSERVATIONS:
            accounted_event = replace(
                event, account_id=portfolio.account, venue=event.venue or portfolio.venue
            )
        change = ledger.apply(accounted_event, portfolio)
        return Reduction(
            state.put("ledger", "state-v1", change.state.to_bytes()),
            ledger_transactions=change.transactions,
            projection_updates=change.projections(),
            economic_aliases=change.alias_updates,
        )

    return FactReducer("accounting-v1", Stage.BOOK_ACCOUNT_OMS, apply)

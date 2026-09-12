from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from quantdesk.core.events import (
    AccountSnapshotObserved,
    CashTransfer,
    FeeAdjustment,
    FundingRateAnnounced,
    FundingSettlement,
    LedgerAdjustmentApproved,
    MarkPrice,
    TimerFired,
)
from quantdesk.core.types import Side
from quantdesk.persistence.db import Database
from quantdesk.persistence.event_store import EventRecord, EventStore, PersistenceTransition
from quantdesk.persistence.raw_journal import RawJournal
from quantdesk.portfolio.ledger import FinancialEvent, Ledger, LedgerState
from tests.support.accounting_case import INSTRUMENT, envelope, fill, spec
from tests.support.cases import CaseDriver


def start() -> tuple[Ledger, LedgerState]:
    ledger = Ledger((spec(),), mark_max_age_ns=10)
    state = ledger.apply(
        envelope(CashTransfer("deposit", Decimal(1000), "USDT", "IN", 0)),
        LedgerState("fixture", "DEMO", "demo"),
    ).state
    return ledger, state


def test_ledger_worked_example_and_duplicates(case: CaseDriver) -> None:
    result = case("accounting_worked_example", duplicate_every_report=True)
    assert result["position_base_qty"] == "2"
    assert result["equity_display_usdt"] == "1039.53"
    assert result["total_fees_usdt"] == "0.43"
    assert result["unbalanced_transactions"] == 0


@pytest.mark.parametrize(
    "side, close_price, realized, remainder",
    [
        (Side.BUY, "110", "10", 1),
        (Side.SELL, "90", "10", -1),
        (Side.SELL, "110", "-10", -1),
    ],
)
def test_partial_close_retains_average_and_full_close_clears_it(
    side: Side, close_price: str, realized: str, remainder: int
) -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("open", side, 2), 2), state).state
    opposite = Side.SELL if side == Side.BUY else Side.BUY
    change = ledger.apply(envelope(fill("close", opposite, 1, close_price), 3), state)
    assert change.state.position(INSTRUMENT).signed_lots == remainder
    assert change.state.position(INSTRUMENT).average_entry == Decimal(100)
    assert ledger.snapshot(change.state).realized_pnl == Decimal(realized)
    flat = ledger.apply(envelope(fill("flat", opposite), 4), change.state).state
    assert flat.position(INSTRUMENT).base_quantity == 0
    assert flat.position(INSTRUMENT).average_entry is None
    assert ledger.snapshot(flat).unrealized_pnl == 0


def test_external_reversal_has_close_and_reopen_legs() -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("open", Side.SELL, 2), 2), state).state
    change = ledger.apply(envelope(fill("reversal", Side.BUY, 3, "90", "-0.03"), 3), state)
    assert [(leg.kind, leg.signed_lots) for leg in change.position_legs] == [
        ("CLOSE", 2),
        ("OPEN", 1),
    ]
    assert change.state.position(INSTRUMENT).average_entry == 90
    assert change.state.balance("cash:USDT") == Decimal("1020.03")
    assert ledger.snapshot(change.state).total_fees == Decimal("-0.03")


def test_marks_expire_and_delayed_older_marks_do_not_replace_fresher_ones() -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("open"), 2), state).state
    assert ledger.snapshot(state).equity is None
    state = ledger.apply(envelope(MarkPrice(Decimal(105), 3, "fixture"), 3), state).state
    assert ledger.snapshot(state).equity == 1005
    state = ledger.apply(envelope(MarkPrice(Decimal(999), 2, "delayed"), 4), state).state
    assert ledger.snapshot(state).equity == 1005
    state = ledger.apply(envelope(TimerFired("age", 15, "report-3", 15), 15), state).state
    assert ledger.snapshot(state).equity is None
    assert ledger.snapshot(state).net_pnl is None


def test_native_fees_and_cash_do_not_lose_precision_to_ambient_context() -> None:
    ledger, state = start()
    with localcontext() as context:
        context.prec = 6
        context.rounding = "ROUND_UP"
        state = ledger.apply(
            envelope(
                fill("tiny", fee="0.000000000000000000000000000000000000000000000000000000000001"),
                2,
            ),
            state,
        ).state
    assert state.balance("cash:USDT") == Decimal(
        "999.999999999999999999999999999999999999999999999999999999999999"
    )
    assert state.balance("expense:trading_fees") == Decimal("1e-60")
    restored = LedgerState.from_bytes(state.to_bytes())
    assert restored == state


def test_fee_correction_is_a_linked_difference_and_duplicates_survive_restart() -> None:
    ledger, state = start()
    first = ledger.apply(envelope(fill("fill", fee="0.20"), 2), state)
    adjustment = FeeAdjustment("correction", "fill", Decimal("-0.05"), "USDT", "maker correction")
    change = ledger.apply(envelope(adjustment, 3), first.state)
    assert change.transactions[0].reversal_of == first.transactions[0].transaction_id
    assert change.state.balance("cash:USDT") == Decimal("999.85")
    state = LedgerState.from_bytes(change.state.to_bytes())
    repeated = ledger.apply(envelope(adjustment, 4), state)
    assert repeated.duplicate and repeated.transactions == ()
    assert ledger.snapshot(repeated.state).total_fees == Decimal("0.15")
    with pytest.raises(ValueError, match="execution"):
        ledger.apply(
            envelope(
                replace(adjustment, native_transaction_id="missing", native_execution_id="unknown"),
                5,
            ),
            state,
        )


def test_funding_announcements_do_not_post_and_settlements_have_account_wide_dedup() -> None:
    from quantdesk.portfolio.pnl import funding_cash_change

    assert funding_cash_change(Decimal(2), Decimal(100), Decimal("0.001")) == Decimal("-0.2")
    assert funding_cash_change(Decimal(-2), Decimal(100), Decimal("0.001")) == Decimal("0.2")
    ledger, state = start()
    announced = ledger.apply(envelope(FundingRateAnnounced(Decimal("0.001"), 5), 2), state)
    assert announced.transactions == ()
    funding = FundingSettlement("native-funding", INSTRUMENT, Decimal("-0.04"), "USDT", 3)
    change = ledger.apply(FinancialEvent(envelope(funding, 3), ("account-row-1",)), announced.state)
    duplicate = ledger.apply(
        envelope(replace(funding, native_transaction_id="account-row-1"), 4), change.state
    )
    assert duplicate.duplicate
    assert duplicate.state.balance("cash:USDT") == Decimal("999.96")
    assert ledger.snapshot(duplicate.state).funding_received == Decimal("-0.04")


def test_transfers_are_not_strategy_pnl_and_approved_adjustments_keep_sources() -> None:
    ledger, state = start()
    state = ledger.apply(
        envelope(CashTransfer("withdrawal", Decimal(500), "USDT", "OUT", 2), 2), state
    ).state
    assert ledger.snapshot(state).net_pnl == 0
    assert ledger.snapshot(state).external_flow == 500
    adjustment = LedgerAdjustmentApproved(
        "adjustment-1", Decimal(2), "USDT", "confirmed correction", "operator"
    )
    with pytest.raises(ValueError, match="observation"):
        ledger.apply(envelope(adjustment, 3), state)
    change = ledger.apply(
        FinancialEvent(
            envelope(adjustment, 3), source_observation_ids=("snapshot-1",), trading_adjustment=True
        ),
        state,
    )
    assert change.transactions[0].approved_by == "operator"
    assert ledger.snapshot(change.state).net_pnl == 2
    assert ledger.snapshot(change.state).equity == 502


def test_nonsettlement_fee_needs_fresh_conversion_and_blocks_supported_profile() -> None:
    from quantdesk.portfolio.events import ConversionRateObserved

    ledger, state = start()
    state = ledger.apply(envelope(fill("foreign-fee", fee="0.5", asset="BNB"), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    assert state.balance("expense:trading_fees", "BNB") == Decimal("0.5")
    assert ledger.snapshot(state).total_fees is None
    assert ledger.snapshot(state).equity is None
    conversion = ConversionRateObserved("BNB", "USDT", Decimal(200), 4, "fixture")
    state = ledger.apply(envelope(conversion, 4), state).state
    assert ledger.snapshot(state).total_fees == 100
    assert ledger.snapshot(state).equity == 900
    assert "UNSUPPORTED_FEE_CURRENCY:BNB" in ledger.snapshot(state).profile_blockers


def test_reservations_encumber_cash_and_closing_quantity_without_postings() -> None:
    from quantdesk.portfolio.events import ReservationChanged

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", lots=3), 2), state).state
    reservation = ReservationChanged(
        "order-1", INSTRUMENT, Side.BUY, 2, Decimal(20), Decimal("0.1"), False, 1
    )
    change = ledger.apply(envelope(reservation, 3), state)
    assert change.transactions == () and change.state.balance("cash:USDT") == 1000
    assert ledger.snapshot(change.state).reserved_cash == Decimal("20.1")
    reduced = replace(
        reservation,
        remaining_lots=1,
        cash_amount=Decimal(10),
        fee_buffer=Decimal("0.05"),
        revision=2,
    )
    state = ledger.apply(envelope(reduced, 4), change.state).state
    assert ledger.snapshot(state).reserved_cash == Decimal("10.05")
    state = ledger.apply(
        envelope(
            replace(
                reservation,
                reservation_id="close-1",
                side=Side.SELL,
                remaining_lots=2,
                cash_amount=Decimal(0),
                reduce_only=True,
            ),
            5,
        ),
        state,
    ).state
    with pytest.raises(ValueError, match="closing"):
        ledger.apply(
            envelope(
                replace(
                    reservation,
                    reservation_id="close-2",
                    side=Side.SELL,
                    remaining_lots=2,
                    cash_amount=Decimal(0),
                    reduce_only=True,
                ),
                6,
            ),
            state,
        )
    with pytest.raises(ValueError, match="revision"):
        ledger.apply(envelope(reservation, 7), state)
    released = replace(
        reduced, remaining_lots=0, cash_amount=Decimal(0), fee_buffer=Decimal(0), revision=3
    )
    state = ledger.apply(envelope(released, 8), state).state
    assert LedgerState.from_bytes(state.to_bytes()) == state
    assert all(r.reservation_id != "order-1" or r.remaining_lots == 0 for r in state.reservations)


def test_reconciliation_observations_never_overwrite_ledger() -> None:
    from quantdesk.portfolio.reconciliation import Reconciler

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", lots=2), 2), state).state
    before = state.to_bytes()
    observed = AccountSnapshotObserved(
        "snapshot-1", (("USDT", Decimal("1000.02")),), ((INSTRUMENT, 3),), 3
    )
    result = Reconciler.compare(state, observed, 2, 4, Decimal("0.01"))
    assert not result.observation.converged
    assert result.position_discrepancies[0].expected_lots == 2
    assert result.position_discrepancies[0].observed_lots == 3
    assert result.balance_discrepancies[0].difference == Decimal("0.02")
    assert state.to_bytes() == before
    matched = replace(
        observed, wallet_balances=(("USDT", Decimal("1000.01")),), positions=((INSTRUMENT, 2),)
    )
    assert Reconciler.compare(state, matched, 2, 4, Decimal("0.01")).observation.converged


def test_margin_uses_worst_reachable_exposure_and_versioned_tiers() -> None:
    from quantdesk.portfolio.events import ReservationChanged
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", lots=2), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    pending = (
        ReservationChanged("buy", INSTRUMENT, Side.BUY, 3, Decimal(0), Decimal("0.1"), False, 1),
        ReservationChanged("sell", INSTRUMENT, Side.SELL, 3, Decimal(0), Decimal("0.1"), False, 1),
    )
    model = MarginSpec(
        spec(),
        Decimal(5),
        Decimal("0.5"),
        (MarginTier(Decimal(1000), Decimal("0.01"), Decimal(0)),),
        "tier-v1",
        3,
        10,
    )
    estimate = Margin.estimate(ledger.snapshot(state), pending, model)
    assert estimate.initial_margin == Decimal("100.7")
    assert estimate.maintenance_margin == Decimal(5)
    assert estimate.worst_long_lots == 5 and estimate.worst_short_lots == -1
    assert estimate.source == "LOCAL_ESTIMATE"
    missing = Margin.estimate(ledger.snapshot(state), pending, replace(model, tiers=()))
    assert missing.initial_margin is None and missing.maintenance_margin is None
    assert missing.status == "INCOMPLETE_MARGIN_MODEL"
    assert (
        Margin.estimate(
            ledger.snapshot(state), pending, replace(model, observed_ns=0, max_age_ns=1)
        ).status
        == "INCOMPLETE_MARGIN_MODEL"
    )
    assert (
        Margin.estimate(
            ledger.snapshot(state), pending, replace(model, require_venue_bounds=True)
        ).status
        == "INCOMPLETE_MARGIN_MODEL"
    )


def test_real_transactions_projection_restart_and_late_alias_binding_are_atomic(
    tmp_path: Path,
) -> None:
    ledger = Ledger((spec(),))
    state = LedgerState("fixture", "DEMO", "demo")
    path = tmp_path / "engine.sqlite"
    with RawJournal(tmp_path / "raw") as journal, Database(path) as db:
        store = EventStore(db)
        event = envelope(fill("stream-fill", fee="0.2"))
        change = ledger.apply(event, state)
        store.commit(
            PersistenceTransition(
                event.event_id,
                0,
                (EventRecord(event, "INPUT"),),
                change.transactions,
                projection_updates=change.projections(),
            ),
            journal.sync(),
        )
        later = replace(event, event_id="alias-discovery", engine_seq=2, available_ns=2)
        later = FinancialEvent(later, ("rest-fill",)).recorded_envelope()
        aliased = ledger.apply(later, change.state)
        assert aliased.duplicate and len(aliased.alias_updates) == 1
        store.commit(
            PersistenceTransition(
                later.event_id,
                1,
                (EventRecord(later, "INPUT"),),
                projection_updates=aliased.projections(),
                economic_aliases=aliased.alias_updates,
            ),
            journal.sync(),
        )
        assert len(store.postings(change.transactions[0].transaction_id)) == 4
        assert db.connection.execute("SELECT COUNT(*) FROM economic_identities").fetchone()[0] == 2
    with Database(path) as db:
        projection = EventStore(db).projection("balances", "ledger-state-v1")
        assert projection is not None
        restored = LedgerState.from_bytes(projection[0])
        duplicate = ledger.apply(envelope(fill("rest-fill", fee="0.2"), 3), restored)
        assert duplicate.duplicate and duplicate.transactions == ()
        assert duplicate.state.balance("cash:USDT") == Decimal("-0.2")


def test_decimal_spelling_does_not_change_economic_identity_and_conflicts_fail() -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("one", price="100.0", fee="0.20"), 2), state).state
    repeated = ledger.apply(envelope(fill("one", price="100", fee="0.2"), 3), state)
    assert repeated.duplicate
    with pytest.raises(ValueError, match="conflicting"):
        ledger.apply(envelope(fill("one", price="101", fee="0.2"), 3), state)


def test_financial_alias_and_adjustment_evidence_can_replay_from_canonical_inputs() -> None:
    ledger, state = start()
    original = ledger.apply(envelope(fill("one", fee="0.20"), 2), state)
    fact = FinancialEvent(envelope(fill("one", fee="0.20"), 3), ("rest-one",))
    change = ledger.apply(fact, original.state)
    replayed = ledger.apply(
        fact.recorded_envelope(), LedgerState.from_bytes(original.state.to_bytes())
    )
    assert replayed == change
    adjustment = FinancialEvent(
        envelope(LedgerAdjustmentApproved("adj", Decimal(1), "USDT", "verified", "operator"), 4),
        source_observation_ids=("snapshot-1",),
        trading_adjustment=True,
    )
    assert ledger.apply(adjustment, change.state) == ledger.apply(
        adjustment.recorded_envelope(), change.state
    )


@pytest.mark.parametrize(
    "field,value", [("price", 1.5), ("executed_lots", 1.5), ("fee_amount", 0.1)]
)
def test_financial_json_floats_rejected(field: str, value: float) -> None:
    import json

    from quantdesk.core.events import canonical_bytes

    ledger, state = start()
    event = envelope(fill("bad"), 2)
    payload = json.loads(event.payload)
    payload[field] = value
    with pytest.raises(ValueError):
        ledger.apply(replace(event, payload=canonical_bytes(payload)), state)


def test_margin_can_value_pending_entry_from_flat_and_reject_unsupported_inputs() -> None:
    from quantdesk.portfolio.events import ReservationChanged
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger, state = start()
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 2, "fixture"), 2), state).state
    pending = (
        ReservationChanged("entry", INSTRUMENT, Side.BUY, 2, Decimal(0), Decimal("0.1"), False, 1),
    )
    model = MarginSpec(
        spec(),
        Decimal(1),
        Decimal(0),
        (MarginTier(Decimal(1000), Decimal("0.01"), Decimal(0)),),
        "tier-v1",
        2,
        10,
    )
    view = Margin.estimate(ledger.snapshot(state), pending, model)
    assert view.initial_margin == Decimal("200.1")
    with pytest.raises(ValueError, match="leverage"):
        Margin.estimate(ledger.snapshot(state), pending, replace(model, leverage=Decimal(1000)))


def test_persistence_alias_conflict_rolls_back_current_event_and_projection(tmp_path: Path) -> None:
    import sqlite3

    from quantdesk.persistence.event_store import EconomicAliasUpdate

    ledger, state = start()
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        event = envelope(fill("one"), 1)
        change = ledger.apply(event, replace(state, available_ns=0))
        store = EventStore(db)
        store.commit(
            PersistenceTransition(
                event.event_id, 0, (EventRecord(event, "INPUT"),), change.transactions
            ),
            journal.sync(),
        )
        transaction = change.transactions[0]
        observation = envelope(fill("one"), 2)
        duplicate_alias = EconomicAliasUpdate(
            observation.event_id, transaction.transaction_id, transaction.identity
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.commit(
                PersistenceTransition(
                    observation.event_id,
                    1,
                    (EventRecord(observation, "INPUT"),),
                    economic_aliases=(duplicate_alias,),
                ),
                journal.sync(),
            )
        assert len(store.read_after(0)) == 1
        foreign = replace(
            duplicate_alias,
            identity=replace(transaction.identity, native_id="other", instrument="foreign"),
        )
        with pytest.raises(ValueError, match="scope"):
            store.commit(
                PersistenceTransition(
                    observation.event_id,
                    1,
                    (EventRecord(observation, "INPUT"),),
                    economic_aliases=(foreign,),
                ),
                journal.sync(),
            )
        assert store.state_watermark() == (1, 1)


def test_real_engine_commits_and_recovers_ledger_and_aliases(tmp_path: Path) -> None:
    from dataclasses import fields

    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.core.events import IncomingEvent
    from quantdesk.portfolio.reducer import accounting_reducer

    ledger = Ledger((spec(),))
    initial = LedgerState("fixture", "DEMO", "demo")
    path = tmp_path / "engine.sqlite"
    with RawJournal(tmp_path / "raw") as journal, Database(path) as db:
        engine = Engine(
            "accounting",
            EventStore(db),
            raw_watermark=lambda: journal.durable_watermark,
            reducers=(accounting_reducer(ledger, initial),),
            code_hash="ledger-v1",
            schema_hash="ledger-v1",
        )
        for event in (
            envelope(fill("stream", fee="0.2")),
            FinancialEvent(envelope(fill("stream", fee="0.2"), 2), ("rest",)).recorded_envelope(),
        ):
            incoming = IncomingEvent(
                **{f.name: getattr(event, f.name) for f in fields(IncomingEvent)}
            )
            engine.commit(engine.process(incoming))
        final = engine.state
        events = engine.store.read_after(0)
        assert db.connection.execute("SELECT COUNT(*) FROM economic_identities").fetchone()[0] == 2
        checkpoint = engine.checkpoint()
    with RawJournal(tmp_path / "raw") as journal, Database(path) as db:
        recovered = Engine(
            "accounting",
            EventStore(db),
            raw_watermark=lambda: journal.durable_watermark,
            reducers=(accounting_reducer(ledger, initial),),
            code_hash="ledger-v1",
            schema_hash="ledger-v1",
            mode=EngineMode.RECOVERY,
        )
        recovered.restore(checkpoint, ())
        assert recovered.state.ledger == final.ledger
        fresh = initial
        for record in events:
            fresh = ledger.apply(record.envelope, fresh).state
        assert fresh == LedgerState.from_bytes(final.get("ledger", "state-v1"))


def test_reconciliation_missing_balance_is_unavailable_even_when_local_balance_zero() -> None:
    from quantdesk.portfolio.reconciliation import Reconciler

    state = LedgerState("fixture", "DEMO", "demo")
    observed = AccountSnapshotObserved("missing-assets", (), (), 1)
    result = Reconciler.compare(state, observed, 1, 2, Decimal("0.01"))
    assert not result.observation.converged
    assert result.balance_discrepancies[0].observed is None


def test_exact_worked_average_is_not_rounded_to_an_executable_tick() -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("first", lots=2, price="100", fee="0.20"), 2), state).state
    state = ledger.apply(envelope(fill("second", price="110", fee="0.11"), 3), state).state
    assert state.position(INSTRUMENT).average_entry == Decimal("103." + "3" * 47)
    state = ledger.apply(
        envelope(fill("third", Side.SELL, price="120", fee="0.12"), 4), state
    ).state
    assert ledger.snapshot(state).realized_pnl == Decimal("16." + "6" * 46 + "7")
    assert state.position(INSTRUMENT).average_entry == Decimal("103." + "3" * 47)


def test_fill_uses_contract_multiplier_and_point_in_time_spec() -> None:
    from quantdesk.venues.instruments import InstrumentSpec

    historic = InstrumentSpec.create(
        instrument_id=INSTRUMENT,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.1"),
        contract_multiplier=Decimal("0.01"),
    )
    future = InstrumentSpec.create(
        instrument_id=INSTRUMENT,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.2"),
        contract_multiplier=Decimal("0.01"),
        valid_from_ns=10,
        known_from_ns=10,
    )
    ledger = Ledger((historic, future))
    state = ledger.apply(
        envelope(fill("first", lots=3), 1), LedgerState("fixture", "DEMO", "demo")
    ).state
    assert state.position(INSTRUMENT).base_quantity == Decimal("0.003")
    with pytest.raises(ValueError, match="unit revision"):
        ledger.apply(envelope(replace(fill("new", lots=2), event_ns=10), 10), state)


def test_missing_conversion_cannot_be_reused_after_restart_and_expiry() -> None:
    from quantdesk.portfolio.events import ConversionRateObserved

    ledger, state = start()
    state = ledger.apply(
        envelope(CashTransfer("foreign", Decimal(1), "BNB", "IN", 2), 2), state
    ).state
    state = ledger.apply(
        envelope(ConversionRateObserved("BNB", "USDT", Decimal(100), 3, "fixture"), 3), state
    ).state
    assert ledger.snapshot(state).equity == 1100
    state = ledger.apply(
        envelope(TimerFired("expiry", 14, "report-3", 14), 14),
        LedgerState.from_bytes(state.to_bytes()),
    ).state
    assert ledger.snapshot(state).equity is None


def test_margin_rejects_stale_supplied_venue_collateral_even_when_not_required() -> None:
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger, state = start()
    state = ledger.apply(envelope(fill("open"), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 20, "fixture"), 20), state).state
    model = MarginSpec(
        spec(),
        Decimal(1),
        Decimal(0),
        (MarginTier(Decimal(1000), Decimal("0.01"), Decimal(0)),),
        "tier-v1",
        20,
        10,
        Decimal(1000),
        0,
    )
    assert Margin.estimate(ledger.snapshot(state), (), model).status == "INCOMPLETE_MARGIN_MODEL"


def test_native_realized_pnl_is_observation_not_authoritative_cash() -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("open"), 2), state).state
    external = replace(fill("close", Side.SELL, price="90"), native_realized_pnl=Decimal(999))
    state = ledger.apply(envelope(external, 3), state).state
    assert state.balance("cash:USDT") == 990
    assert ledger.snapshot(state).realized_pnl == -10


def test_fee_aliases_cannot_merge_two_previously_distinct_executions() -> None:
    ledger, state = start()
    state = ledger.apply(envelope(fill("one"), 2), state).state
    state = ledger.apply(envelope(fill("two"), 3), state).state
    with pytest.raises(ValueError, match="conflicting"):
        ledger.apply(FinancialEvent(envelope(fill("one"), 4), ("two",)), state)


def test_direct_financial_wrapper_rejects_nontyped_adjustment_metadata() -> None:
    ledger, state = start()
    fact = FinancialEvent(
        envelope(LedgerAdjustmentApproved("adj", Decimal(1), "USDT", "verified", "operator"), 2),
        source_observation_ids=("observation",),
        trading_adjustment="yes",
    )  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        ledger.apply(fact, state)


def test_nonsettlement_funding_alias_scope_cannot_change_in_persistence(tmp_path: Path) -> None:
    from quantdesk.persistence.event_store import EconomicAliasUpdate

    ledger = Ledger((spec(),))
    event = envelope(FundingSettlement("fund", INSTRUMENT, Decimal("-0.1"), "USDT", 1))
    change = ledger.apply(event, LedgerState("fixture", "DEMO", "demo"))
    transaction = change.transactions[0]
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        store.commit(
            PersistenceTransition(
                event.event_id, 0, (EventRecord(event, "INPUT"),), change.transactions
            ),
            journal.sync(),
        )
        observation = replace(event, event_id="later", engine_seq=2, available_ns=2)
        wrong = EconomicAliasUpdate(
            observation.event_id,
            transaction.transaction_id,
            replace(transaction.identity, native_id="alias", instrument="other"),
        )
        with pytest.raises(ValueError, match="scope"):
            store.commit(
                PersistenceTransition(
                    observation.event_id,
                    1,
                    (EventRecord(observation, "INPUT"),),
                    economic_aliases=(wrong,),
                ),
                journal.sync(),
            )
        assert store.state_watermark() == (1, 1)


def test_spot_instrument_cannot_be_accounted_as_a_linear_perpetual() -> None:
    from quantdesk.portfolio.events import ReservationChanged
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier
    from quantdesk.venues.instruments import InstrumentSpec

    spot_id = "fixture:SPOT:TEST:USDT:USDT:TESTUSDT"
    spot = InstrumentSpec.create(
        instrument_id=spot_id, tick_size=Decimal("0.01"), quantity_step=Decimal(1)
    )
    ledger = Ledger((spot,))
    with pytest.raises(ValueError, match="unsupported linear"):
        ledger.apply(
            envelope(fill("spot"), instrument=spot_id), LedgerState("fixture", "DEMO", "demo")
        )
    state = ledger.apply(
        envelope(MarkPrice(Decimal(100), 1, "fixture"), instrument=spot_id),
        LedgerState("fixture", "DEMO", "demo"),
    ).state
    pending = (
        ReservationChanged("spot-entry", spot_id, Side.BUY, 1, Decimal(0), Decimal(0), False, 1),
    )
    model = MarginSpec(
        spot,
        Decimal(1),
        Decimal(0),
        (MarginTier(Decimal(1000), Decimal("0.01"), Decimal(0)),),
        "tier-v1",
        1,
        10,
    )
    assert (
        Margin.estimate(ledger.snapshot(state), pending, model).status == "INCOMPLETE_MARGIN_MODEL"
    )


def test_worked_postings_and_fee_correction_persist_with_exact_native_precision(
    tmp_path: Path,
) -> None:
    from fractions import Fraction

    ledger = Ledger((spec(),))
    state = LedgerState("fixture", "DEMO", "demo")
    facts = (
        CashTransfer("deposit", Decimal(1000), "USDT", "IN", 0),
        fill("one", lots=2, fee="0.20"),
        fill("two", price="110", fee="0.11"),
        fill("three", Side.SELL, price="120", fee="0.12"),
        FundingSettlement("fund", INSTRUMENT, Decimal("-0.04"), "USDT", 0),
        FeeAdjustment("correction", "three", Decimal("-0.02"), "USDT", "confirmed fee delta"),
    )
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        for seq, payload in enumerate(facts, 1):
            event = envelope(payload, seq)
            change = ledger.apply(event, state)
            store.commit(
                PersistenceTransition(
                    event.event_id,
                    seq - 1,
                    (EventRecord(event, "INPUT"),),
                    change.transactions,
                    projection_updates=change.projections(),
                ),
                journal.sync(),
            )
            state = change.state
            stored = store.postings(change.transactions[0].transaction_id)
            assert stored == change.transactions[0].postings
            assert sum((Fraction(p.amount) for p in stored), Fraction()) == 0
        assert state.balance("expense:trading_fees") == Decimal("0.41")
        assert len(store.read_after(0)) == 6


@pytest.mark.parametrize(
    "opening_side,reducing_side,expected_long,expected_short",
    [(Side.BUY, Side.SELL, 10, -15), (Side.SELL, Side.BUY, 15, -10)],
)
def test_reduction_before_ordinary_reversal_reaches_larger_margin_tier(
    opening_side: Side, reducing_side: Side, expected_long: int, expected_short: int
) -> None:
    from quantdesk.portfolio.events import ReservationChanged
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", opening_side, lots=10), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    pending = (
        ReservationChanged(
            "close", INSTRUMENT, reducing_side, 10, Decimal(0), Decimal("0.25"), True, 1
        ),
        ReservationChanged(
            "entry", INSTRUMENT, reducing_side, 15, Decimal(0), Decimal("0.25"), False, 1
        ),
    )
    model = MarginSpec(
        spec(),
        Decimal(5),
        Decimal("0.5"),
        (
            MarginTier(Decimal(1000), Decimal("0.01"), Decimal(0)),
            MarginTier(Decimal(2000), Decimal("0.02"), Decimal(10)),
        ),
        "two-tiers",
        3,
        10,
    )
    estimate = Margin.estimate(ledger.snapshot(state), pending, model)
    assert (estimate.worst_long_lots, estimate.worst_short_lots) == (expected_long, expected_short)
    assert estimate.initial_margin == Decimal(301)
    assert estimate.incremental_pending_reserve == Decimal(100)
    assert estimate.maintenance_margin == Decimal(20)
    assert Margin.estimate(ledger.snapshot(state), tuple(reversed(pending)), model) == estimate


@pytest.mark.parametrize(
    "opening_side,reducing_side,expected_long,expected_short",
    [(Side.BUY, Side.SELL, 10, -9), (Side.SELL, Side.BUY, 9, -10)],
)
def test_partial_reduce_only_reservation_preserves_no_fill_extreme(
    opening_side: Side, reducing_side: Side, expected_long: int, expected_short: int
) -> None:
    from quantdesk.portfolio.events import ReservationChanged
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", opening_side, lots=10), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    pending = (
        ReservationChanged("close", INSTRUMENT, reducing_side, 4, Decimal(0), Decimal(0), True, 1),
        ReservationChanged(
            "entry", INSTRUMENT, reducing_side, 15, Decimal(0), Decimal(0), False, 1
        ),
    )
    model = MarginSpec(
        spec(),
        Decimal(5),
        Decimal(0),
        (MarginTier(Decimal(2000), Decimal("0.01"), Decimal(0)),),
        "tier",
        3,
        10,
    )
    estimate = Margin.estimate(ledger.snapshot(state), pending, model)
    assert (estimate.worst_long_lots, estimate.worst_short_lots) == (expected_long, expected_short)
    assert estimate.initial_margin == Decimal(200)


@pytest.mark.parametrize(
    "quantity_step,multiplier,valid_from,known_from,reason",
    [
        ("0.1", "1", 0, 0, "POSITION_UNIT_MISMATCH"),
        ("1", "0.1", 0, 0, "POSITION_UNIT_MISMATCH"),
        ("1", "1", 4, 0, "INSTRUMENT_SPEC_UNAVAILABLE"),
        ("1", "1", 0, 4, "INSTRUMENT_SPEC_UNAVAILABLE"),
    ],
)
def test_margin_does_not_reinterpret_position_with_incompatible_or_future_units(
    quantity_step: str, multiplier: str, valid_from: int, known_from: int, reason: str
) -> None:
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier
    from quantdesk.venues.instruments import InstrumentSpec

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", lots=10), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    alternate = InstrumentSpec.create(
        instrument_id=INSTRUMENT,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal(quantity_step),
        contract_multiplier=Decimal(multiplier),
        valid_from_ns=valid_from,
        known_from_ns=known_from,
        max_leverage=Decimal(20),
    )
    model = MarginSpec(
        alternate,
        Decimal(5),
        Decimal(0),
        (MarginTier(Decimal(10000), Decimal("0.01"), Decimal(0)),),
        "tier",
        3,
        10,
    )
    before = state.to_bytes()
    estimate = Margin.estimate(ledger.snapshot(state), (), model)
    assert estimate.status == "INCOMPLETE_MARGIN_MODEL"
    assert estimate.initial_margin is None and estimate.maintenance_margin is None
    assert reason in estimate.reasons
    assert state.to_bytes() == before
    assert state.position(INSTRUMENT).base_quantity == Decimal(10)


@pytest.mark.parametrize("missing_position_revision", [False, True])
def test_margin_requires_verifiable_spec_revisions(missing_position_revision: bool) -> None:
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", lots=10), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    instrument = spec()
    if missing_position_revision:
        state = replace(state, positions=(replace(state.position(INSTRUMENT), spec_revision=""),))
    else:
        instrument = replace(instrument, revision_hash="unverified")
    model = MarginSpec(
        instrument,
        Decimal(5),
        Decimal(0),
        (MarginTier(Decimal(10000), Decimal("0.01"), Decimal(0)),),
        "tier",
        3,
        10,
    )
    estimate = Margin.estimate(ledger.snapshot(state), (), model)
    assert estimate.status == "INCOMPLETE_MARGIN_MODEL"
    assert estimate.initial_margin is None and estimate.maintenance_margin is None


def test_margin_allows_newer_verified_metadata_with_unchanged_position_units() -> None:
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier
    from quantdesk.venues.instruments import InstrumentSpec

    ledger, state = start()
    state = ledger.apply(envelope(fill("open", lots=10), 2), state).state
    state = ledger.apply(envelope(MarkPrice(Decimal(100), 3, "fixture"), 3), state).state
    revised_tick = InstrumentSpec.create(
        instrument_id=INSTRUMENT,
        tick_size=Decimal("0.02"),
        quantity_step=Decimal(1),
        valid_from_ns=2,
        known_from_ns=2,
        max_leverage=Decimal(20),
    )
    model = MarginSpec(
        revised_tick,
        Decimal(5),
        Decimal(0),
        (MarginTier(Decimal(10000), Decimal("0.01"), Decimal(0)),),
        "tier",
        3,
        10,
    )
    estimate = Margin.estimate(ledger.snapshot(state), (), model)
    assert state.position(INSTRUMENT).spec_revision != revised_tick.revision_hash
    assert estimate.status == "ESTIMATED"
    assert estimate.initial_margin == Decimal(200)
    assert estimate.maintenance_margin == Decimal(10)


@pytest.mark.parametrize("restore_checkpoint", [False, True])
@pytest.mark.parametrize("keep_historical_metadata", [False, True])
def test_old_execution_duplicate_and_late_alias_survive_close_reopen_with_changed_units(
    restore_checkpoint: bool, keep_historical_metadata: bool
) -> None:
    from quantdesk.venues.instruments import InstrumentSpec

    original_spec = spec()
    revised = InstrumentSpec.create(
        instrument_id=INSTRUMENT,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.1"),
        valid_from_ns=10,
        known_from_ns=10,
        max_leverage=Decimal(20),
    )
    ledger = Ledger((original_spec, revised))
    state = LedgerState("fixture", "DEMO", "demo")
    state = ledger.apply(
        envelope(CashTransfer("deposit", Decimal(1000), "USDT", "IN", 0)), state
    ).state
    original = replace(fill("old-execution", lots=2, fee="0.20"), event_ns=2, receipt_ns=2)
    booked = ledger.apply(envelope(original, 2), state)
    state = ledger.apply(
        envelope(replace(fill("close-old", Side.SELL, lots=2, fee="0.20"), event_ns=3), 3),
        booked.state,
    ).state
    assert state.position(INSTRUMENT).signed_lots == 0
    state = ledger.apply(
        envelope(replace(fill("open-new", lots=3, fee="0.03"), event_ns=10), 10), state
    ).state
    assert state.position(INSTRUMENT).base_quantity == Decimal("0.3")
    assert state.balance("cash:USDT") == Decimal("999.57")
    if restore_checkpoint:
        state = LedgerState.from_bytes(state.to_bytes())
    reader = ledger if keep_historical_metadata else Ledger((revised,))
    duplicate = reader.apply(envelope(replace(original, receipt_ns=11), 11), state)
    assert duplicate.duplicate and duplicate.state == replace(state, available_ns=11)
    assert duplicate.transactions == () and duplicate.position_updates == ()
    assert duplicate.position_legs == ()
    rest_report = replace(original, native_execution_id="REST-old", receipt_ns=12)
    aliased = reader.apply(
        FinancialEvent(envelope(rest_report, 12), ("old-execution",)), duplicate.state
    )
    assert aliased.duplicate and aliased.transactions == ()
    assert len(aliased.alias_updates) == 1
    assert aliased.alias_updates[0].transaction_id == booked.transactions[0].transaction_id
    assert aliased.state.position(INSTRUMENT) == state.position(INSTRUMENT)
    assert aliased.state.balances == state.balances
    assert aliased.state.transactions == state.transactions
    restored_alias = LedgerState.from_bytes(aliased.state.to_bytes())
    assert reader.apply(envelope(rest_report, 13), restored_alias).duplicate
    with pytest.raises(ValueError, match="conflicting economic duplicate"):
        reader.apply(envelope(replace(original, price=Decimal(101)), 13), aliased.state)

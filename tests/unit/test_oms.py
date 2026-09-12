import asyncio
import sqlite3
from dataclasses import replace
from decimal import Decimal

import pytest

from quantdesk.core.events import CancelTransportResult, OrderReport, SubmitTransportResult
from quantdesk.core.types import IntentAction, Side
from quantdesk.execution.intents import (
    CancelRequested,
    DispatchStarted,
    OrderApproved,
    UnsentAborted,
)
from quantdesk.execution.oms import OMS
from quantdesk.execution.order_state import Knowledge, Lifecycle, OMSState, PendingAction
from quantdesk.execution.router import AccountOwnership, DispatchAuthority, GatewayPolicy, Router
from quantdesk.persistence.db import WriterOwnershipError
from quantdesk.persistence.outbox import Outbox
from quantdesk.portfolio.ledger import FinancialEvent, Ledger, LedgerState
from quantdesk.risk.arbitration import Arbitrator
from tests.support.accounting_case import INSTRUMENT, envelope, fill, spec
from tests.support.oms_case import AccountCase, instruction


@pytest.fixture
def account(tmp_path):
    value = AccountCase(tmp_path)
    yield value
    value.close()


def test_partial_fill_during_cancel_is_accounted_once(case):
    r = case("partial_fill_cancel_race", order_lots=5, fill_lots=2, duplicates=3)
    assert r == {
        "lifecycle": "CANCELED",
        "accounted_fill_lots": 2,
        "canceled_remainder_lots": 3,
        "ledger_execution_count": 1,
    }


def test_transport_ack_is_not_a_fill_or_confirmed_open(account):
    order = account.approve()
    account.send(SubmitTransportResult(order.instruction_id, True, "venue-order", None))
    result = account.state.order("client")
    assert (result.lifecycle, result.pending, result.accounted_fill_lots) == (
        Lifecycle.CREATED,
        PendingAction.SUBMIT,
        0,
    )
    assert account.portfolio.reservations[0].remaining_lots == 5
    assert account.portfolio.transactions == ()


def test_partial_fill_preserves_pending_cancel_and_late_fills_apply(account):
    value = account.approve()
    account.send(SubmitTransportResult(value.instruction_id, True, "venue-order", None))
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    account.send(fill("first", lots=2))
    assert account.state.order("client").pending == PendingAction.CANCEL
    assert account.portfolio.reservations[0].remaining_lots == 3
    account.send(OrderReport("client", "venue-order", "CANCELED", 2, 0))
    account.send(fill("late", lots=1))
    account.send(fill("late", lots=1))
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    result = account.state.order("client")
    assert result.lifecycle == Lifecycle.CANCELED
    assert result.accounted_fill_lots == 3 and result.canceled_remainder_lots == 2
    assert account.portfolio.position(INSTRUMENT).signed_lots == 3
    assert len(account.portfolio.transactions) == 2
    account.send(fill("last", lots=2))
    assert account.state.order("client").lifecycle == Lifecycle.FILLED


def test_terminal_missing_executions_retains_reserves_until_recovered(account):
    account.approve()
    account.send(OrderReport("client", "venue-order", "CANCELED", 2, 0))
    result = account.state.order("client")
    assert result.knowledge == Knowledge.RECONCILING and result.reserved_lots == 2
    assert account.portfolio.reservations[0].cash_amount == Decimal("20")
    assert account.portfolio.reservations[0].fee_buffer == Decimal("2")
    account.send(fill("recovered", lots=2))
    assert account.state.order("client").knowledge == Knowledge.CONFIRMED
    assert account.portfolio.reservations[0].remaining_lots == 0


def test_cancel_transport_rejection_does_not_reject_original_order(account):
    account.approve()
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    account.send(CancelTransportResult("client", False, "VENUE_REJECTED"))
    result = account.state.order("client")
    assert result.lifecycle == Lifecycle.OPEN and result.pending == PendingAction.NONE
    assert result.reserved_lots == 5


def test_unknown_timeout_and_inconclusive_absence_never_release(account):
    value = account.approve()
    account.send(SubmitTransportResult(value.instruction_id, False, None, "TIMEOUT"))
    result = account.state.order("client")
    assert result.knowledge == Knowledge.UNCERTAIN and result.reserved_lots == 5
    assert result.lifecycle == Lifecycle.CREATED
    with pytest.raises(ValueError, match=r"uncertain|unresolved"):
        account.oms.replace_order("client", instruction("new"), account.state)


def test_replace_requires_cancel_confirmation_and_resolved_execution_history(account):
    account.approve()
    with pytest.raises(ValueError, match="cancel"):
        account.oms.replace_order("client", instruction("new"), account.state)
    account.send(OrderReport("client", "venue-order", "CANCELED", 2, 0))
    with pytest.raises(ValueError, match="unresolved"):
        account.oms.replace_order("client", instruction("new"), account.state)
    account.send(fill("fill", lots=2))
    replacement = account.oms.replace_order(
        "client", instruction("new", quantity_lots=3), account.state
    )
    assert replacement.client_order_id == "new" and replacement.quantity_lots == 3


def test_fill_identity_can_arrive_before_venue_order_link(account):
    account.approve()
    account.send(replace(fill("first", lots=2), client_order_id=None))
    assert account.portfolio.position(INSTRUMENT).signed_lots == 2
    assert account.state.unmatched_executions
    account.send(OrderReport("client", "venue-order", "OPEN", 2, 0))
    assert account.state.order("client").accounted_fill_lots == 2
    assert account.state.unmatched_executions == ()
    account.send(fill("first", lots=2))
    assert len(account.portfolio.transactions) == 1


def test_conflicting_order_alias_fails_before_any_financial_effect(account):
    account.approve()
    account.approve(instruction("other"))
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    before = account.portfolio
    with pytest.raises(ValueError, match="identity"):
        account.send(replace(fill("bad"), client_order_id="other"))
    assert account.portfolio == before


def test_cross_source_economic_aliases_do_not_double_count_oms(account):
    account.approve()
    event = FinancialEvent(envelope(fill("ws", lots=2)), aliases=("rest",)).recorded_envelope()
    from quantdesk.core.reducers import decode_payload

    account.send(decode_payload(event))
    account.send(fill("rest", lots=2))
    assert account.state.order("client").accounted_fill_lots == 2
    assert len(account.portfolio.transactions) == 1


def test_reduce_only_reservations_across_strategies_never_exceed_position(account):
    account.send(fill("external", lots=5))
    account.approve(instruction("exit-a", side=Side.SELL, reduce_only=True, quantity_lots=3))
    with pytest.raises(ValueError, match=r"closing|position"):
        account.approve(
            instruction(
                "exit-b",
                side=Side.SELL,
                reduce_only=True,
                quantity_lots=3,
                owner_strategy_id="strategy-b",
            )
        )
    assert sum(r.remaining_lots for r in account.portfolio.reservations) == 3


def test_reduce_only_cannot_open_or_reverse_even_with_wrong_side():
    oms, state = OMS(), OMSState("fixture", "DEMO", "demo")
    ledger = Ledger((spec(),))
    portfolio = ledger.apply(
        envelope(fill("position", lots=2)), LedgerState("fixture", "DEMO", "demo")
    ).state
    for side, lots in ((Side.BUY, 1), (Side.SELL, 3)):
        with pytest.raises(ValueError, match=r"closing|position"):
            oms.approve(
                OrderApproved(
                    instruction(side=side, quantity_lots=lots, reduce_only=True),
                    Decimal(0),
                    Decimal(0),
                ),
                state,
                portfolio,
                1,
            )


def test_expired_unsent_releases_outbox_and_reservations_atomically(account):
    value = account.approve(instruction(expires_at_ns=2))
    transition = account.engine.process(
        account.incoming(UnsentAborted(value.instruction_id, "EXPIRED"))
    )
    assert len(Outbox(account.db.path).pending()) == 1
    assert account.state.order("client").reserved_lots == 5
    account.engine.commit(transition)
    assert Outbox(account.db.path).pending() == ()
    assert account.state.order("client").lifecycle == Lifecycle.EXPIRED
    assert account.portfolio.reservations[0].cash_amount == 0


def test_failed_outbox_status_cas_rolls_back_event_and_reservation(account):
    value = account.approve()
    before = account.engine.state
    transition = account.engine.process(
        account.incoming(DispatchStarted(value.instruction_id, "attempt"))
    )
    account.db.connection.execute("UPDATE outbox SET status='BLOCKED'")
    with pytest.raises(ValueError, match="outbox"):
        account.engine.commit(transition)
    assert account.engine.state == before
    assert len(account.store.read_after(0)) == 1


def intent(name, side=Side.BUY, action=IntentAction.ENTER, quantity="3", strategy="a"):
    from quantdesk.core.events import StrategyIntent

    return StrategyIntent(
        name,
        strategy,
        INSTRUMENT,
        1,
        "features",
        "config",
        None,
        action,
        side,
        Decimal(quantity),
        None,
        "LIMIT",
        1000,
        "stop",
        "fixture",
    )


def test_arbitration_deterministically_nets_only_unsent_same_owner_intents():
    arb = Arbitrator((spec(),))
    portfolio = LedgerState("fixture", "DEMO", "demo")
    state = OMSState("fixture", "DEMO", "demo")
    intents = (intent("sell", Side.SELL, quantity="2"), intent("buy", quantity="5"))
    a = arb.resolve(intents, portfolio, state)
    b = arb.resolve(tuple(reversed(intents)), portfolio, state)
    assert a == b and len(a.intents) == 1
    assert a.intents[0].side == Side.BUY and a.intents[0].desired_quantity == Decimal(3)


def test_arbitration_exit_precedence_closing_caps_and_symbol_exclusivity(account):
    account.send(fill("external", lots=5))
    account.approve()
    batch = Arbitrator((spec(),)).resolve(
        (intent("new", strategy="b"), intent("exit", Side.SELL, IntentAction.EXIT, "9")),
        account.portfolio,
        account.state,
    )
    assert [(i.intent_id, i.desired_quantity) for i in batch.intents] == [("exit", Decimal(5))]
    assert batch.cancel_client_ids == ("client",)
    assert any(r.intent_id == "new" for r in batch.rejections)


class BoundaryVenue:
    def __init__(self, prepare=None, lose_ack=False):
        self.prepare_callback = prepare
        self.lose_ack = lose_ack
        self.accepted = []

    async def prepare(self, instruction):
        await asyncio.sleep(0)
        if self.prepare_callback:
            self.prepare_callback()

    def handoff(self, instruction):
        self.accepted.append(instruction)

        async def response():
            if self.lose_ack:
                raise TimeoutError("accepted but ack lost")
            return SubmitTransportResult(instruction.instruction_id, True, "venue-order", None)

        return response()


def gateway(account, venue, policy=None):
    ownership = AccountOwnership(account.db, now_ns=lambda: account.time, heartbeat_timeout_ns=100)
    current = [policy or GatewayPolicy("risk-1", "latch-1", True, True)]
    authority = DispatchAuthority(
        account.store,
        ownership,
        lambda: current[0],
        lambda: account.time,
        lambda payload, _: account.send(payload),
        lambda _: True,
    )
    return Router(Outbox(account.db.path), authority, venue), authority, ownership, current


def test_lost_ack_cannot_retry_or_release_and_uses_original_client(account):
    value = account.approve()
    venue = BoundaryVenue(lose_ack=True)
    router, _authority, ownership, _ = gateway(account, venue)
    try:
        asyncio.run(router.dispatch_ready())
        asyncio.run(router.dispatch_ready())
        assert [i.client_order_id for i in venue.accepted] == [value.client_order_id]
        assert account.state.order("client").knowledge == Knowledge.UNCERTAIN
        assert account.state.order("client").reserved_lots == 5
        assert Outbox(account.db.path).pending() == ()
    finally:
        ownership.close()


@pytest.mark.parametrize("change", ["risk", "latch", "fence", "heartbeat", "expiry", "filters"])
def test_gateway_revalidates_after_await_immediately_before_handoff(account, change):
    account.approve()
    venue = BoundaryVenue()
    router, authority, ownership, policy = gateway(account, venue)

    def mutate():
        if change == "risk":
            policy[0] = replace(policy[0], risk_version="risk-2")
        if change == "latch":
            policy[0] = replace(policy[0], latch_version="latch-2", entries_allowed=False)
        if change == "fence":
            ownership.close()
        if change == "heartbeat":
            account.time += 101
        if change == "expiry":
            account.time += 10001
        if change == "filters":
            authority.validate_filters = lambda _: False

    venue.prepare_callback = mutate
    try:
        asyncio.run(router.dispatch_ready())
        assert venue.accepted == []
    finally:
        ownership.close()


def test_dispatch_permit_is_single_use_and_bound_to_exact_instruction(account):
    account.approve()
    _router, authority, ownership, _ = gateway(account, BoundaryVenue())
    try:
        row = Outbox(account.db.path).pending()[0]
        permit = authority.grant(row)
        with pytest.raises(PermissionError):
            authority.consume(permit, replace(row, payload=b"forged"))
        with pytest.raises(PermissionError):
            authority.consume(permit, row)
        assert account.state.order("client").knowledge == Knowledge.UNCERTAIN
    finally:
        ownership.close()


def test_gateway_claim_requires_committed_instruction_and_one_account_owner(account):
    _router, authority, ownership, _ = gateway(account, BoundaryVenue())
    try:
        from quantdesk.core.events import canonical_bytes
        from quantdesk.persistence.event_store import OutboxInstruction

        value = instruction()
        with pytest.raises(PermissionError):
            authority.grant(
                OutboxInstruction(
                    value.instruction_id,
                    value.client_order_id,
                    canonical_bytes(value),
                    "risk-1",
                    "1",
                    1,
                    10000,
                )
            )
        with pytest.raises(WriterOwnershipError):
            AccountOwnership(account.db, now_ns=lambda: 0)
    finally:
        ownership.close()


def test_writer_epoch_advances_on_reacquisition_and_stale_instruction_never_sends(account):
    account.approve()
    router, _authority, ownership, _ = gateway(account, BoundaryVenue())
    epoch = ownership.epoch
    ownership.close()
    venue = BoundaryVenue()
    router, _authority, ownership, _ = gateway(account, venue)
    try:
        assert int(ownership.epoch) == int(epoch) + 1
        asyncio.run(router.dispatch_ready())
        assert venue.accepted == []
    finally:
        ownership.close()


def test_v2_outbox_migration_preserves_committed_rows_and_old_statuses(tmp_path):
    from quantdesk.persistence.migrations import SCHEMA_VERSION, migrate

    connection = sqlite3.connect(tmp_path / "old.sqlite", isolation_level=None)
    try:
        migrate(connection, target_version=2)
        connection.execute(
            "INSERT INTO outbox VALUES ('i', 'client', X'00', 'PENDING', 'r', 'f', 1, 99)"
        )
        migrate(connection)
        assert connection.execute("SELECT instruction_id, status FROM outbox").fetchall() == [
            ("i", "PENDING")
        ]
        connection.execute("UPDATE outbox SET status='UNKNOWN'")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        connection.close()


def test_unknown_submission_blocks_a_fresh_id_even_with_new_intent(account):
    value = account.approve()
    account.send(SubmitTransportResult(value.instruction_id, False, None, "TIMEOUT"))
    with pytest.raises(ValueError, match="unresolved"):
        account.approve(instruction("retry"))


def test_late_close_fill_reallocates_existing_exits_without_losing_economic_fact(account):
    account.send(
        replace(fill("external", lots=5), client_order_id="external", venue_order_id="external")
    )
    original = instruction("exit-a", side=Side.SELL, reduce_only=True, quantity_lots=3)
    account.approve(original)
    account.send(OrderReport("exit-a", "venue-a", "CANCELED", 0, 0))
    account.approve(instruction("exit-b", side=Side.SELL, reduce_only=True, quantity_lots=3))
    account.approve(instruction("exit-c", side=Side.SELL, reduce_only=True, quantity_lots=2))
    late = replace(
        fill("late-close", Side.SELL, lots=3), client_order_id="exit-a", venue_order_id="venue-a"
    )
    account.send(late)
    account.send(late)
    assert account.portfolio.position(INSTRUMENT).signed_lots == 2
    assert len(account.portfolio.transactions) == 2
    assert sum(r.remaining_lots for r in account.portfolio.reservations if r.reduce_only) == 2
    assert "CLOSE_RESERVATION_CONFLICT" in account.state.incidents


def test_duplicate_execution_cannot_clear_unknown_cancel(account):
    value = account.approve()
    account.send(fill("partial", lots=1))
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    account.send(CancelTransportResult("client", False, "TIMEOUT"))
    account.send(fill("partial", lots=1))
    assert account.state.order(value.client_order_id).knowledge == Knowledge.UNCERTAIN
    assert account.state.order(value.client_order_id).pending == PendingAction.CANCEL


def test_sent_cancel_timeout_is_not_resolved_by_old_open_report(account):
    account.approve()
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    account.send(CancelTransportResult("client", False, "TIMEOUT"))
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    assert account.state.order("client").knowledge == Knowledge.UNCERTAIN


def test_abort_cannot_release_a_sent_ambiguous_request(account):
    account.approve()
    venue = BoundaryVenue(lose_ack=True)
    router, authority, ownership, _ = gateway(account, venue)
    try:
        row = Outbox(account.db.path).pending()[0]
        asyncio.run(router.dispatch_ready())
        with pytest.raises(PermissionError):
            authority.abort_unsent(row, "EXPIRED")
        assert account.state.order("client").reserved_lots == 5
    finally:
        ownership.close()


def test_checkpoint_recovery_replays_oms_ledger_aliases_and_dispatch_claims(account):
    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.execution.oms import execution_reducer

    checkpoint = account.engine.checkpoint()
    account.approve()
    account.send(DispatchStarted("instruction-client", "attempt"))
    account.send(fill("first", lots=2))
    account.send(OrderReport("client", "venue-order", "CANCELED", 3, 0))
    account.send(fill("late", lots=1))
    recovered = Engine(
        "accounting",
        account.store,
        raw_watermark=account.journal.sync,
        reducers=(
            execution_reducer(
                OMS(),
                account.ledger,
                OMSState("fixture", "DEMO", "demo"),
                LedgerState("fixture", "DEMO", "demo"),
            ),
        ),
        mode=EngineMode.RECOVERY,
        code_hash="oms-v1",
        schema_hash="oms-v1",
    )
    recovered.restore(checkpoint, account.store.read_after(0))
    assert recovered.hashes().economic_state_hash == account.engine.hashes().economic_state_hash
    assert recovered.state == account.engine.state


def test_pending_buy_and_sell_exposure_does_not_net_away(account):
    from quantdesk.portfolio.margin import Margin, MarginSpec, MarginTier

    account.approve(instruction("buy", quantity_lots=7))
    account.approve(instruction("sell", side=Side.SELL, quantity_lots=5))
    margin = Margin.estimate(
        account.ledger.snapshot(account.portfolio),
        account.portfolio.reservations,
        MarginSpec(
            spec(),
            Decimal(1),
            Decimal(0),
            (MarginTier(Decimal(100000), Decimal("0.01"), Decimal(0)),),
            "tiers",
            0,
            100000,
        ),
    )
    assert margin.worst_long_lots == 7 and margin.worst_short_lots == -5


def test_unsent_cancel_never_leaves_a_dispatchable_entry(account):
    account.approve()
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    assert Outbox(account.db.path).pending() == ()
    assert account.state.order("client").lifecycle == Lifecycle.CANCELED
    assert account.portfolio.reservations[0].remaining_lots == 0


def test_mainnet_cancel_gateway_remains_disarmed(account):
    from quantdesk.core.events import canonical_bytes

    account.approve()
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    account.db.connection.execute(
        "UPDATE store_metadata SET value=? WHERE name='account_scope'",
        (canonical_bytes(("fixture", "LIVE", "demo")),),
    )
    venue = BoundaryVenue()
    router, _authority, ownership, _ = gateway(account, venue)
    try:
        asyncio.run(router.dispatch_ready())
        assert venue.accepted == []
    finally:
        ownership.close()


def test_expired_queued_order_releases_even_when_original_gateway_fence_is_stale(account):
    account.approve(instruction(expires_at_ns=4))
    venue = BoundaryVenue()
    router, _authority, ownership, _ = gateway(account, venue)
    account.time = 1000
    try:
        asyncio.run(router.dispatch_ready())
        assert venue.accepted == []
        assert account.state.order("client").lifecycle == Lifecycle.EXPIRED
        assert account.portfolio.reservations[0].remaining_lots == 0
    finally:
        ownership.close()


def test_atomic_reservation_batch_validates_final_cap_and_rolls_back_bad_revisions(account):
    from quantdesk.portfolio.events import ReservationBatchChanged, ReservationChanged

    ledger = account.ledger
    portfolio = ledger.apply(
        envelope(fill("position", lots=5)), LedgerState("fixture", "DEMO", "demo")
    ).state
    first = ReservationChanged("a", INSTRUMENT, Side.SELL, 3, Decimal(0), Decimal(1), True, 1)
    second = replace(first, reservation_id="b", remaining_lots=2)
    portfolio = ledger.apply(
        envelope(ReservationBatchChanged(INSTRUMENT, (first, second)), 2), portfolio
    ).state
    shifted = ledger.apply(
        envelope(
            ReservationBatchChanged(
                INSTRUMENT,
                (
                    replace(first, remaining_lots=1, revision=2),
                    replace(second, remaining_lots=4, revision=2),
                ),
            ),
            3,
        ),
        portfolio,
    ).state
    assert [r.remaining_lots for r in shifted.reservations] == [1, 4]
    with pytest.raises(ValueError, match="revision"):
        ledger.apply(
            envelope(ReservationBatchChanged(INSTRUMENT, (replace(first, remaining_lots=1),)), 4),
            shifted,
        )
    with pytest.raises(ValueError, match="exceed"):
        ledger.apply(
            envelope(
                ReservationBatchChanged(
                    INSTRUMENT, (replace(first, remaining_lots=5, revision=3),)
                ),
                4,
            ),
            shifted,
        )
    assert [r.remaining_lots for r in shifted.reservations] == [1, 4]


def test_gateway_requires_account_writer_thread(account):
    from concurrent.futures import ThreadPoolExecutor

    account.approve()
    _router, authority, ownership, _ = gateway(account, BoundaryVenue())
    try:
        row = Outbox(account.db.path).pending()[0]
        with ThreadPoolExecutor(max_workers=1) as pool, pytest.raises(WriterOwnershipError):
            pool.submit(authority.grant, row).result()
        assert len(Outbox(account.db.path).pending()) == 1
    finally:
        ownership.close()


def test_known_unsent_expiry_cannot_erase_fill_that_raced_with_prepare(account):
    account.approve()
    venue = BoundaryVenue(prepare=lambda: account.send(fill("race", lots=2)))
    router, _authority, ownership, _ = gateway(account, venue)
    try:
        asyncio.run(router.dispatch_ready())
        assert venue.accepted == []
        assert account.portfolio.position(INSTRUMENT).signed_lots == 2
        assert account.state.order("client").reserved_lots == 3
    finally:
        ownership.close()


def test_stale_open_report_cannot_regress_reported_partial_fill(account):
    account.approve()
    account.send(OrderReport("client", "venue-order", "PARTIALLY_FILLED", 2, 0))
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    assert account.state.order("client").lifecycle == Lifecycle.PARTIALLY_FILLED
    assert account.state.order("client").reported_fill_lots == 2
    assert account.state.order("client").accounted_fill_lots == 0


def test_old_cancel_rejection_cannot_clear_a_new_pending_cancel(account):
    account.approve()
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    account.send(CancelRequested("cancel-old", "client", 10000, "risk-1", "1"))
    account.send(CancelTransportResult("client", False, "VENUE_REJECTED"))
    account.send(CancelRequested("cancel-new", "client", 10000, "risk-1", "1"))
    account.send(CancelTransportResult("client", False, "VENUE_REJECTED"))
    assert account.state.order("client").pending == PendingAction.CANCEL
    assert account.state.order("client").knowledge == Knowledge.UNCERTAIN


def test_real_reduce_only_venue_violation_is_accounted_and_flagged(account):
    account.send(
        replace(fill("initial", lots=2), client_order_id="external", venue_order_id="external")
    )
    account.approve(instruction("exit", side=Side.SELL, reduce_only=True, quantity_lots=2))
    account.send(
        replace(
            fill("violation", Side.SELL, lots=3),
            client_order_id="exit",
            venue_order_id="venue-exit",
        )
    )
    assert account.portfolio.position(INSTRUMENT).signed_lots == -1
    assert {"ORDER_OVERFILLED", "REDUCE_ONLY_VENUE_VIOLATION"}.issubset(account.state.incidents)
    assert sum(r.remaining_lots for r in account.portfolio.reservations) == 0


def test_complete_fill_resolves_a_queued_cancel_before_dispatch(account):
    account.approve()
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    account.send(CancelRequested("cancel", "client", 10000, "risk-1", "1"))
    account.send(fill("complete", lots=5))
    assert Outbox(account.db.path).pending() == ()


def test_reservation_failure_rolls_back_late_fill_and_all_candidate_projections(account):
    account.approve()
    before = account.engine.state
    account.db.connection.execute(
        "CREATE TRIGGER injected_fail BEFORE INSERT ON reservations "
        "BEGIN SELECT RAISE(ABORT, 'injected reservation failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected reservation failure"):
        account.send(fill("candidate", lots=2))
    assert account.engine.state == before
    assert (
        account.store.database.connection.execute(
            "SELECT COUNT(*) FROM ledger_transactions"
        ).fetchone()[0]
        == 0
    )
    assert len(account.store.read_after(0)) == 1


def test_v3_migration_interruption_restores_prior_outbox(tmp_path):
    from quantdesk.persistence.migrations import migrate

    class FailingMigration(sqlite3.Connection):
        fail = False

        def execute(self, sql, parameters=()):
            if self.fail and sql.startswith("ALTER TABLE outbox_v3"):
                raise sqlite3.OperationalError("interrupted rebuild")
            return super().execute(sql, parameters)

    connection = sqlite3.connect(
        tmp_path / "old.sqlite", isolation_level=None, factory=FailingMigration
    )
    try:
        migrate(connection, target_version=2)
        connection.execute(
            "INSERT INTO outbox VALUES ('i', 'client', X'00', 'SENT', 'r', 'f', 1, 99)"
        )
        connection.fail = True
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            migrate(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT instruction_id, status FROM outbox").fetchall() == [
            ("i", "SENT")
        ]
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='outbox_v3'").fetchall()
            == []
        )
    finally:
        connection.close()


def test_router_correlates_each_cancel_response_to_its_committed_attempt(account):
    class RejectCancels(BoundaryVenue):
        def handoff(self, instruction):
            self.accepted.append(instruction)

            async def response():
                return CancelTransportResult(instruction.client_order_id, False, "VENUE_REJECTED")

            return response()

    account.approve()
    account.send(OrderReport("client", "venue-order", "OPEN", 0, 0))
    venue = RejectCancels()
    router, _authority, ownership, _ = gateway(account, venue)
    try:
        for cancel in ("cancel-a", "cancel-b"):
            account.send(CancelRequested(cancel, "client", 10000, "risk-1", "1"))
            asyncio.run(router.dispatch_ready())
            assert account.state.order("client").pending == PendingAction.NONE
        assert [i.instruction_id for i in venue.accepted] == ["cancel-a", "cancel-b"]
        assert all(
            c.status == "REJECTED"
            for c in account.state.commands
            if isinstance(c.instruction, CancelRequested)
        )
    finally:
        ownership.close()


def test_symbol_owner_survives_filled_entry_until_position_is_flat(account):
    account.approve()
    account.send(fill("entry", lots=5))
    batch = Arbitrator((spec(),)).resolve(
        (intent("other", strategy="strategy-b"),), account.portfolio, account.state
    )
    assert batch.intents == ()
    with pytest.raises(ValueError, match="owner"):
        account.approve(instruction("other", owner_strategy_id="strategy-b"))
    account.approve(instruction("exit", side=Side.SELL, reduce_only=True))
    account.send(
        replace(
            fill("close", Side.SELL, lots=5), client_order_id="exit", venue_order_id="venue-exit"
        )
    )
    account.approve(instruction("new-owner", owner_strategy_id="strategy-b"))
    assert account.state.order("new-owner").instruction.owner_strategy_id == "strategy-b"


def test_approval_cannot_reverse_existing_position_or_enter_during_exit(account):
    account.approve()
    account.send(fill("entry", lots=5))
    with pytest.raises(ValueError, match="reversal"):
        account.approve(instruction("reversal", side=Side.SELL, quantity_lots=6))
    account.approve(instruction("exit", side=Side.SELL, reduce_only=True))
    with pytest.raises(ValueError, match="exit"):
        account.approve(instruction("entry-during-exit"))


def test_historical_reduce_only_fill_does_not_flag_a_later_authorized_new_direction(account):
    account.approve()
    account.send(fill("buy", lots=5))
    account.approve(instruction("close-long", side=Side.SELL, reduce_only=True))
    account.send(
        replace(
            fill("close", Side.SELL, lots=5),
            client_order_id="close-long",
            venue_order_id="close-long",
        )
    )
    account.approve(instruction("new-short", side=Side.SELL))
    account.send(
        replace(
            fill("short", Side.SELL, lots=5),
            client_order_id="new-short",
            venue_order_id="new-short",
        )
    )
    assert account.portfolio.position(INSTRUMENT).signed_lots == -5
    assert "REDUCE_ONLY_VENUE_VIOLATION" not in account.state.incidents

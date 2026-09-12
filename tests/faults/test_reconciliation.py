import asyncio

import pytest

from tests.support.oms_case import AccountCase
from tests.unit.test_oms import BoundaryVenue, gateway


def test_cancelled_preparation_releases_known_unsent_permit_without_sending(tmp_path):
    account = AccountCase(tmp_path)
    account.approve()

    def cancel():
        raise asyncio.CancelledError

    venue = BoundaryVenue(prepare=cancel)
    router, authority, ownership, _ = gateway(account, venue)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(router.dispatch_ready())
        assert venue.accepted == []
        assert not authority._issued and not authority._unsent
        assert account.state.commands[0].status == "BLOCKED"
        assert account.portfolio.reservations[0].remaining_lots == 0
    finally:
        ownership.close()
        account.close()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"accountLevel": "advanced"}, "ACCOUNT_LEVEL"),
        ({"holdMode": "hedge_mode"}, "HOLD_MODE"),
        ({"accountMode": "upgrading"}, "ACCOUNT_MODE"),
    ],
)
def test_unsupported_profile_never_becomes_live_ready(change, reason):
    from quantdesk.venues.bitget_uta.recovery import account_profile
    from tests.support.bitget_case import profile_rows

    info, settings, assets = profile_rows()
    settings.update(change)
    result = account_profile(info, settings, assets, (), ("BTCUSDT",))
    assert not result.eligible and reason in result.reasons


def test_absent_collateral_is_unknown_and_foreign_debt_is_blocked():
    from quantdesk.venues.bitget_uta.recovery import account_profile
    from tests.support.bitget_case import profile_rows

    info, settings, assets = profile_rows()
    del assets["assets"][0]["available"]
    assets["assets"].append({"coin": "BTC", "balance": "1", "debt": "0.1", "available": "0"})
    result = account_profile(info, settings, assets, (), ("BTCUSDT",))
    assert not result.eligible and result.available_collateral is None
    assert {"COLLATERAL_UNAVAILABLE", "MIXED_COLLATERAL", "BORROWING"} <= set(result.reasons)


def test_protection_shortfall_restore_budget_partial_close_and_confirmed_flat():
    from quantdesk.execution.protection import NativeProtection, ProtectionManager, ProtectionState

    manager = ProtectionManager(restoration_budget_ns=2000000000)
    state = ProtectionState("group", "instrument", 5, "MARK", "90")
    first = manager.evaluate(
        state, (), owned_lots=5, observed_ns=100, verified_flat=False, capability_verified=True
    )
    assert first.state.status == "RESTORING"
    assert first.actions == ("LATCH_ENTRIES", "CANCEL_ENTRY_REMAINDER", "RESTORE_STOP")
    coverage = NativeProtection("native", "group", 5, "MARK", "90", "pending", True)
    valid = manager.evaluate(
        first.state,
        (coverage,),
        owned_lots=5,
        observed_ns=200,
        verified_flat=False,
        capability_verified=True,
    )
    assert valid.state.status == "PROTECTED" and valid.actions == ()
    partial = manager.evaluate(
        valid.state,
        (coverage,),
        owned_lots=3,
        observed_ns=300,
        verified_flat=False,
        capability_verified=True,
    )
    assert "RESIZE_STOP" in partial.actions and "CANCEL_STOP" not in partial.actions
    missing = manager.evaluate(
        first.state,
        (),
        owned_lots=5,
        observed_ns=2000000101,
        verified_flat=False,
        capability_verified=True,
    )
    assert missing.actions == (
        "LATCH_ENTRIES",
        "CANCEL_ENTRY_REMAINDER",
        "BOUNDED_REDUCE_ONLY_EXIT",
    )
    uncertain_flat = manager.evaluate(
        valid.state,
        (coverage,),
        owned_lots=0,
        observed_ns=400,
        verified_flat=False,
        capability_verified=True,
    )
    assert "CANCEL_STOP" not in uncertain_flat.actions
    flat = manager.evaluate(
        valid.state,
        (coverage,),
        owned_lots=0,
        observed_ns=500,
        verified_flat=True,
        capability_verified=True,
    )
    assert flat.actions == ("CANCEL_STOP", "RECONCILE")


def test_two_stop_legs_do_not_add_coverage_and_unverified_coexistence_blocks():
    from quantdesk.execution.protection import NativeProtection, ProtectionManager, ProtectionState

    manager = ProtectionManager()
    state = ProtectionState("group", "instrument", 5, "MARK", "90")
    legs = (
        NativeProtection("tp", "group", 5, "MARK", "110", "pending", True, kind="TP"),
        NativeProtection("sl", "group", 2, "MARK", "90", "pending", True),
    )
    result = manager.evaluate(
        state, legs, owned_lots=5, observed_ns=100, verified_flat=False, capability_verified=False
    )
    assert result.state.status == "BLOCKED"
    assert result.covered_lots == 2
    assert "RESTORE_STOP" not in result.actions


def test_lost_ack_reconnect_resolves_original_order(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path))
    assert result["distinct_submitted_client_ids"] == 1
    assert result["missing_execution_applied_once"] is True
    assert result["position_matches_exchange"] is True
    assert result["blind_snapshot_overwrites"] == 0
    assert result["entries_before_convergence"] == 0
    assert result["converged"] is True
    assert result["cursor_committed"] is True
    assert result["observations"] >= 3


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing_history", "MISSING_EXECUTION_HISTORY"),
        ("foreign_order", "FOREIGN_ORDER"),
        ("history_gap", "HISTORY_RETENTION_GAP"),
        ("balance_drift", "BALANCE_DIVERGENCE:USDT"),
        ("protection_missing", "PROTECTION_UNVERIFIED"),
        ("foreign_protection", "FOREIGN_OR_UNVERIFIED_PROTECTION"),
    ],
)
def test_recovery_fault_never_overwrites_ledger_or_advances_cursor(tmp_path, fault, reason):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, fault=fault))
    assert not result["converged"]
    assert reason in result["reasons"]
    assert not result["cursor_committed"]
    assert result["blind_snapshot_overwrites"] == 0


def test_flat_opening_checkpoint_is_audited_and_restart_does_not_duplicate_cash(tmp_path):
    from tests.support.bitget_case import opening_case

    result = asyncio.run(opening_case(tmp_path))
    assert result["converged"]
    assert result["cash"] == "1000"
    assert result["opening_transactions"] == 1
    assert result["restart_cash"] == "1000"
    assert result["raw_evidence"] >= 2


@pytest.mark.parametrize("change", ["writer_latch", "profile", "capability", "invalid_filter"])
def test_concrete_adapter_writer_revalidation_revokes_prepared_bytes(tmp_path, change):
    from dataclasses import replace
    from decimal import Decimal

    from quantdesk.core.events import CashTransfer
    from quantdesk.execution.intents import OrderApproved
    from tests.support.bitget_case import RecoveryAccount
    from tests.support.oms_case import instruction

    async def run():
        account = RecoveryAccount(tmp_path)
        account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
        order = instruction(
            instrument_id=account.spec.instrument_id,
            quantity_lots=100,
            price_ticks=1000,
            native_trigger_basis="MARK",
            native_trigger_value=Decimal("90"),
            protection_group_id="group",
            expires_at_ns=account.time + 10000000000,
        )
        account.send(OrderApproved(order, Decimal("10"), Decimal("1")))
        router, authority, ownership, policy = gateway(account, account.adapter)
        original = account.transport.prepare

        async def mutate(request):
            await original(request)
            if change == "writer_latch":
                policy[0] = replace(policy[0], latch_version="changed", entries_allowed=False)
            elif change == "profile":
                account.adapter.last_profile = None
            elif change == "capability":
                account.adapter.protection_capability_evidence = None

        account.transport.prepare = mutate
        if change == "invalid_filter":
            account.adapter.normalizer.specs["BTCUSDT"] = replace(
                account.spec, trading_status="HALTED"
            )
        try:
            await account.adapter.connect_private()
            await router.dispatch_ready()
            assert account.transport.accepted == []
            assert not authority._issued and not authority._unsent
            assert account.adapter._prepared is None
        finally:
            ownership.close()
            await account.close()

    asyncio.run(run())


def test_protection_decisions_are_durable_and_restoration_deadline_survives_replay(tmp_path):
    from decimal import Decimal

    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.core.events import CashTransfer
    from quantdesk.execution.intents import OrderApproved
    from quantdesk.execution.protection import ProtectionReview
    from tests.support.accounting_case import fill
    from tests.support.bitget_case import RecoveryAccount
    from tests.support.oms_case import instruction

    async def run():
        account = RecoveryAccount(tmp_path)
        try:
            checkpoint = account.engine.checkpoint()
            account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
            order = instruction(
                instrument_id=account.spec.instrument_id,
                quantity_lots=5,
                price_ticks=1000,
                native_trigger_basis="MARK",
                native_trigger_value=Decimal("90"),
                protection_group_id="group",
                expires_at_ns=account.time + 10000000000,
            )
            account.send(OrderApproved(order, Decimal("1"), Decimal("1")))
            account.send(fill("fill", lots=5))
            account.send(ProtectionReview("group", (), account.time, False, True, True))
            first = json.loads(account.engine.state.get("protection", "group"))
            assert first["state"]["status"] == "RESTORING"
            restored = Engine(
                "uta-test",
                account.store,
                raw_watermark=account.journal.sync,
                reducers=account.reducers,
                producers=account.producers,
                code_hash="uta-test",
                schema_hash="uta-test",
                mode=EngineMode.RECOVERY,
            )
            restored.restore(checkpoint, account.store.read_after(checkpoint.engine_seq))
            restored.resume_offline()
            account.engine = restored
            await account.sleep(2)
            account.send(ProtectionReview("group", (), account.time, False, True, True))
            after = json.loads(restored.state.get("protection", "group"))
            assert after["state"]["status"] == "EXIT_REQUIRED"
            assert "BOUNDED_REDUCE_ONLY_EXIT" in after["actions"]
            assert restored.state.get("risk_latches", "protection:group") != b"null"
            actions = [
                r
                for r in account.store.read_after(0)
                if r.envelope.event_type == "ProtectionActionsRequired"
            ]
            assert len(actions) == 2
        finally:
            await account.close()

    import json

    asyncio.run(run())


def test_opening_activity_cannot_be_double_counted_as_a_baseline(tmp_path):
    from tests.support.bitget_case import opening_case

    result = asyncio.run(opening_case(tmp_path, fault="opening_activity"))
    assert not result["converged"]
    assert result["cash"] == "10"
    assert result["opening_transactions"] == 1


def test_full_recovery_over_real_loopback_http_and_websocket(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, use_sockets=True))
    assert result["converged"] and result["missing_execution_applied_once"]
    assert result["distinct_submitted_client_ids"] == 1


def test_confirmed_flat_requires_stale_owned_stop_cleanup_before_convergence(tmp_path):
    from decimal import Decimal

    from quantdesk.core.events import CashTransfer, OrderReport
    from quantdesk.execution.intents import OrderApproved
    from quantdesk.execution.reconciliation import Reconciler
    from tests.support.bitget_case import RecoveryAccount, incoming
    from tests.support.oms_case import instruction

    async def run():
        account = RecoveryAccount(tmp_path, opening=True, fault="stale_protection")
        try:
            account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
            order = instruction(
                instrument_id=account.spec.instrument_id,
                quantity_lots=100,
                price_ticks=1000,
                native_trigger_basis="MARK",
                native_trigger_value=Decimal("90"),
                protection_group_id="group",
                expires_at_ns=account.time + 10000000000,
            )
            account.send(OrderApproved(order, Decimal("10"), Decimal("1")))
            account.send(OrderReport("client", "venue-order", "CANCELED", 0, account.time))
            runner = Reconciler(
                account.engine,
                account.adapter,
                lambda payload: incoming(payload, time=account.time),
                clock=lambda: account.time,
                sleep=account.sleep,
                timeout_ns=4000000000,
            )
            result = await runner.run(start_ms=account.time // 1000000 - 1000)
            assert not result.converged and "STALE_PROTECTION" in result.reasons
            records = account.store.read_after(0)
            assert any(
                r.envelope.event_type == "ProtectionActionsRequired"
                and b'"CANCEL_STOP"' in r.envelope.payload
                for r in records
            )
        finally:
            await account.close()

    asyncio.run(run())

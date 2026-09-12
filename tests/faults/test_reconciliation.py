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
    coverage = NativeProtection(
        "native",
        "group",
        5,
        "MARK",
        "90",
        "pending",
        True,
        instrument_id="instrument",
        position_side="LONG",
        reduce_only=True,
    )
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
        NativeProtection(
            "tp",
            "group",
            5,
            "MARK",
            "110",
            "pending",
            True,
            kind="TP",
            instrument_id="instrument",
            position_side="LONG",
            reduce_only=True,
        ),
        NativeProtection(
            "sl",
            "group",
            2,
            "MARK",
            "90",
            "pending",
            True,
            instrument_id="instrument",
            position_side="LONG",
            reduce_only=True,
        ),
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


def test_review_live_order_recovers_as_real_oms_open(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, fault="live_order"))
    assert result["lifecycle"] == "OPEN"
    assert result["converged"]


@pytest.mark.parametrize(
    "changes",
    [
        {"stopLoss": "80", "slTriggerBy": "market", "slOrderType": "limit"},
        {"stopLoss": None},
        {"slTriggerBy": None},
        {"slOrderType": None},
        {"slTriggerBy": "index"},
        {"stopLoss": "garbled"},
    ],
)
def test_round2_attached_stop_contract_conflict_blocks_recovery(tmp_path, changes):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, fault="live_order", order_changes=changes))
    assert not result["converged"] and not result["cursor_committed"]
    assert "ORDER_CONTRACT_CONFLICT" in result["reasons"]
    assert any(row["discrepancies"] for row in result["order_evidence"])


def test_round2_equivalent_attached_stop_decimal_converges(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, order_changes={"stopLoss": "90.000"}))
    assert result["converged"]


@pytest.mark.parametrize(
    "extra",
    [
        {"stopLoss": "90"},
        {"slTriggerBy": "mark"},
        {"slOrderType": "market"},
        {"stopLoss": "bad"},
        {"slTriggerBy": "index"},
    ],
)
def test_round2_unexpected_stop_on_unprotected_instruction_is_writer_conflict(tmp_path, extra):
    from decimal import Decimal

    from quantdesk.core.events import CashTransfer
    from quantdesk.execution.intents import OrderApproved
    from tests.support.bitget_case import RecoveryAccount
    from tests.support.oms_case import instruction

    async def run():
        account = RecoveryAccount(tmp_path)
        try:
            account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
            order = instruction(
                instrument_id=account.spec.instrument_id,
                quantity_lots=100,
                price_ticks=1000,
                expires_at_ns=account.time + 10000000000,
            )
            account.send(OrderApproved(order, Decimal("10"), Decimal("1")))
            row = {
                "category": "USDT-FUTURES",
                "symbol": "BTCUSDT",
                "clientOid": "client",
                "orderId": "venue-order",
                "qty": "0.100",
                "side": "buy",
                "price": "100",
                "orderType": "limit",
                "timeInForce": "gtc",
                "reduceOnly": "no",
                "marginMode": "isolated",
                "holdMode": "one_way_mode",
                **extra,
            }
            # Private updates have no expected instruction at the adapter; the
            # real writer must independently bind observed attached controls.
            evidence = account.adapter.normalizer.order_contract(row, None)
            account.send(evidence)
            assert account.engine.state.get("oms", "order-contract-conflicts-v1") == b'["client"]'
            assert any(
                r.envelope.event_type == "OrderContractObserved"
                for r in account.store.read_after(0)
            )
        finally:
            await account.close()

    asyncio.run(run())


def test_round2_native_stop_is_validated_against_writer_not_venue_position(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(
        recovery_case(
            tmp_path, position_changes={"posSide": "short"}, stop_changes={"posSide": "short"}
        )
    )
    assert not result["converged"]
    assert result["protection"]["group"]["state"]["status"] != "PROTECTED"
    assert "BOUNDED_REDUCE_ONLY_EXIT" in result["protection"]["group"]["actions"]


def test_round2_native_safe_boolean_without_evidence_cannot_protect():
    from quantdesk.execution.protection import NativeProtection, ProtectionManager, ProtectionState

    result = ProtectionManager().evaluate(
        ProtectionState("group", "instrument", 100, "MARK", "90"),
        (NativeProtection("stop", "group", 100, "MARK", "90", "pending", True),),
        owned_lots=100,
        observed_ns=1,
        verified_flat=False,
        capability_verified=True,
    )
    assert result.state.status != "PROTECTED" and result.actions


@pytest.mark.parametrize(
    "lots,native_side,native_instrument,reducing,protected",
    [
        (100, "LONG", "instrument", True, True),
        (-100, "SHORT", "instrument", True, True),
        (100, "SHORT", "instrument", True, False),
        (-100, "LONG", "instrument", True, False),
        (100, "LONG", "other-instrument", True, False),
        (100, "LONG", "instrument", False, False),
        (100, "LONG", "instrument", None, False),
    ],
)
def test_round2_manager_checks_canonical_native_evidence(
    lots, native_side, native_instrument, reducing, protected
):
    from quantdesk.execution.protection import NativeProtection, ProtectionManager, ProtectionState

    leg = NativeProtection(
        "stop",
        "group",
        100,
        "MARK",
        "90",
        "pending",
        True,
        instrument_id=native_instrument,
        position_side=native_side,
        reduce_only=reducing,
    )
    result = ProtectionManager().evaluate(
        ProtectionState("group", "instrument", 100, "MARK", "90"),
        (leg,),
        owned_lots=lots,
        observed_ns=1,
        verified_flat=False,
        capability_verified=True,
    )
    assert (result.state.status == "PROTECTED") is protected
    assert bool(result.actions) is not protected
    assert result.covered_lots == (100 if protected else 0)


@pytest.mark.parametrize(
    "error", [RuntimeError, asyncio.CancelledError, SystemExit, KeyboardInterrupt]
)
def test_round2_possibly_written_exception_irrevocably_revokes_unsent(tmp_path, error):
    class AcceptThenRaise(BoundaryVenue):
        def handoff(self, instruction):
            self.accepted.append(instruction)
            self.permit = next(iter(authority._unsent.values()))
            raise error("boundary accepted bytes")

    account = AccountCase(tmp_path)
    account.approve()
    venue = AcceptThenRaise()
    router, authority, ownership, _ = gateway(account, venue)
    row = router.outbox.pending()[0]

    async def run():
        # Catch process-style exceptions inside the coroutine so the test never
        # interrupts the runner; the real synchronous Router path is exercised.
        try:
            await router.dispatch_ready()
        except BaseException as exc:
            assert isinstance(exc, error)
        else:
            pytest.fail("unexpected synchronous boundary error must propagate")
        assert len(venue.accepted) == 1
        assert not authority._unsent and not authority._issued
        assert account.state.commands[0].status == "UNKNOWN"
        assert account.portfolio.reservations[0].remaining_lots == 5
        with pytest.raises(PermissionError):
            authority.abort_unsent(row, "DISPATCH_INVALIDATED", venue.permit)
        assert account.state.commands[0].status == "UNKNOWN"

    try:
        asyncio.run(run())
    finally:
        ownership.close()
        account.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"qty": "0.200"},
        {"side": "sell"},
        {"marginMode": "cross"},
        {"holdMode": "hedge_mode"},
        {"price": "101"},
        {"orderType": "market"},
        {"timeInForce": "ioc"},
        {"reduceOnly": "yes"},
        {"qty": None},
    ],
)
def test_review_conflicting_known_order_contract_is_durable_block(tmp_path, changes):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, order_changes=changes))
    assert not result["converged"] and not result["cursor_committed"]
    assert "ORDER_CONTRACT_CONFLICT" in result["reasons"]
    assert result["order_evidence"]
    assert any(row["discrepancies"] for row in result["order_evidence"])


def test_review_buffered_private_order_contract_is_checked_against_writer(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, private_order_changes={"qty": "0.200"}))
    assert not result["converged"] and "ORDER_CONTRACT_CONFLICT" in result["reasons"]


def test_review_missing_active_open_order_prevents_flat_and_stop_cleanup(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, fault="missing_open_order"))
    assert not result["converged"]
    assert "ACTIVE_ORDER_SET_MISMATCH" in result["reasons"]
    assert not any("CANCEL_STOP" in row["actions"] for row in result["action_facts"])


@pytest.mark.parametrize(
    "changes",
    [
        {"posSide": "short"},
        {"reduceOnly": "no"},
        {"posSide": "short", "reduceOnly": "no"},
        {"reduceOnly": None},
        {"slTriggerBy": "index"},
        {"qty": None},
        {"symbol": "ETHUSDT"},
    ],
)
def test_review_unsafe_native_stop_cannot_be_protected_without_actions(tmp_path, changes):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, stop_changes=changes))
    assert result["protection"]["group"]["state"]["status"] != "PROTECTED"
    assert "BOUNDED_REDUCE_ONLY_EXIT" in result["protection"]["group"]["actions"]
    assert not result["converged"]


def test_review_old_canceled_group_does_not_own_later_filled_position(tmp_path):
    from tests.support.bitget_case import recovery_case

    result = asyncio.run(recovery_case(tmp_path, old_canceled=True))
    assert not any(
        "BOUNDED_REDUCE_ONLY_EXIT" in row["actions"]
        for row in result["action_facts"]
        if row["group_id"] == "old-group"
    )
    assert result["protection"]["group"]["state"]["status"] == "PROTECTED"
    assert result["converged"]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_review_protection_owner_changes_only_after_actual_flat_cycle_and_replays(
    tmp_path, direction
):
    import json
    from dataclasses import replace
    from decimal import Decimal

    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.core.events import CashTransfer
    from quantdesk.core.types import Side
    from quantdesk.execution.intents import OrderApproved
    from quantdesk.execution.protection import NativeProtection, ProtectionReview
    from tests.support.accounting_case import fill
    from tests.support.bitget_case import RecoveryAccount
    from tests.support.oms_case import instruction

    async def run():
        account = RecoveryAccount(tmp_path)
        try:
            checkpoint = account.engine.checkpoint()
            entry_side = Side(direction)
            close_side = Side.SELL if entry_side == Side.BUY else Side.BUY
            trigger = "90" if entry_side == Side.BUY else "110"
            account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
            for client, side, group, reducing in (
                ("old", entry_side, "old-group", False),
                ("close", close_side, None, True),
                ("new", entry_side, "new-group", False),
            ):
                order = instruction(
                    client,
                    instrument_id=account.spec.instrument_id,
                    quantity_lots=100,
                    price_ticks=1000,
                    side=side,
                    reduce_only=reducing,
                    native_trigger_basis="MARK" if group else None,
                    native_trigger_value=Decimal(trigger) if group else None,
                    protection_group_id=group,
                    expires_at_ns=account.time + 10000000000,
                )
                account.send(OrderApproved(order, Decimal("10"), Decimal("1")))
                account.send(
                    replace(
                        fill(client + "-fill", side, 100),
                        client_order_id=client,
                        venue_order_id=client + "-order",
                    )
                )
            # Replayed old execution is factual corroboration, not a new owner.
            account.send(
                replace(
                    fill("old-fill", entry_side, 100),
                    client_order_id="old",
                    venue_order_id="old-order",
                )
            )
            account.send(ProtectionReview("old-group", (), account.time, False, True, False))
            stop = NativeProtection(
                "new-stop",
                "new-group",
                100,
                "MARK",
                trigger,
                "pending",
                True,
                instrument_id=account.spec.instrument_id,
                position_side="LONG" if entry_side == Side.BUY else "SHORT",
                reduce_only=True,
            )
            account.send(ProtectionReview("new-group", (stop,), account.time, False, True, False))
            old = json.loads(account.engine.state.get("protection", "old-group"))
            assert old["state"]["desired_lots"] == 0 and old["actions"] == []
            assert (
                json.loads(account.engine.state.get("protection", "new-group"))["state"]["status"]
                == "PROTECTED"
            )
            replay = Engine(
                "uta-test",
                account.store,
                raw_watermark=account.journal.sync,
                reducers=account.reducers,
                producers=account.producers,
                code_hash="uta-test",
                schema_hash="uta-test",
                mode=EngineMode.RECOVERY,
            )
            replay.restore(checkpoint, account.store.read_after(checkpoint.engine_seq))
            assert replay.state.protection == account.engine.state.protection
            assert replay.state.get("oms", "protection-ownership-v1") == account.engine.state.get(
                "oms", "protection-ownership-v1"
            )
        finally:
            await account.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "semantic_quarantine",
        "decimal_quarantine",
        "prebyte_expiry",
        "prebyte_header",
        "prebyte_filter_revision",
    ],
)
def test_review_private_quarantine_and_prebyte_expiry_release_unsent(tmp_path, fault):
    from decimal import Decimal

    from quantdesk.core.events import CashTransfer
    from quantdesk.execution.intents import OrderApproved
    from tests.support.bitget_case import RecoveryAccount
    from tests.support.oms_case import instruction

    async def run():
        account = RecoveryAccount(tmp_path)
        ownership = None
        try:
            account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
            order = instruction(
                instrument_id=account.spec.instrument_id,
                quantity_lots=100,
                price_ticks=1000,
                native_trigger_basis="MARK",
                native_trigger_value=Decimal("90"),
                protection_group_id="group",
                expires_at_ns=account.time + 60000000000,
            )
            account.send(OrderApproved(order, Decimal("10"), Decimal("1")))
            await account.adapter.connect_private()
            router, authority, ownership, policy = gateway(account, account.adapter)
            if fault in {"semantic_quarantine", "decimal_quarantine"}:
                import json

                row = (
                    {}
                    if fault == "semantic_quarantine"
                    else {
                        "category": "USDT-FUTURES",
                        "symbol": "BTCUSDT",
                        "clientOid": "client",
                        "orderId": "venue-order",
                        "execId": "bad-fill",
                        "side": "buy",
                        "execQty": "0.100",
                        "execPrice": "not-a-decimal",
                        "createdTime": "1789200000123",
                        "tradeScope": "maker",
                        "feeDetail": [{"feeCoin": "USDT", "fee": "0"}],
                    }
                )
                await account.socket.queue.put(
                    json.dumps({"arg": {"instType": "UTA", "topic": "fill"}, "data": [row]})
                )
                await asyncio.wait_for(account.adapter.private.changed.wait(), 1)
                events = account.adapter.drain_private()
                assert events[0].event_type == "DataGap"
                assert not account.adapter.private_healthy()
                assert account.adapter.last_profile is None
                for event in events:
                    account.engine.commit(account.engine.process(event))
                assert any(r.envelope.event_type == "DataGap" for r in account.store.read_after(0))
            else:

                def jump_after_prepare():
                    if account.adapter._prepared is not None:
                        if fault == "prebyte_expiry":
                            account.time += 6000000000
                        elif fault == "prebyte_header":
                            from dataclasses import replace

                            account.adapter.rest.credentials = replace(
                                account.adapter.rest.credentials, api_key="unencodable-한글"
                            )
                        else:
                            from dataclasses import replace

                            account.adapter.normalizer.specs["BTCUSDT"] = replace(
                                account.spec, tick_size=Decimal("0.2")
                            )
                    return policy[0]

                authority.current_policy = jump_after_prepare
            await router.dispatch_ready()
            assert account.transport.accepted == []
            assert account.state.commands[0].status == "BLOCKED"
            assert not authority._issued and not authority._unsent
            assert account.portfolio.reservations[0].remaining_lots == 0
        finally:
            if ownership:
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

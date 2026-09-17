"""External boundary scripts only; core state uses Engine, OMS and Ledger."""

import asyncio
import json

from quantdesk.core.events import IncomingEvent, canonical_bytes


def clock():
    return 1789200000124000000


def credentials():
    from quantdesk.venues.bitget_uta.auth import Credentials

    return Credentials("fixture-key", "fixture-secret", "fixture-passphrase", demo=True)


def incoming(payload, *, instrument=None, channel="private", time=None):
    now = time or clock()
    return IncomingEvent(
        type(payload).__name__,
        1,
        "uta-test",
        "demo",
        "bitget",
        "DEMO",
        instrument,
        channel,
        "epoch-1",
        None,
        None,
        None,
        None,
        now,
        now,
        now,
        None,
        "test",
        None,
        "bitget-test-v1",
        canonical_bytes(payload),
    )


class ScriptedHTTP:
    base_url = "http://127.0.0.1:12345"

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def prepare(self, request):
        assert self.script, f"unexpected request {request.target}"

    def handoff(self, request):
        from quantdesk.venues.bitget_uta.rest import Response

        self.requests.append(request)
        method, target, status, data = self.script.pop(0)
        assert (request.method, request.target) == (method, target)

        async def response():
            if isinstance(data, BaseException):
                raise data
            return Response(status, canonical_bytes(data))

        return response()

    async def close(self):
        self.script.clear()

    def discard_prepared(self):
        return None


class ScriptedSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []

    async def connect(self, url):
        return self

    async def send(self, data):
        value = json.loads(data) if data != "ping" else data
        self.sent.append(value)
        if value == "ping":
            await self.queue.put("pong")
        elif value["op"] == "login":
            await self.queue.put('{"event":"login","code":"0"}')
        elif value["op"] == "subscribe":
            for arg in value["args"]:
                await self.queue.put(json.dumps({"event": "subscribe", "arg": arg}))

    async def recv(self):
        value = await self.queue.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self):
        return None


def profile_rows():
    return (
        {
            "userId": "fixture",
            "permType": "read-and-write",
            "permissions": ["uta_trade", "uta_mgt"],
        },
        {
            "uid": "fixture",
            "accountMode": "unified",
            "accountLevel": "isolated",
            "holdMode": "one_way_mode",
            "deltaSwitch": "no",
            "symbolConfigList": [
                {
                    "category": "USDT-FUTURES",
                    "symbol": "BTCUSDT",
                    "marginMode": "isolated",
                    "leverage": "1",
                }
            ],
            "coinConfigList": [],
        },
        {
            "assets": [
                {"coin": "USDT", "balance": "1000", "available": "1000", "debt": "0", "locked": "0"}
            ]
        },
    )


class RecoveryHTTP(ScriptedHTTP):
    """Stateful external-only emulator: accepts once, loses ACK, lags snapshots."""

    def __init__(self, fault=None, opening=False):
        super().__init__([])
        self.fault, self.opening = fault, opening
        self.round = 0
        self.accepted = []
        self.order_changes = {}
        self.stop_changes = {}
        self.stop_rows = None
        self.extra_stop_rows = []
        self.position_changes = {}

    async def prepare(self, request):
        await asyncio.sleep(0)

    def handoff(self, request):
        from urllib.parse import parse_qs, urlsplit

        from quantdesk.venues.bitget_uta.rest import Response

        self.requests.append(request)
        path = urlsplit(request.target).path
        query = parse_qs(urlsplit(request.target).query)
        order = {
            "category": "USDT-FUTURES",
            "symbol": "BTCUSDT",
            "clientOid": "client",
            "orderId": "venue-order",
            "orderStatus": "filled",
            "cumExecQty": "0.100",
            "updatedTime": "1789200000123",
            "qty": "0.100",
            "side": "buy",
            "price": "100",
            "orderType": "limit",
            "timeInForce": "gtc",
            "reduceOnly": "no",
            "marginMode": "isolated",
            "holdMode": "one_way_mode",
            "stopLoss": "90",
            "slTriggerBy": "mark",
            "slOrderType": "market",
        }
        if self.fault == "stale_protection":
            order.update(orderStatus="cancelled", cumExecQty="0")
        active = self.fault in {"live_order", "missing_open_order"}
        if active:
            order.update(
                orderStatus="live" if self.fault == "live_order" else "new", cumExecQty="0"
            )
        order.update(self.order_changes)
        order = {key: value for key, value in order.items() if value is not None}
        fill = {
            "category": "USDT-FUTURES",
            "symbol": "BTCUSDT",
            "clientOid": "client",
            "orderId": "venue-order",
            "execId": "execution-1",
            "execLinkId": "link-1",
            "side": "buy",
            "execQty": "0.100",
            "execPrice": "100",
            "createdTime": "1789200000123",
            "tradeScope": "maker",
            "feeDetail": [{"feeCoin": "USDT", "fee": "0"}],
            "execPnl": "0",
        }
        info, settings, assets = profile_rows()
        if self.fault == "balance_drift":
            assets["assets"][0]["balance"] = "1010"
        if path.endswith("place-order"):
            self.accepted.append(json.loads(request.body))

            async def lost():
                raise TimeoutError("accepted by emulator, acknowledgement dropped")

            return lost()
        if path.endswith("unfilled-orders"):
            if "cursor" not in query:
                self.round += 1
            rows = (
                [
                    {
                        **order,
                        "clientOid": "foreign",
                        "orderId": "foreign-order",
                        "orderStatus": "live",
                        "cumExecQty": "0",
                    }
                ]
                if self.fault == "foreign_order" and "cursor" not in query
                else []
            )
            data = {"list": rows, "cursor": "foreign-page"}
            if self.fault == "live_order" and "cursor" not in query:
                data["list"] = [order]
        elif path.endswith("order-info"):
            assert query["clientOid"][0] in {"client", "old-client"}
            data = (
                {
                    **order,
                    "clientOid": "old-client",
                    "orderId": "old-native",
                    "orderStatus": "cancelled",
                    "cumExecQty": "0",
                }
                if query["clientOid"] == ["old-client"]
                else order
            )
        elif path.endswith("history-orders"):
            assert query.get("category") == ["USDT-FUTURES"]
            data = {
                "list": [order] if not self.opening and "cursor" not in query else [],
                "cursor": "history-page",
            }
        elif path.endswith("/fills"):
            data = {
                "list": [fill, fill]
                if not self.opening
                and not active
                and self.fault != "missing_history"
                and "cursor" not in query
                else [],
                "cursor": "fill-page",
            }
        elif path.endswith("financial-records"):
            assert query.get("category")
            data = {
                "list": [
                    {
                        "id": "during-opening",
                        "category": "OTHER",
                        "type": "TRANSFER_IN",
                        "amount": "10",
                        "coin": "USDT",
                        "ts": "1789200000124",
                    }
                ]
                if self.fault == "opening_activity"
                and query["category"] == ["OTHER"]
                and "cursor" not in query
                else [],
                "cursor": "cash-page",
            }
        elif path.endswith("/info"):
            data = info
        elif path.endswith("/settings"):
            data = settings
        elif path.endswith("/assets"):
            data = assets
        elif path.endswith("current-position"):
            data = {
                "list": []
                if self.opening or active or query["category"] != ["USDT-FUTURES"]
                else [
                    {
                        "category": "USDT-FUTURES",
                        "symbol": "BTCUSDT",
                        "total": "0.100" if self.round > 1 else "0.099",
                        "available": "0.100" if self.round > 1 else "0.099",
                        "frozen": "0",
                        "posSide": "long",
                        "avgPrice": "100",
                        "marginMode": "isolated",
                        "holdMode": "one_way_mode",
                        **self.position_changes,
                    }
                ]
            }
        elif path.endswith("unfilled-strategy-orders"):
            data = (
                []
                if (self.opening and self.fault != "stale_protection")
                or self.fault == "protection_missing"
                or query["category"] != ["usdt-futures"]
                else [
                    {
                        "orderId": "stop-1",
                        "clientOid": "stop-client",
                        "category": "usdt-futures",
                        "symbol": "BTCUSDT",
                        "qty": "0.100",
                        "posSide": "long",
                        "status": "pending",
                        "slTriggerBy": "mark",
                        "stopLoss": "90",
                        "slOrderType": "market",
                        "reduceOnly": "yes",
                        **self.stop_changes,
                    }
                ]
            )
            if self.fault == "foreign_protection" and query["category"] == ["spot"]:
                data = [{"orderId": "foreign-conditional", "category": "spot", "symbol": "BTCUSDT"}]
            if query["category"] == ["usdt-futures"]:
                if self.stop_rows is not None:
                    data = self.stop_rows
                data = [*data, *self.extra_stop_rows]
        else:
            raise AssertionError(f"unrecognized emulator endpoint {path}")

        async def response():
            if path.endswith("unfilled-strategy-orders"):
                # External JSON need not satisfy the core canonical contract.
                wire = json.dumps({"code": "00000", "data": data}, ensure_ascii=True)
                return Response(200, wire.replace("Infinity", "1e999").encode("ascii"))
            return Response(200, canonical_bytes({"code": "00000", "data": data}))

        return response()


class RecoveryAccount:
    def __init__(self, path, *, fault=None, opening=False):
        from decimal import Decimal

        from quantdesk.core.engine import Engine
        from quantdesk.execution.oms import OMS, execution_reducer
        from quantdesk.execution.order_state import OMSState
        from quantdesk.execution.protection import protection_producer, protection_reducer
        from quantdesk.execution.reconciliation import recovery_reducer
        from quantdesk.persistence.db import Database
        from quantdesk.persistence.event_store import EventStore
        from quantdesk.persistence.raw_journal import AESGCMCipher, RawJournal
        from quantdesk.portfolio.ledger import Ledger, LedgerState
        from quantdesk.venues.bitget_uta import BitgetUTAAdapter
        from tests.integration.test_bitget_uta_contract import spec

        self.db = Database(path / "uta.sqlite")
        self.store = EventStore(self.db)
        self.journal = RawJournal(path / "raw", cipher=AESGCMCipher("test", b"z" * 32))
        self.spec = spec()
        self.reducers = (
            execution_reducer(
                OMS(),
                Ledger((self.spec,)),
                OMSState("bitget", "DEMO", "demo"),
                LedgerState("bitget", "DEMO", "demo"),
            ),
            recovery_reducer(),
            protection_reducer(),
        )
        self.producers = (protection_producer(),)
        self.engine = Engine(
            "uta-test",
            self.store,
            raw_watermark=self.journal.sync,
            reducers=self.reducers,
            producers=self.producers,
            code_hash="uta-test",
            schema_hash="uta-test",
        )
        self.time = clock()
        self.transport = RecoveryHTTP(fault, opening)
        self.socket = ScriptedSocket()
        self.adapter = BitgetUTAAdapter(
            self.transport,
            self.journal,
            credentials(),
            specs=(self.spec,),
            clock=lambda: self.time,
            sleep=self.sleep,
            websocket_connect=self.socket.connect,
            verified_protection_bindings=(("stop-1", "group"),),
            protection_capability_evidence="synthetic-emulator-contract",
        )
        eligible_fixture(self.adapter)
        self.Decimal = Decimal

    async def sleep(self, seconds):
        self.time += int(seconds * 1000000000)
        await asyncio.sleep(0)

    def send(self, payload):
        self.engine.commit(
            self.engine.process(
                incoming(payload, time=self.time, instrument=self.spec.instrument_id)
            )
        )

    @property
    def state(self):
        from quantdesk.execution.order_state import OMSState

        return OMSState.from_bytes(self.engine.state.get("oms", "state-v1"))

    @property
    def portfolio(self):
        from quantdesk.portfolio.ledger import LedgerState

        return LedgerState.from_bytes(self.engine.state.get("ledger", "state-v1"))

    async def close(self):
        await self.adapter.close()
        self.db.close()
        self.journal.close()


async def recovery_case(
    path,
    fault=None,
    use_sockets=False,
    order_changes=None,
    stop_changes=None,
    old_canceled=False,
    private_order_changes=None,
    position_changes=None,
    second_stop_changes=None,
    second_stop_rows=None,
    second_extra_stop_rows=None,
):
    from decimal import Decimal

    from quantdesk.core.events import CashTransfer
    from quantdesk.execution.intents import OrderApproved
    from quantdesk.execution.reconciliation import Reconciler
    from tests.support.oms_case import instruction
    from tests.unit.test_oms import gateway

    account = RecoveryAccount(path, fault=fault)
    account.transport.order_changes = order_changes or {}
    account.transport.stop_changes = stop_changes or {}
    account.transport.position_changes = position_changes or {}
    servers = await start_loopback_exchange(account) if use_sockets else ()
    ownership = None
    try:
        account.send(CashTransfer("deposit", Decimal("1000"), "USDT", "IN", account.time))
        if old_canceled:
            from quantdesk.core.events import OrderReport

            old = instruction(
                "old-client",
                instrument_id=account.spec.instrument_id,
                quantity_lots=100,
                price_ticks=1000,
                native_trigger_basis="MARK",
                native_trigger_value=Decimal("90"),
                protection_group_id="old-group",
                expires_at_ns=account.time + 60000000000,
            )
            account.send(OrderApproved(old, Decimal("10"), Decimal("1")))
            account.send(OrderReport("old-client", "old-native", "CANCELED", 0, account.time))
        value = instruction(
            instrument_id=account.spec.instrument_id,
            quantity_lots=100,
            price_ticks=1000,
            native_trigger_basis="MARK",
            native_trigger_value=Decimal("90"),
            protection_group_id="group",
            expires_at_ns=account.time + 60000000000,
        )
        account.send(OrderApproved(value, Decimal("10"), Decimal("1")))
        await account.adapter.connect_private()
        router, _, ownership, policy = gateway(account, account.adapter)
        await router.dispatch_ready()
        if private_order_changes:
            row = {
                "category": "USDT-FUTURES",
                "symbol": "BTCUSDT",
                "clientOid": "client",
                "orderId": "venue-order",
                "orderStatus": "filled",
                "cumExecQty": "0.100",
                "updatedTime": "1789200000123",
                "qty": "0.100",
                "side": "buy",
                "price": "100",
                "orderType": "limit",
                "timeInForce": "gtc",
                "reduceOnly": "no",
                "marginMode": "isolated",
                "holdMode": "one_way_mode",
                "stopLoss": "90",
                "slTriggerBy": "mark",
                "slOrderType": "market",
                **private_order_changes,
            }
            await account.socket.queue.put(
                json.dumps({"arg": {"instType": "UTA", "topic": "order"}, "data": [row]})
            )
            await account.adapter.private.changed.wait()
        from dataclasses import replace

        policy[0] = replace(policy[0], entries_allowed=False, reconciled=False)
        await account.adapter.private.close()
        start = account.time // 1000000 - (91 * 86400000 if fault == "history_gap" else 1000)
        runner = Reconciler(
            account.engine,
            account.adapter,
            lambda payload: incoming(payload, time=account.time),
            clock=lambda: account.time,
            sleep=account.sleep,
            timeout_ns=4000000000,
        )
        result = await runner.run(start_ms=start)
        first_protection = {key: json.loads(data) for key, data in account.engine.state.protection}
        first_cursor = result.cursor_ms
        second_seq = account.engine.state.engine_seq
        checkpoint = account.engine.checkpoint()
        if any(
            value is not None
            for value in (second_stop_changes, second_stop_rows, second_extra_stop_rows)
        ):
            assert result.status == "CONVERGED"
            account.transport.stop_changes = second_stop_changes or {}
            account.transport.stop_rows = second_stop_rows
            account.transport.extra_stop_rows = second_extra_stop_rows or []
            result = await runner.run()
        await router.dispatch_ready()
        transactions = account.portfolio.transactions
        records = account.store.read_after(0)
        from quantdesk.core.engine import Engine, EngineMode

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
        return {
            "distinct_submitted_client_ids": len(
                {r["clientOid"] for r in account.transport.accepted}
            ),
            "missing_execution_applied_once": len(account.state.executions) == 1
            and len(transactions) == 2,
            "position_matches_exchange": account.portfolio.position(
                account.spec.instrument_id
            ).signed_lots
            == 100,
            "blind_snapshot_overwrites": sum(
                r.envelope.event_type == "LedgerAdjustmentApproved" for r in records
            ),
            "entries_before_convergence": max(0, len(account.transport.accepted) - 1),
            "converged": result.status == "CONVERGED",
            "cursor_committed": result.cursor_ms is not None,
            "reasons": result.reasons,
            "observations": result.observations,
            "lifecycle": account.state.order("client").lifecycle,
            "protection": {key: json.loads(data) for key, data in account.engine.state.protection},
            "action_facts": [
                json.loads(r.envelope.payload)
                for r in records
                if r.envelope.event_type == "ProtectionActionsRequired"
            ],
            "order_evidence": [
                json.loads(r.envelope.payload)
                for r in records
                if r.envelope.event_type == "OrderContractObserved"
            ],
            "first_protection": first_protection,
            "replayed_protection": restored.state.protection,
            "recorded_protection": account.engine.state.protection,
            "replayed_latches": restored.state.risk_latches,
            "recorded_latches": account.engine.state.risk_latches,
            "cursor_unchanged": result.cursor_ms == first_cursor,
            "new_protection_events": [
                r.envelope
                for r in records
                if r.envelope.engine_seq > second_seq
                and r.envelope.event_type in {"ProtectionReview", "VenueObservation"}
            ],
        }
    finally:
        if ownership:
            ownership.close()
        await account.close()
        for server in servers:
            server.close()
            await server.wait_closed()


async def opening_case(path, fault=None):
    from quantdesk.core.engine import Engine, EngineMode
    from quantdesk.execution.reconciliation import Reconciler

    account = RecoveryAccount(path, opening=True, fault=fault)
    try:
        checkpoint = account.engine.checkpoint()
        runner = Reconciler(
            account.engine,
            account.adapter,
            lambda payload: incoming(payload, time=account.time),
            clock=lambda: account.time,
            sleep=account.sleep,
            timeout_ns=4000000000,
        )
        result = await runner.run(opening=True)
        cash = account.portfolio.balance("cash:USDT", "USDT")
        count = len(account.portfolio.transactions)
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
        runner = Reconciler(
            restored,
            account.adapter,
            lambda payload: incoming(payload, time=account.time),
            clock=lambda: account.time,
            sleep=account.sleep,
            timeout_ns=4000000000,
        )
        await runner.run(opening=True)
        return {
            "converged": result.status == "CONVERGED",
            "cash": str(cash),
            "opening_transactions": count,
            "restart_cash": str(account.portfolio.balance("cash:USDT", "USDT")),
            "raw_evidence": len(result.observation_ids),
        }
    finally:
        await account.close()


def bitget_uta_accept_then_disconnect(**overrides):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        return asyncio.run(recovery_case(Path(directory), fault=overrides.get("fault")))


def eligible_fixture(adapter):
    from quantdesk.venues.bitget_uta.recovery import account_profile

    info, settings, assets = profile_rows()
    adapter.last_profile = account_profile(info, settings, assets, (), ("BTCUSDT",))
    adapter.protection_capability_evidence = "synthetic-emulator-contract"


async def start_loopback_exchange(account):
    """Real TCP/WS framing outside the core; same deterministic venue state script."""
    from websockets.asyncio.server import serve

    from quantdesk.venues.bitget_uta.private_ws import websocket_connect
    from quantdesk.venues.bitget_uta.rest import Request, StreamHTTPTransport

    async def http_handler(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("ascii").split("\r\n")
            method, target, _ = lines[0].split(" ")
            headers = dict(line.split(": ", 1) for line in lines[1:] if line)
            body = await reader.readexactly(int(headers["Content-Length"]))
            response = await account.transport.handoff(
                Request(method, target, body, True, account.time, head + body)
            )
            framing = (
                f"HTTP/1.1 {response.status} Scripted\r\n"
                f"Content-Length: {len(response.body)}\r\n\r\n"
            )
            writer.write(framing.encode() + response.body)
            await writer.drain()
        except TimeoutError:
            # Venue accepted the order but intentionally closes before ACK bytes.
            writer.close()
        finally:
            writer.close()
            await writer.wait_closed()

    async def ws_handler(socket):
        async for frame in socket:
            if frame == "ping":
                await socket.send("pong")
                continue
            row = json.loads(frame)
            if row["op"] == "login":
                assert len(row["args"][0]["timestamp"]) == 13
                await socket.send('{"event":"login","code":"0"}')
            elif row["op"] == "subscribe":
                for arg in row["args"]:
                    assert arg["instType"] == "UTA"
                    await socket.send(json.dumps({"event": "subscribe", "arg": arg}))
                await socket.send(
                    json.dumps(
                        {
                            "arg": {"instType": "UTA", "topic": "account"},
                            "data": profile_rows()[2]["assets"],
                            "action": "snapshot",
                        }
                    )
                )

    http_server = await asyncio.start_server(http_handler, "127.0.0.1", 0)
    ws_server = await serve(ws_handler, "127.0.0.1", 0)
    port = http_server.sockets[0].getsockname()[1]
    wsport = ws_server.sockets[0].getsockname()[1]
    account.adapter.rest.transport = StreamHTTPTransport(f"http://127.0.0.1:{port}")
    account.adapter.private.base_url = f"ws://127.0.0.1:{wsport}"
    account.adapter.private.connector = websocket_connect
    return http_server, ws_server

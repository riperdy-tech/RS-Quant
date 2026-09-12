"""UTA protocol tests; all account traffic uses a scripted local boundary."""

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from quantdesk.core.events import BookSnapshot, ExecutionReport
from quantdesk.data.orderbook.builder import BookBuilder
from quantdesk.persistence.raw_journal import AESGCMCipher, RawJournal
from quantdesk.venues.instruments import InstrumentSpec


def spec():
    return InstrumentSpec.create(
        instrument_id="bitget:USDT-FUTURES:BTC:USDT:USDT:BTCUSDT",
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("100"),
    )


@pytest.fixture
def journal(tmp_path):
    value = RawJournal(tmp_path / "raw", cipher=AESGCMCipher("test", b"x" * 32))
    yield value
    value.close()


@pytest.fixture(autouse=True)
def loopback_network_tripwire(monkeypatch):
    import socket

    original = socket.getaddrinfo

    def checked(host, *args, **kwargs):
        if host not in {"localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}:
            raise AssertionError("contract tests forbid nonloopback DNS/network")
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", checked)


def test_signs_exact_transmitted_utf8_bytes_and_never_logs_credentials():
    from quantdesk.venues.bitget_uta.auth import Credentials, signed_headers

    credentials = Credentials("test-key", "test-secret", "test-passphrase", demo=True)
    body = '{"symbol":"BTCUSDT","note":"한글"}'.encode()
    headers = signed_headers(credentials, 1789200000123, "POST", "/api/v3/trade/place-order", body)
    message = b"1789200000123POST/api/v3/trade/place-order" + body
    expected = base64.b64encode(hmac.new(b"test-secret", message, hashlib.sha256).digest()).decode()
    assert headers["ACCESS-SIGN"] == expected
    assert headers["ACCESS-TIMESTAMP"] == "1789200000123"
    assert "test-secret" not in repr(credentials) and "test-key" not in repr(credentials)


def test_normalization_keeps_exact_fill_identity_fee_and_timestamp():
    from quantdesk.venues.bitget_uta.normalize import Normalizer

    value = Normalizer((spec(),)).fill(
        {
            "category": "usdt-futures",
            "symbol": "BTCUSDT",
            "execId": "9876543210123456789",
            "execLinkId": "link-not-an-alias",
            "orderId": "venue-1",
            "clientOid": "demo-1",
            "side": "buy",
            "execQty": "0.003",
            "execPrice": "100.1",
            "execTime": "1789200000123",
            "feeDetail": [{"feeCoin": "USDT", "fee": "-0.000001"}],
            "tradeScope": "maker",
            "execPnl": "0",
        },
        receipt_ns=1789200000124000000,
    )
    assert isinstance(value, ExecutionReport)
    assert value.native_execution_id == "9876543210123456789"
    assert value.executed_lots == 3 and value.price == Decimal("100.1")
    assert value.fee_amount == Decimal("-0.000001") and value.maker is True
    assert value.event_ns == 1789200000123000000


@pytest.mark.parametrize("bad", [1.2, True, "NaN", "0.0001"])
def test_financial_wire_values_cannot_round_into_lots(bad):
    from quantdesk.venues.bitget_uta.normalize import Normalizer

    with pytest.raises((ValueError, TypeError)):
        Normalizer((spec(),)).order(
            {
                "category": "USDT-FUTURES",
                "symbol": "BTCUSDT",
                "clientOid": "demo-1",
                "orderId": "1",
                "orderStatus": "filled",
                "cumExecQty": bad,
                "updatedTime": "1789200000123",
            }
        )


def test_book_contract_accepts_first_overlap_then_requires_exact_pseq(journal):
    from quantdesk.venues.bitget_uta.normalize import Normalizer
    from quantdesk.venues.bitget_uta.public_ws import BitgetBookSequence

    normalizer = Normalizer((spec(),))
    builder = BookBuilder(BitgetBookSequence())

    def event(action, seq, pseq):
        payload = normalizer.book(
            "BTCUSDT",
            "books",
            action,
            {
                "b": [["100.0", "0.005"]],
                "a": [["100.1", "0.004"]],
                "seq": seq,
                "pseq": pseq,
                "ts": "1789200000123",
            },
        )
        from tests.support.bitget_case import incoming

        return incoming(payload, instrument=spec().instrument_id, channel="books")

    assert isinstance(
        normalizer.book(
            "BTCUSDT",
            "books5",
            "snapshot",
            {
                "b": [["100", "0.005"]],
                "a": [["100.1", "0.004"]],
                "seq": "100",
            },
        ),
        BookSnapshot,
    )
    assert builder.apply(event("snapshot", "100", "0")).state == "VALID"
    assert builder.apply(event("update", "110", "95")).state == "VALID"
    assert builder.apply(event("update", "130", "110")).state == "VALID"
    assert builder.apply(event("update", "140", "120")).state == "INVALID"
    with pytest.raises(ValueError, match="snapshot"):
        normalizer.book("BTCUSDT", "books5", "update", {"b": [], "a": [], "seq": "1"})


@pytest.mark.parametrize(
    "previous,current,prior",
    [("100", None, "100"), ("100", "101", None), ("100", "101", "0"), ("100", "99", "100")],
)
def test_missing_reset_or_reordered_book_sequence_fails_closed(previous, current, prior):
    from quantdesk.venues.bitget_uta.public_ws import BitgetBookSequence

    contract = BitgetBookSequence()
    assert contract.validate(None, "100", None, True) is None
    assert contract.validate(previous, current, prior, False) is not None


def test_rest_success_errors_cursor_cycle_and_safe_journal(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient, VenueError
    from tests.support.bitget_case import ScriptedHTTP, clock, credentials

    transport = ScriptedHTTP(
        [
            (
                "GET",
                "/api/v3/trade/fills?category=USDT-FUTURES&limit=100",
                200,
                {"code": "00000", "data": {"list": [{"execId": "3"}], "cursor": "3"}},
            ),
            (
                "GET",
                "/api/v3/trade/fills?category=USDT-FUTURES&cursor=3&limit=100",
                200,
                {"code": "00000", "data": {"list": [{"execId": "2"}], "cursor": "3"}},
            ),
            (
                "GET",
                "/api/v3/account/info",
                200,
                {"code": "40009", "msg": "bad signature", "data": None},
            ),
        ]
    )
    client = RestClient(transport, journal, credentials(), clock=clock, environment="DEMO")

    async def run():
        with pytest.raises(VenueError, match="CURSOR"):
            await client.pages("/api/v3/trade/fills", {"category": "USDT-FUTURES"})
        with pytest.raises(VenueError) as caught:
            await client.get("/api/v3/account/info")
        assert caught.value.category == "AUTH"

    asyncio.run(run())
    assert journal.sync().frame_ordinal == 6


def test_async_handoff_implementation_is_rejected_before_any_request(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient
    from tests.support.bitget_case import ScriptedHTTP, clock, credentials

    class Deferred(ScriptedHTTP):
        async def handoff(self, request):
            return await super().handoff(request)

    with pytest.raises(TypeError, match="synchronous"):
        RestClient(Deferred([]), journal, credentials(), clock=clock)


def test_instrument_discovery_uses_v3_multipliers_and_retains_provenance():
    from quantdesk.venues.bitget_uta.normalize import instrument

    value = instrument(
        {
            "category": "USDT-FUTURES",
            "symbol": "BTCUSDT",
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "type": "perpetual",
            "priceMultiplier": "0.1",
            "quantityMultiplier": "0.001",
            "pricePrecision": "1",
            "quantityPrecision": "3",
            "minOrderQty": "0.001",
            "maxOrderQty": "100",
            "minOrderAmount": "5",
            "status": "online",
            "minLeverage": "1",
            "maxLeverage": "100",
            "fundInterval": "8",
        },
        1789200000000000000,
    )
    assert value.quantity_to_lots("0.003") == 3
    assert value.price_to_ticks("100.1") == 1001
    assert value.known_from_ns == 1789200000000000000
    assert value.min_notional == Decimal("5") and value.funding_schedule == ("interval_hours:8",)


def test_private_subscription_ack_required_and_bytes_sent_before_response_await(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, clock, credentials
    from tests.support.oms_case import instruction

    transport = ScriptedHTTP(
        [
            (
                "POST",
                "/api/v3/trade/place-order",
                200,
                {"code": "00000", "data": {"clientOid": "client", "orderId": "v1"}},
            )
        ]
    )
    socket = ScriptedSocket()
    adapter = BitgetUTAAdapter(
        transport,
        journal,
        credentials(),
        specs=(spec(),),
        clock=clock,
        websocket_connect=socket.connect,
    )
    from tests.support.bitget_case import eligible_fixture

    eligible_fixture(adapter)
    order = instruction(
        instrument_id=spec().instrument_id,
        quantity_lots=100,
        price_ticks=1000,
        expires_at_ns=clock() + 10000000000,
        native_trigger_basis="MARK",
        native_trigger_value=Decimal("90"),
        protection_group_id="group",
    )

    async def run():
        with pytest.raises(PermissionError, match="private"):
            await adapter.prepare(order)
        await adapter.connect_private()
        await adapter.prepare(order)
        response = adapter.handoff(order)
        assert len(transport.requests) == 1
        body = json.loads(transport.requests[0].body)
        assert body["marginMode"] == "isolated" and body["qty"] == "0.100"
        assert body["clientOid"] == "client" and body["reduceOnly"] == "no"
        assert b"POST /api/v3/trade/place-order HTTP/1.1\r\n" in transport.requests[0].wire
        result = await response
        assert result.accepted and result.venue_order_id == "v1"
        await adapter.close()

    asyncio.run(run())
    subscriptions = [m for m in socket.sent if isinstance(m, dict) and m.get("op") == "subscribe"]
    assert {a["topic"] for m in subscriptions for a in m["args"]} == {
        "order",
        "account",
        "position",
        "fill",
    }
    assert all(a["instType"] == "UTA" for m in subscriptions for a in m["args"])


@pytest.mark.parametrize(
    "code,category",
    [("40010", "TIMEOUT"), ("40725", "TIMEOUT"), ("45001", "TIMEOUT"), ("45110", "VENUE_REJECTED")],
)
def test_venue_uncertain_codes_retain_oms_uncertainty(journal, code, category):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, clock, credentials
    from tests.support.oms_case import instruction

    transport = ScriptedHTTP(
        [("POST", "/api/v3/trade/place-order", 200, {"code": code, "data": None})]
    )
    socket = ScriptedSocket()
    adapter = BitgetUTAAdapter(
        transport,
        journal,
        credentials(),
        specs=(spec(),),
        clock=clock,
        websocket_connect=socket.connect,
    )
    from tests.support.bitget_case import eligible_fixture

    eligible_fixture(adapter)
    order = instruction(
        instrument_id=spec().instrument_id,
        quantity_lots=100,
        price_ticks=1000,
        expires_at_ns=clock() + 10000000000,
        native_trigger_basis="MARK",
        native_trigger_value=Decimal("90"),
        protection_group_id="group",
    )

    async def run():
        await adapter.connect_private()
        await adapter.prepare(order)
        result = await adapter.handoff(order)
        assert not result.accepted and result.error_code == category
        assert len(transport.requests) == 1
        await adapter.close()

    asyncio.run(run())


def test_public_stream_normalizes_real_trade_and_book_and_disconnect_invalidates(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, clock

    socket = ScriptedSocket()
    adapter = BitgetUTAAdapter(
        ScriptedHTTP([]), journal, specs=(spec(),), clock=clock, websocket_connect=socket.connect
    )

    async def run():
        await adapter.connect_public()
        await socket.queue.put(
            json.dumps(
                {
                    "arg": {
                        "instType": "usdt-futures",
                        "topic": "publicTrade",
                        "symbol": "BTCUSDT",
                    },
                    "data": [
                        {
                            "i": "t1",
                            "p": "100.1",
                            "v": "0.003",
                            "S": "buy",
                            "T": "1789200000123",
                        }
                    ],
                    "action": "snapshot",
                }
            )
        )
        event = await adapter.receive()
        assert event.event_type == "Trade" and event.raw_ref
        assert json.loads(event.payload)["size_lots"] == 3
        await socket.queue.put(ConnectionError("disconnect"))
        event = await adapter.receive()
        assert event.event_type == "ConnectionChanged"
        assert json.loads(event.payload)["connected"] is False
        await adapter.close()

    asyncio.run(run())


def test_sandbox_binding_and_receive_window_never_emit_mainnet_write(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient
    from tests.support.bitget_case import ScriptedHTTP, credentials

    now = [1000000000]
    transport = ScriptedHTTP([])
    with pytest.raises(PermissionError, match="LIVE"):
        RestClient(transport, journal, credentials(), environment="LIVE")
    with pytest.raises(PermissionError, match="demo"):
        RestClient(transport, journal, replace(credentials(), demo=False), environment="SANDBOX")
    client = RestClient(
        transport, journal, credentials(), environment="SANDBOX", clock=lambda: now[0]
    )
    with pytest.raises(PermissionError, match="disarmed"):
        asyncio.run(client.prepare("POST", "/api/v3/trade/place-order", body={}))
    assert transport.requests == []


def test_attached_stop_maps_explicit_trigger_and_unverified_standalone_fails_closed():
    from quantdesk.core.types import OrderType, Side
    from quantdesk.venues.bitget_uta.orders import order_request
    from tests.support.oms_case import instruction

    value = instruction(
        instrument_id=spec().instrument_id,
        side=Side.SELL,
        order_type=OrderType.STOP,
        reduce_only=True,
        native_trigger_basis="MARK",
        native_trigger_value=Decimal("90"),
        protection_group_id="group",
        quantity_lots=100,
        price_ticks=None,
    )
    with pytest.raises(ValueError, match="standalone"):
        order_request(value, spec())
    value = replace(
        value, order_type=OrderType.LIMIT, side=Side.BUY, reduce_only=False, price_ticks=1000
    )
    path, body = order_request(value, spec())
    assert path == "/api/v3/trade/place-order"
    assert body["stopLoss"] == "90" and body["slTriggerBy"] == "mark"
    with pytest.raises(ValueError, match="trigger"):
        order_request(replace(value, reduce_only=True), spec())
    with pytest.raises(ValueError, match="trigger"):
        order_request(replace(value, native_trigger_basis="INDEX"), spec())


def test_unverified_account_and_protection_capability_cannot_prepare_entry(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, clock, credentials
    from tests.support.oms_case import instruction

    adapter = BitgetUTAAdapter(
        ScriptedHTTP([]),
        journal,
        credentials(),
        specs=(spec(),),
        clock=clock,
        websocket_connect=ScriptedSocket().connect,
    )

    async def run():
        await adapter.connect_private()
        with pytest.raises(PermissionError, match=r"profile|protection"):
            await adapter.prepare(instruction(instrument_id=spec().instrument_id))
        await adapter.close()

    asyncio.run(run())


def test_demo_default_websocket_never_contacts_real_endpoint(journal, monkeypatch):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter, private_ws
    from tests.support.bitget_case import ScriptedHTTP, credentials

    async def forbidden(*args, **kwargs):
        raise AssertionError("nonlocal connector must never be invoked")

    monkeypatch.setattr(private_ws, "connect", forbidden)
    with pytest.raises(PermissionError, match="loopback"):
        adapter = BitgetUTAAdapter(ScriptedHTTP([]), journal, credentials(), specs=(spec(),))
        asyncio.run(adapter.connect_private())


def test_periodic_heartbeat_is_not_starved_by_busy_data_stream(journal):
    from quantdesk.venues.bitget_uta.private_ws import StreamSession
    from quantdesk.venues.bitget_uta.rest import RestClient
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket

    socket = ScriptedSocket()
    session = StreamSession(
        RestClient(ScriptedHTTP([]), journal),
        private=False,
        args=(),
        connector=socket.connect,
        heartbeat_seconds=0.01,
        capacity=1000,
    )

    async def run():
        await session.connect()
        for _ in range(40):
            await socket.queue.put('{"data":[]}')
            await asyncio.sleep(0.001)
        assert "ping" in socket.sent
        assert session.ready
        await session.close()

    asyncio.run(run())


def test_transport_discard_prevents_known_unsent_preparation_leak(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient
    from tests.support.bitget_case import ScriptedHTTP, credentials

    transport = ScriptedHTTP([])
    client = RestClient(transport, journal, credentials())
    assert callable(client.discard_prepared)


def test_real_loopback_http_and_websocket_protocol(journal):
    from websockets.asyncio.server import serve

    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from quantdesk.venues.bitget_uta.rest import StreamHTTPTransport
    from tests.support.bitget_case import credentials

    observed = []

    async def http_handler(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            observed.append(head)
            payload = b'{"code":"00000","data":{"userId":"fixture"}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            await writer.drain()
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
                await socket.send('{"event":"login","code":"0"}')
            elif row["op"] == "subscribe":
                for arg in row["args"]:
                    await socket.send(json.dumps({"event": "subscribe", "arg": arg}))

    async def run():
        server = await asyncio.start_server(http_handler, "127.0.0.1", 0)
        async with server, serve(ws_handler, "127.0.0.1", 0) as sockets:
            port = server.sockets[0].getsockname()[1]
            wsport = sockets.sockets[0].getsockname()[1]
            adapter = BitgetUTAAdapter(
                StreamHTTPTransport(f"http://127.0.0.1:{port}"),
                journal,
                credentials(),
                specs=(spec(),),
                websocket_base_url=f"ws://127.0.0.1:{wsport}",
            )
            await adapter.connect_private()
            result = await adapter.rest.get("/api/v3/account/info")
            assert result.data["userId"] == "fixture" and result.raw_ref
            assert adapter.private_healthy()
            await adapter.close()

    asyncio.run(run())
    assert b"GET /api/v3/account/info HTTP/1.1" in observed[0]
    assert b"ACCESS-SIGN:" in observed[0]


def test_funding_transfers_and_unmapped_corrections_preserve_exact_economics():
    from quantdesk.core.events import CashTransfer, FundingSettlement
    from quantdesk.venues.bitget_uta.normalize import Normalizer, VenueObservation

    normalizer = Normalizer((spec(),))
    row = {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "id": "cash-1",
        "coin": "USDT",
        "type": "MARGIN_SETTLE_FEE_USER_OUT",
        "amount": "-0.00000001",
        "ts": "1789200000123",
    }
    value = normalizer.financial(row)
    assert isinstance(value, FundingSettlement) and value.amount == Decimal("-0.00000001")
    value = normalizer.financial({**row, "type": "TRANSFER_IN", "amount": "10"})
    assert (
        isinstance(value, CashTransfer)
        and value.direction == "IN"
        and value.amount == Decimal("10")
    )
    value = normalizer.financial({**row, "type": "UNKNOWN_FEE_CORRECTION"})
    assert isinstance(value, VenueObservation) and value.reason == "UNMATCHED_CASH_MOVEMENT"


def test_pagination_short_page_is_not_completeness_and_missing_cursor_blocks(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient, VenueError
    from tests.support.bitget_case import ScriptedHTTP, clock, credentials

    client = RestClient(
        ScriptedHTTP(
            [
                (
                    "GET",
                    "/api/v3/trade/unfilled-orders?limit=100",
                    200,
                    {"code": "00000", "data": {"list": [{"orderId": "one"}]}},
                )
            ]
        ),
        journal,
        credentials(),
        clock=clock,
    )
    with pytest.raises(VenueError, match="CURSOR"):
        asyncio.run(client.pages("/api/v3/trade/unfilled-orders"))


def test_private_overflow_and_malformed_payload_require_recovery(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, credentials

    socket = ScriptedSocket()
    adapter = BitgetUTAAdapter(
        ScriptedHTTP([]),
        journal,
        credentials(),
        specs=(spec(),),
        websocket_connect=socket.connect,
        stream_capacity=1,
    )

    async def run():
        await adapter.connect_private()
        await socket.queue.put('{"arg":{"instType":"UTA","topic":"fill"},"data":[{}]}')
        await socket.queue.put('{"arg":{"instType":"UTA","topic":"fill"},"data":[{}]}')
        while adapter.private.ready:
            await asyncio.sleep(0)
        assert not adapter.private_healthy()
        assert adapter.drain_private()[0].event_type == "DataGap"
        await adapter.close()

    asyncio.run(run())


def test_throttle_retries_reads_only_and_endpoint_buckets_are_separate(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient
    from tests.support.bitget_case import ScriptedHTTP, credentials

    now = [1000000000]
    delays = []

    async def sleep(seconds):
        delays.append(seconds)
        now[0] += int(seconds * 1000000000)

    transport = ScriptedHTTP(
        [
            ("GET", "/api/v3/account/info", 429, {"code": "429", "data": None}),
            ("GET", "/api/v3/account/info", 200, {"code": "00000", "data": {}}),
        ]
    )
    client = RestClient(transport, journal, credentials(), clock=lambda: now[0], sleep=sleep)
    asyncio.run(client.get("/api/v3/account/info"))
    assert delays == [0.5] and len(transport.requests) == 2
    assert "/api/v3/account/info" in client.limiter.windows
    assert "/api/v3/trade/place-order" not in client.limiter.windows


def test_expired_prepared_request_never_hands_bytes_to_transport(journal):
    from quantdesk.venues.bitget_uta.rest import RestClient
    from tests.support.bitget_case import ScriptedHTTP, credentials

    now = [1000000000]
    transport = ScriptedHTTP(
        [("POST", "/api/v3/trade/place-order", 200, {"code": "00000", "data": {}})]
    )
    client = RestClient(transport, journal, credentials(), clock=lambda: now[0])
    request = asyncio.run(client.prepare("POST", "/api/v3/trade/place-order", body={}))
    now[0] += 5000000001
    with pytest.raises(TimeoutError):
        client.handoff(request)
    assert transport.requests == []


def test_buffered_stream_keeps_original_connection_epoch(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from quantdesk.venues.bitget_uta.rest import Observation
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, clock

    adapter = BitgetUTAAdapter(
        ScriptedHTTP([]), journal, specs=(spec(),), websocket_connect=ScriptedSocket().connect
    )
    adapter.public.epoch = 8
    observation = Observation(
        {}, "raw", clock(), 1, None, connection_epoch="7", receive_monotonic_ns=123
    )
    from quantdesk.core.events import MarkPrice

    event = adapter.event(MarkPrice(Decimal("1"), 0, "fixture"), observation, private=False)
    assert event.connection_epoch == "7" and event.receive_monotonic_ns == 123


def test_versioned_sanitized_protocol_fixture(journal):
    from pathlib import Path

    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from quantdesk.venues.bitget_uta.rest import Observation
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, clock

    fixture = json.loads(Path("fixtures/bitget_uta/v3-2026-09-13.json").read_text())
    adapter = BitgetUTAAdapter(
        ScriptedHTTP([]), journal, specs=(spec(),), websocket_connect=ScriptedSocket().connect
    )
    for key, private, expected in (
        ("public_trade", False, "Trade"),
        ("book", False, "BookSnapshot"),
        ("fill", True, "ExecutionReport"),
    ):
        value = Observation(fixture[key], "synthetic", clock(), 1, None)
        result = adapter.normalize_stream(value, private=private)
        assert result[0].event_type == expected
    assert adapter.normalizer.order(fixture["order"]["data"]).cumulative_fill_lots == 100


def test_explicit_nonloopback_demo_origin_rejected_at_construction(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket

    with pytest.raises(PermissionError, match="loopback"):
        BitgetUTAAdapter(
            ScriptedHTTP([]),
            journal,
            specs=(spec(),),
            websocket_connect=ScriptedSocket().connect,
            websocket_base_url="wss://ws.bitget.com",
        )


def test_reconnecting_invalidates_previous_account_profile(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import (
        ScriptedHTTP,
        ScriptedSocket,
        credentials,
        eligible_fixture,
    )

    async def run():
        socket = ScriptedSocket()
        adapter = BitgetUTAAdapter(
            ScriptedHTTP([]), journal, credentials(), websocket_connect=socket.connect
        )
        try:
            await adapter.connect_private()
            eligible_fixture(adapter)
            await adapter.private.close()
            await adapter.connect_private()
            assert adapter.last_profile is None
        finally:
            await adapter.close()

    asyncio.run(run())


def test_account_profile_reads_foreign_position_categories(journal):
    from quantdesk.venues.bitget_uta import BitgetUTAAdapter
    from tests.support.bitget_case import ScriptedHTTP, ScriptedSocket, credentials, profile_rows

    async def run():
        script = [
            ("GET", f"/api/v3/account/{name}", 200, {"code": "00000", "data": data})
            for name, data in zip(("info", "settings", "assets"), profile_rows(), strict=True)
        ]
        for category in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            rows = (
                [
                    {
                        "category": category,
                        "symbol": "BTCUSD",
                        "marginMode": "isolated",
                        "holdMode": "one_way_mode",
                    }
                ]
                if category == "COIN-FUTURES"
                else []
            )
            script.append(
                (
                    "GET",
                    f"/api/v3/position/current-position?category={category}",
                    200,
                    {"code": "00000", "data": {"list": rows}},
                )
            )
        adapter = BitgetUTAAdapter(
            ScriptedHTTP(script),
            journal,
            credentials(),
            specs=(spec(),),
            websocket_connect=ScriptedSocket().connect,
        )
        try:
            profile = await adapter.read_account_profile()
            assert not profile.eligible and "FOREIGN_EXPOSURE" in profile.reasons
        finally:
            await adapter.close()

    asyncio.run(run())

"""Versioned UTA transport composition; state changes are journaled canonical inputs."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

from quantdesk.core.events import (
    AccountSnapshotObserved,
    CancelTransportResult,
    ConnectionChanged,
    DataGap,
    FundingRateAnnounced,
    IncomingEvent,
    MarkPrice,
    OrderInstruction,
    SubmitTransportResult,
    Trade,
    canonical_bytes,
)
from quantdesk.core.types import EventPayload, Side
from quantdesk.execution.intents import CancelRequested, GatewayInstruction
from quantdesk.execution.protection import NativeProtection, ProtectionReview
from quantdesk.execution.reconciliation import RecoveryWindow
from quantdesk.execution.router import KnownUnsentError
from quantdesk.persistence.raw_journal import RawJournal
from quantdesk.portfolio.arithmetic import exact_sum
from quantdesk.venues.bitget_uta.auth import Credentials
from quantdesk.venues.bitget_uta.normalize import (
    Normalizer,
    VenueObservation,
    decimal,
    identifier,
    instrument,
    timestamp,
)
from quantdesk.venues.bitget_uta.orders import client_id, order_request
from quantdesk.venues.bitget_uta.private_ws import Socket, StreamSession, websocket_connect
from quantdesk.venues.bitget_uta.recovery import AccountProfile, account_profile
from quantdesk.venues.bitget_uta.rest import (
    VERSION,
    HTTPTransport,
    Observation,
    Request,
    RestClient,
    VenueError,
)
from quantdesk.venues.capabilities import VenueCapabilities
from quantdesk.venues.instruments import InstrumentSpec

__all__ = ["BitgetUTAAdapter"]


class BitgetUTAAdapter:
    capabilities = VenueCapabilities(
        order_types=frozenset({"LIMIT"}),
        time_in_force=frozenset({"GTC", "IOC", "FOK", "POST_ONLY"}),
        post_only=True,
        reduce_only=True,
        native_stops=True,
        client_ids=True,
        position_modes=frozenset({"one_way"}),
        margin_modes=frozenset({"isolated"}),
        sequence_contracts=frozenset({"bitget-uta-v3-books-2026-09-13"}),
        snapshot_recovery=True,
        rate_limits=(
            ("order", 10, 1),
            ("recovery", 20, 1),
            ("market", 20, 1),
            ("ws-control", 10, 1),
        ),
    )

    def __init__(
        self,
        transport: HTTPTransport,
        journal: RawJournal,
        credentials: Credentials | None = None,
        *,
        specs: tuple[InstrumentSpec, ...] = (),
        clock: Callable[[], int] = time.time_ns,
        environment: str = "DEMO",
        account_id: str = "demo",
        run_id: str = "uta-test",
        websocket_connect: Callable[[str], Awaitable[Socket]] = websocket_connect,
        allow_sandbox_writes: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        stream_capacity: int = 1000,
        verified_protection_bindings: tuple[tuple[str, str], ...] = (),
        protection_capability_evidence: str | None = None,
        websocket_base_url: str | None = None,
    ) -> None:
        self.rest = RestClient(
            transport,
            journal,
            credentials,
            clock=clock,
            environment=environment,
            allow_sandbox_writes=allow_sandbox_writes,
            sleep=sleep,
        )
        from quantdesk.venues.bitget_uta.private_ws import websocket_connect as real_connect

        if (
            environment == "DEMO"
            and websocket_connect is real_connect
            and websocket_base_url is None
        ):
            raise PermissionError("DEMO requires injected WebSocket or explicit loopback origin")
        self.normalizer = Normalizer(specs)
        self.account_id, self.run_id = account_id, run_id
        self.clock, self.environment = clock, environment
        self.private = StreamSession(
            self.rest,
            private=True,
            args=tuple(
                {"instType": "UTA", "topic": t} for t in ("order", "account", "position", "fill")
            ),
            connector=websocket_connect,
            capacity=stream_capacity,
            base_url=websocket_base_url,
        )
        self.public = StreamSession(
            self.rest,
            private=False,
            args=tuple(
                {"instType": "usdt-futures", "topic": topic, "symbol": symbol}
                for symbol in sorted(self.normalizer.specs)
                for topic in ("books", "publicTrade", "ticker")
            ),
            connector=websocket_connect,
            capacity=stream_capacity,
            base_url=websocket_base_url,
        )
        self._prepared: tuple[GatewayInstruction, Request, int] | None = None
        self._events: deque[IncomingEvent] = deque()
        self.last_profile: AccountProfile | None = None
        # Native child -> committed protection-group association. Empty until
        # externally verified; matching a symbol/price alone is not ownership.
        self.protection_bindings = dict(verified_protection_bindings)
        self.protection_capability_evidence = protection_capability_evidence

    def private_healthy(self) -> bool:
        return self.private.ready and self.private.failure is None

    def event(
        self,
        payload: EventPayload,
        observation: Observation,
        *,
        instrument_id: str | None = None,
        channel: str = "rest",
        private: bool = True,
        exchange_ns: int | None = None,
    ) -> IncomingEvent:
        return IncomingEvent(
            type(payload).__name__,
            1,
            self.run_id,
            self.account_id if private else None,
            "bitget",
            self.environment,
            instrument_id,
            channel,
            observation.connection_epoch,
            str(observation.receive_seq),
            None,
            exchange_ns,
            None,
            observation.receive_ns,
            observation.receive_monotonic_ns
            if observation.receive_monotonic_ns is not None
            else observation.receive_ns,
            observation.receive_ns,
            None,
            observation.raw_ref,
            observation.raw_ref,
            VERSION,
            canonical_bytes(payload),
        )

    async def discover_instruments(self) -> tuple[InstrumentSpec, ...]:
        observation = await self.rest.get(
            "/api/v3/market/instruments", {"category": "USDT-FUTURES"}
        )
        specs = tuple(
            instrument(row, observation.receive_ns)
            for row in observation.data
            if row.get("symbol") in {"BTCUSDT", "ETHUSDT"}
        )
        if not specs:
            raise ValueError("requested instruments unavailable")
        self.normalizer = Normalizer(specs)
        self.public.args = tuple(
            {"instType": "usdt-futures", "topic": topic, "symbol": symbol}
            for symbol in sorted(self.normalizer.specs)
            for topic in ("books", "publicTrade", "ticker")
        )
        for spec in specs:
            # InstrumentSpec is the value subtype; the canonical registry names
            # this shared payload InstrumentSpecUpdated.
            from dataclasses import replace

            event = self.event(spec, observation, instrument_id=spec.instrument_id, private=False)
            self._events.append(replace(event, event_type="InstrumentSpecUpdated"))
        return specs

    async def connect_public(self) -> None:
        if not self.normalizer.specs:
            await self.discover_instruments()
        await self.public.connect()

    async def connect_private(self) -> None:
        if self.private.epoch and not self.private_healthy():
            self.last_profile = None
        await self.private.connect()

    def _request_body(self, instruction: GatewayInstruction) -> tuple[str, dict[str, object]]:
        if not self.private_healthy():
            raise PermissionError("private subscriptions are not healthy")
        if isinstance(instruction, OrderInstruction):
            if not instruction.reduce_only:
                if self.last_profile is None or not self.last_profile.eligible:
                    raise PermissionError("account profile is unverified or unsupported")
                if not self.protection_capability_evidence or not instruction.native_trigger_value:
                    raise PermissionError("native protection capability is unverified")
            if (
                instruction.account_id != self.account_id
                or instruction.environment != self.environment
            ):
                raise PermissionError("instruction account/environment mismatch")
        try:
            if isinstance(instruction, OrderInstruction):
                spec = self.normalizer.specs[instruction.instrument_id.split(":")[-1]]
                return order_request(instruction, spec)
            return (
                "/api/v3/trade/cancel-order",
                {"clientOid": client_id(instruction.client_order_id), "category": "USDT-FUTURES"},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionError("current venue instruction filters failed") from exc

    async def prepare(self, instruction: GatewayInstruction) -> None:
        if self._prepared is not None:
            raise PermissionError("another instruction is prepared")
        path, body = self._request_body(instruction)
        epoch = self.private.epoch
        request = await self.rest.prepare("POST", path, body=body)
        try:
            if epoch != self.private.epoch or (path, body) != self._request_body(instruction):
                raise PermissionError("venue contract changed during preparation")
        except PermissionError:
            self.rest.discard_prepared()
            raise
        self._prepared = instruction, request, epoch

    def discard_prepared(self) -> None:
        self._prepared = None
        self.rest.discard_prepared()

    def handoff(
        self, instruction: GatewayInstruction
    ) -> Awaitable[SubmitTransportResult | CancelTransportResult]:
        prepared, self._prepared = self._prepared, None
        if (
            prepared is None
            or prepared[0] != instruction
            or prepared[2] != self.private.epoch
            or not self.private.ready
        ):
            self.rest.discard_prepared()
            raise KnownUnsentError("private stream changed or request is not prepared")
        try:
            path, body = self._request_body(instruction)
            if path != prepared[1].target or canonical_bytes(body) != prepared[1].body:
                raise PermissionError("prepared contract changed before transport")
        except PermissionError as exc:
            self.rest.discard_prepared()
            raise KnownUnsentError("venue authorization changed before transport") from exc
        future = self.rest.handoff(prepared[1])  # synchronous byte write, before returning
        return self._transport_result(instruction, future)

    async def dispatch(self, router: Any) -> tuple[str, ...]:
        """Dispatch only through the account writer's existing fenced router."""
        from quantdesk.execution.router import Router

        if not isinstance(router, Router) or router.venue is not self:
            raise PermissionError("dispatch requires this adapter's fenced account router")
        return await router.dispatch_ready()

    async def _transport_result(
        self, instruction: GatewayInstruction, future: Awaitable[Observation]
    ) -> SubmitTransportResult | CancelTransportResult:
        try:
            response = await future
            data = response.data
            if not isinstance(data, dict) or data.get("clientOid") != instruction.client_order_id:
                raise VenueError("RESPONSE_IDENTITY_MISMATCH", "UNKNOWN", ambiguous=True)
            if isinstance(instruction, CancelRequested):
                return CancelTransportResult(instruction.client_order_id, True, None)
            return SubmitTransportResult(
                instruction.instruction_id, True, data.get("orderId") or None, None
            )
        except VenueError as exc:
            code = "TIMEOUT" if exc.ambiguous else "VENUE_REJECTED"
            return (
                CancelTransportResult(instruction.client_order_id, False, code)
                if isinstance(instruction, CancelRequested)
                else SubmitTransportResult(instruction.instruction_id, False, None, code)
            )

    def normalize_stream(
        self, observation: Observation, *, private: bool
    ) -> tuple[IncomingEvent, ...]:
        data = observation.data
        arg = data["arg"]
        topic = arg["topic"]
        if arg["instType"] != ("UTA" if private else "usdt-futures"):
            raise ValueError("stream product mismatch")
        events: list[IncomingEvent] = []
        for row in data["data"]:
            symbol = row.get("symbol", arg.get("symbol"))
            spec = self.normalizer.specs.get(symbol)
            instrument_id = spec.instrument_id if spec else None
            payloads: list[EventPayload] = []
            if topic == "order":
                payloads.append(self.normalizer.order_contract(row, None, strict=private))
                payloads.append(self.normalizer.order(row))
                payloads.append(
                    VenueObservation(
                        "order-metadata",
                        identifier(row["orderId"]),
                        json.dumps(row, sort_keys=True),
                        "LIQUIDATION_OR_ADL"
                        if row.get("execType") in {"liquidation", "reduce", "offset"}
                        else None,
                    )
                )
            elif topic == "fill":
                payloads.append(self.normalizer.fill(row, receipt_ns=observation.receive_ns))
            elif topic in {"account", "position"}:
                payloads.append(
                    VenueObservation(
                        topic, str(observation.receive_seq), json.dumps(row, sort_keys=True)
                    )
                )
            elif topic.startswith("books"):
                payloads.append(self.normalizer.book(symbol, topic, data["action"], row))
            elif topic == "publicTrade" and spec:
                payloads.append(
                    Trade(
                        identifier(row["i"]),
                        spec.price_to_ticks(decimal(row["p"])),
                        spec.quantity_to_lots(decimal(row["v"])),
                        Side(row["S"].upper()),
                        tuple((key, identifier(row[key])) for key in ("L", "isRPI") if key in row),
                    )
                )
            elif topic == "ticker" and spec:
                if row.get("markPrice"):
                    payloads.append(
                        MarkPrice(
                            decimal(row["markPrice"]),
                            timestamp(row.get("ts", data.get("ts"))),
                            "bitget-mark",
                        )
                    )
                if row.get("fundingRate") is not None and row.get("nextFundingTime"):
                    payloads.append(
                        FundingRateAnnounced(
                            decimal(row["fundingRate"]), timestamp(row["nextFundingTime"])
                        )
                    )
            else:
                raise ValueError("unknown or unsupported stream payload")
            for payload in payloads:
                events.append(
                    self.event(
                        payload,
                        observation,
                        instrument_id=instrument_id,
                        channel=topic,
                        private=private,
                        exchange_ns=timestamp(row["T"])
                        if topic == "publicTrade"
                        else timestamp(row["ts"])
                        if row.get("ts") is not None
                        else None,
                    )
                )
        return tuple(events)

    def drain_private(self) -> tuple[IncomingEvent, ...]:
        events: list[IncomingEvent] = []
        for observation in self.private.drain():
            try:
                events.extend(self.normalize_stream(observation, private=True))
            except (KeyError, TypeError, ValueError):
                self.private.invalidate("QUARANTINED_PRIVATE_PAYLOAD")
                self.last_profile = None
                events.append(
                    self.event(
                        DataGap("private", None, None, "QUARANTINED_PRIVATE_PAYLOAD"), observation
                    )
                )
        return tuple(events)

    async def receive(self) -> IncomingEvent:
        while not self._events:
            for observation in self.public.drain():
                try:
                    self._events.extend(self.normalize_stream(observation, private=False))
                except (KeyError, ValueError, TypeError):
                    self._events.append(
                        self.event(
                            DataGap("public", None, None, "QUARANTINED_PUBLIC_PAYLOAD"),
                            observation,
                            private=False,
                        )
                    )
            if self.public.failure:
                ref, now, seq = self.rest.capture(
                    b'{"connected":false}',
                    "connection",
                    private=False,
                    epoch=str(self.public.epoch),
                )
                self._events.append(
                    self.event(
                        ConnectionChanged("public", False, self.public.failure),
                        Observation({}, ref, now, seq, None),
                        private=False,
                    )
                )
                self.public.failure = None
            if not self._events:
                await self.public.changed.wait()
        return self._events.popleft()

    async def read_account_profile(self) -> AccountProfile:
        info = await self.rest.get("/api/v3/account/info")
        settings = await self.rest.get("/api/v3/account/settings")
        assets = await self.rest.get("/api/v3/account/assets")
        rows = []
        for category in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            positions = await self.rest.get(
                "/api/v3/position/current-position", {"category": category}
            )
            if not isinstance(positions.data, dict) or not isinstance(
                positions.data.get("list"), list
            ):
                raise ValueError("position snapshot is not explicitly complete")
            rows.extend(positions.data["list"])
        self.last_profile = account_profile(
            info.data,
            settings.data,
            assets.data,
            tuple(rows),
            tuple(self.normalizer.specs),
        )
        return self.last_profile

    async def fetch_recovery_window(
        self, start_ms: int, end_ms: int, original_orders: tuple[OrderInstruction, ...]
    ) -> RecoveryWindow:
        if not self.private_healthy():
            raise PermissionError("private stream must be buffered before recovery reads")
        if not 0 <= start_ms <= end_ms:
            raise ValueError("invalid history window")
        reasons: set[str] = set()
        if start_ms < end_ms - 90 * 86400000:
            reasons.add("HISTORY_RETENTION_GAP")
            start_ms = end_ms - 90 * 86400000
        start_seq = self.rest.ordinal
        facts: list[IncomingEvent] = []
        evidence: list[str] = []
        originals = {order.client_order_id: order for order in original_orders}

        def observe(
            payload: EventPayload, obs: Observation, instrument_id: str | None = None
        ) -> None:
            facts.append(self.event(payload, obs, instrument_id=instrument_id))
            evidence.append(obs.raw_ref)

        def order_observed(row: dict[str, Any], obs: Observation) -> None:
            instruction = originals.get(row.get("clientOid", ""))
            contract = self.normalizer.order_contract(row, instruction)
            observe(contract, obs, instruction.instrument_id if instruction else None)
            if instruction is None or contract.discrepancies:
                reasons.add("ORDER_CONTRACT_CONFLICT" if instruction else "FOREIGN_ORDER")
                return
            spec = self.normalizer.spec(row)
            observe(self.normalizer.order(row), obs, spec.instrument_id)

        def order_rows(pages: tuple[Observation, ...], *, open_snapshot: bool = False) -> list[str]:
            clients = []
            for page in pages:
                evidence.append(page.raw_ref)
                for row in page.data["list"]:
                    if open_snapshot:
                        clients.append(str(row.get("clientOid", "")))
                    try:
                        order_observed(row, page)
                    except (ValueError, KeyError, TypeError):
                        reasons.add(
                            "FOREIGN_ORDER" if open_snapshot else "UNNORMALIZED_ORDER_HISTORY"
                        )
                        observe(
                            VenueObservation(
                                "order",
                                str(row.get("orderId", "unknown")),
                                json.dumps(row, sort_keys=True),
                                "UNSUPPORTED_ORDER",
                            ),
                            page,
                        )
            return clients

        open_ids = order_rows(
            await self.rest.pages("/api/v3/trade/unfilled-orders"), open_snapshot=True
        )
        for original in original_orders:
            try:
                detail = await self.rest.get(
                    "/api/v3/trade/order-info", {"clientOid": client_id(original.client_order_id)}
                )
                if detail.data.get("clientOid") != original.client_order_id:
                    raise ValueError("lookup changed original client identity")
                order_observed(detail.data, detail)
            except VenueError as exc:
                if exc.category in {"AUTH", "CLOCK"}:
                    raise
                reasons.add("ORIGINAL_ORDER_UNRESOLVED")
        # Documented 30-day maximum span; all pages in every span are retained.
        cursor = start_ms
        while cursor <= end_ms:
            through = min(end_ms, cursor + 30 * 86400000 - 1)
            times = {"startTime": str(cursor), "endTime": str(through)}
            order_rows(
                await self.rest.pages(
                    "/api/v3/trade/history-orders", {**times, "category": "USDT-FUTURES"}
                )
            )
            pages = await self.rest.pages(
                "/api/v3/trade/fills", {**times, "category": "USDT-FUTURES"}
            )
            for page in pages:
                evidence.append(page.raw_ref)
                for row in page.data["list"]:
                    spec = self.normalizer.spec(row)
                    observe(
                        self.normalizer.fill(row, receipt_ns=page.receive_ns),
                        page,
                        spec.instrument_id,
                    )
            for category in (
                "USDT-FUTURES",
                "COIN-FUTURES",
                "USDC-FUTURES",
                "SPOT",
                "MARGIN",
                "OTHER",
            ):
                pages = await self.rest.pages(
                    "/api/v3/account/financial-records", {**times, "category": category}
                )
                for page in pages:
                    evidence.append(page.raw_ref)
                    for row in page.data["list"]:
                        if category not in {"USDT-FUTURES", "OTHER"}:
                            reasons.add("FOREIGN_FINANCIAL_ACTIVITY")
                        payload = self.normalizer.financial(row)
                        if isinstance(payload, VenueObservation) and payload.reason:
                            reasons.add(payload.reason)
                        financial_spec = self.normalizer.specs.get(row.get("symbol"))
                        observe(
                            payload, page, financial_spec.instrument_id if financial_spec else None
                        )
            cursor = through + 1
        info = await self.rest.get("/api/v3/account/info")
        settings = await self.rest.get("/api/v3/account/settings")
        assets = await self.rest.get("/api/v3/account/assets")
        rows = []
        for category in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            positions = await self.rest.get(
                "/api/v3/position/current-position", {"category": category}
            )
            if not isinstance(positions.data, dict) or not isinstance(
                positions.data.get("list"), list
            ):
                raise ValueError("position snapshot is not explicitly complete")
            rows.extend(positions.data["list"])
            evidence.append(positions.raw_ref)
        profile = account_profile(
            info.data, settings.data, assets.data, tuple(rows), tuple(self.normalizer.specs)
        )
        self.last_profile = profile
        reasons.update(profile.reasons)
        evidence.extend((info.raw_ref, settings.raw_ref, assets.raw_ref))
        position_lots: dict[str, int] = {}
        for row in rows:
            if (
                row["category"].upper() != "USDT-FUTURES"
                or row["symbol"] not in self.normalizer.specs
            ):
                reasons.add("FOREIGN_EXPOSURE")
                continue
            spec = self.normalizer.spec(row)
            qty = decimal(row["total"])
            if qty != exact_sum(decimal(row["available"]), decimal(row["frozen"])):
                raise ValueError("position quantities conflict")
            if row["posSide"] not in {"long", "short"} or spec.instrument_id in position_lots:
                raise ValueError("ambiguous one-way position snapshot")
            position_lots[spec.instrument_id] = spec.quantity_to_lots(qty) * (
                1 if row["posSide"] == "long" else -1
            )
        snapshot = AccountSnapshotObserved(
            assets.raw_ref,
            tuple(
                sorted(
                    (identifier(r["coin"]), decimal(r["balance"])) for r in assets.data["assets"]
                )
            ),
            tuple(sorted(position_lots.items())),
            assets.receive_ns,
        )
        protected: set[str] = set()
        protective_rows = []
        native_protection: list[NativeProtection] = []
        protection_pages = await self.read_protection()
        native_sources: dict[str, Observation] = {}
        for page in protection_pages:
            evidence.append(page.raw_ref)
            protective_rows.extend(page.data)
            for row_index, row in enumerate(page.data):
                try:
                    native = identifier(row["orderId"])
                except (KeyError, TypeError, ValueError):
                    native = f"unidentified:{page.receive_seq}:{row_index}"
                    group = None  # audit label is never a native ownership identity
                else:
                    group = self.protection_bindings.get(native)
                candidates = [
                    order
                    for order in original_orders
                    if group and order.protection_group_id == group
                ]
                if len(candidates) != 1:
                    reasons.add("FOREIGN_OR_UNVERIFIED_PROTECTION")
                    observe(
                        VenueObservation(
                            "protection",
                            native,
                            json.dumps(row, sort_keys=True),
                            "FOREIGN_OR_UNVERIFIED_PROTECTION",
                        ),
                        page,
                    )
                    continue
                order = candidates[0]
                native_sources[order.protection_group_id or ""] = page
                try:
                    spec = self.normalizer.spec(row)
                    native_lots = spec.quantity_to_lots(decimal(row["qty"]))
                    trigger = decimal(row["stopLoss"])
                    status = identifier(row["status"])
                    # Validate string type before enum lookup: arbitrary JSON
                    # arrays/objects are unsafe evidence, never hash keys.
                    basis = {"mark": "MARK", "market": "LAST"}[identifier(row["slTriggerBy"])]
                    native_side = {"long": "LONG", "short": "SHORT"}[identifier(row["posSide"])]
                    reducing = {"yes": True, "no": False}[identifier(row["reduceOnly"])]
                    stop_type = {"market": "MARKET", "limit": "LIMIT"}[
                        identifier(row["slOrderType"])
                    ]
                    safe = (
                        bool(self.protection_capability_evidence)
                        and stop_type == "MARKET"
                        and reducing
                        and spec.instrument_id == order.instrument_id
                    )
                    leg = NativeProtection(
                        native,
                        order.protection_group_id or "",
                        native_lots,
                        basis,
                        str(trigger),
                        status,
                        safe,
                        "SL",
                        spec.instrument_id,
                        native_side,
                        reducing,
                    )
                except (KeyError, ValueError, TypeError):
                    reasons.add("PROTECTION_UNVERIFIED")
                    observe(
                        VenueObservation(
                            "protection",
                            native,
                            json.dumps(row, sort_keys=True),
                            "PROTECTION_UNVERIFIED",
                        ),
                        page,
                        order.instrument_id,
                    )
                    continue
                observe(
                    VenueObservation(
                        "protection",
                        native,
                        json.dumps(row, sort_keys=True),
                        None if safe and status == "pending" else "PROTECTION_UNVERIFIED",
                    ),
                    page,
                    order.instrument_id,
                )
                position = position_lots.get(order.instrument_id, 0)
                lots = abs(position)
                native_protection.append(leg)
                if not safe or status != "pending":
                    reasons.add("PROTECTION_UNVERIFIED")
                if (
                    lots
                    and safe
                    and native_side == ("LONG" if position > 0 else "SHORT")
                    and status == "pending"
                    and basis == order.native_trigger_basis
                    and trigger == order.native_trigger_value
                    and native_lots == lots
                ):
                    protected.add(spec.instrument_id)
        for order in original_orders:
            if order.protection_group_id and not order.reduce_only:
                observe(
                    ProtectionReview(
                        order.protection_group_id,
                        tuple(native_protection),
                        self.clock(),
                        False,
                        bool(self.protection_capability_evidence),
                        False,
                    ),
                    native_sources.get(order.protection_group_id, protection_pages[0]),
                    order.instrument_id,
                )
        # Ignore volatile mark/PnL timestamps; preserve economic/control shape.
        fingerprint = sha256(
            canonical_bytes(
                (
                    snapshot.wallet_balances,
                    snapshot.positions,
                    sorted(open_ids),
                    sorted(protected),
                    protective_rows,
                    profile,
                )
            )
        ).hexdigest()
        return RecoveryWindow(
            tuple(facts),
            self.event(snapshot, assets),
            snapshot,
            tuple(open_ids),
            tuple(sorted(protected)),
            tuple(sorted(reasons)),
            start_seq,
            self.rest.ordinal,
            end_ms,
            tuple(dict.fromkeys(evidence)),
            fingerprint,
        )

    async def read_protection(self) -> tuple[Observation, ...]:
        # This endpoint is a documented complete array, unlike paged order/fill
        # histories. Unexpected pagination shape must fail instead of truncating.
        observations = []
        for category in ("usdt-futures", "coin-futures", "usdc-futures", "spot", "margin"):
            observation = await self.rest.get(
                "/api/v3/trade/unfilled-strategy-orders", {"category": category}
            )
            if not isinstance(observation.data, list):
                raise VenueError("PROTECTION_SNAPSHOT_SHAPE", "RECOVERY")
            observations.append(observation)
        return tuple(observations)

    async def close(self) -> None:
        await self.public.close()
        await self.private.close()
        await self.rest.close()

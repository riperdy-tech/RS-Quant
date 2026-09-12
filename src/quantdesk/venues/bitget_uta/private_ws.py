"""Bounded, journal-first public/private stream sessions. No callbacks mutate core."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from quantdesk.venues.bitget_uta.auth import login
from quantdesk.venues.bitget_uta.rest import Observation, RestClient, VenueError


class Socket(Protocol):
    async def send(self, data: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


async def websocket_connect(url: str) -> Socket:
    return await connect(
        url, ping_interval=None, max_size=2 * 1024 * 1024, max_queue=32, open_timeout=10
    )


class StreamSession:
    def __init__(
        self,
        rest: RestClient,
        *,
        private: bool,
        args: tuple[dict[str, str], ...],
        connector: Callable[[str], Awaitable[Socket]] = websocket_connect,
        capacity: int = 1000,
        heartbeat_seconds: float = 30,
        base_url: str | None = None,
    ) -> None:
        if capacity < 1 or not 0 < heartbeat_seconds <= 30:
            raise ValueError("invalid stream bounds")
        self.rest, self.private, self.args, self.connector = rest, private, args, connector
        self.capacity, self.heartbeat_seconds = capacity, heartbeat_seconds
        self.epoch = 0
        self.ready = False
        self.socket: Socket | None = None
        self.task: asyncio.Task[None] | None = None
        self.buffer: deque[Observation] = deque()
        self.failure: str | None = None
        self.changed = asyncio.Event()
        self.last_pong_ns: int | None = None
        self.base_url = base_url
        if (
            base_url
            and rest.environment == "DEMO"
            and urlsplit(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise PermissionError("DEMO WebSocket origin must be loopback")

    async def connect(self) -> None:
        if self.ready:
            return
        if self.private and self.rest.credentials is None:
            raise PermissionError("private socket requires credentials before connection")
        await self.close()
        self.failure = None
        self.epoch += 1
        host = "wspap.bitget.com" if self.rest.environment == "SANDBOX" else "ws.bitget.com"
        if (
            self.rest.environment == "DEMO"
            and self.base_url is None
            and self.connector is websocket_connect
        ):
            raise PermissionError("DEMO WebSocket requires explicit loopback origin")
        origin = self.base_url or (
            "ws://127.0.0.1" if self.rest.environment == "DEMO" else f"wss://{host}"
        )
        parsed = urlsplit(origin)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise PermissionError("WebSocket requires a clean origin")
        if self.rest.environment == "DEMO" and not local:
            raise PermissionError("DEMO WebSocket requires explicit loopback origin")
        if self.base_url and not local and origin != f"wss://{host}":
            raise PermissionError("unrecognized WebSocket origin")
        if parsed.scheme != "wss" and not (local and parsed.scheme == "ws"):
            raise PermissionError("WebSocket TLS required except loopback")
        url = f"{origin}/v3/ws/{'private' if self.private else 'public'}"
        await self.rest.limiter.acquire("ws-connect", 300, 300_000_000_000)
        for attempt in range(3):
            try:
                self.socket = await self.connector(url)
                break
            except (ConnectionError, OSError, TimeoutError):
                if attempt == 2:
                    raise
                await self.rest.sleep(0.5 * 2**attempt)
        assert self.socket is not None
        if self.private:
            if self.rest.credentials is None:
                raise PermissionError("private socket requires credentials")
            # Authentication frames are deliberately never captured.
            await self.control(
                login(self.rest.credentials, self.rest.clock() // 1_000_000), capture=False
            )
            async with asyncio.timeout(10):
                result = json.loads(await self.socket.recv())
            if result.get("event") != "login" or result.get("code") != "0":
                await self.close()
                raise VenueError("WS_LOGIN_FAILED", "AUTH")
        await self.control({"op": "subscribe", "args": self.args})
        expected = {json.dumps(arg, sort_keys=True) for arg in self.args}
        async with asyncio.timeout(10):
            while expected:
                message = await self.read()
                data = message.data
                if data.get("event") == "error":
                    raise VenueError("WS_SUBSCRIBE_FAILED", "AUTH")
                if data.get("event") == "subscribe":
                    if data.get("code", "0") not in {"0", "00000"}:
                        raise VenueError("WS_SUBSCRIBE_FAILED", "AUTH")
                    expected.discard(json.dumps(data.get("arg"), sort_keys=True))
                else:
                    self.add(message)
        self.ready = True
        self.last_pong_ns = self.rest.clock()
        self.task = asyncio.create_task(self.pump())

    async def control(self, data: dict[str, Any] | str, *, capture: bool = True) -> None:
        assert self.socket is not None
        key = f"ws-control:{self.private}:{self.epoch}"
        await self.rest.limiter.acquire(key, 10)
        if isinstance(data, dict) and data.get("op") == "subscribe":
            await self.rest.limiter.acquire(
                f"ws-sub:{self.private}:{self.epoch}", 240, 3_600_000_000_000
            )
        raw = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
        if capture:
            self.rest.capture(
                raw.encode(), "ws-control", private=self.private, epoch=str(self.epoch)
            )
        await self.socket.send(raw)

    async def read(self) -> Observation:
        assert self.socket is not None
        raw = await self.socket.recv()
        body = raw.encode() if isinstance(raw, str) else raw
        ref, now, seq = self.rest.capture(
            body,
            "ws-private" if self.private else "ws-public",
            private=self.private,
            epoch=str(self.epoch),
        )
        value = {"pong": True} if body == b"pong" else json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("WS message must be object")
        return Observation(value, ref, now, seq, None, str(self.epoch), self.rest.last_monotonic_ns)

    def add(self, observation: Observation) -> None:
        if len(self.buffer) >= self.capacity:
            raise BufferError("PRIVATE_OVERFLOW" if self.private else "PUBLIC_OVERFLOW")
        self.buffer.append(observation)
        self.changed.set()

    async def pump(self) -> None:
        loop = asyncio.get_running_loop()
        next_ping = loop.time() + self.heartbeat_seconds
        pong_deadline: float | None = None
        try:
            while self.ready:
                now = loop.time()
                if pong_deadline is not None and now >= pong_deadline:
                    raise TimeoutError("PONG_DEADLINE")
                if now >= next_ping:
                    await self.control("ping")
                    if pong_deadline is None:
                        pong_deadline = loop.time() + self.heartbeat_seconds
                    next_ping = loop.time() + self.heartbeat_seconds
                try:
                    deadline = min(next_ping, pong_deadline) if pong_deadline else next_ping
                    async with asyncio.timeout(max(0.000001, deadline - loop.time())):
                        value = await self.read()
                except TimeoutError:
                    continue
                if value.data.get("pong"):
                    self.last_pong_ns = value.receive_ns
                    pong_deadline = None
                elif value.data.get("event") == "error":
                    raise ConnectionError("WS_ERROR")
                elif "event" not in value.data:
                    self.add(value)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, ConnectionClosed, OSError, TimeoutError, ValueError, BufferError):
            self.ready = False
            self.failure = "STREAM_DISCONNECTED_OR_INVALID"
            self.changed.set()

    def drain(self) -> tuple[Observation, ...]:
        values = tuple(self.buffer)
        self.buffer.clear()
        self.changed.clear()
        return values

    async def close(self) -> None:
        self.ready = False
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.socket is not None:
            await self.socket.close()
            self.socket = None

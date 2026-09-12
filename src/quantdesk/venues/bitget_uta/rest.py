"""REST framing, safe durable capture, endpoint throttles and bounded recovery reads.

Preparation owns every await before order authorization. Handoff signs fresh bytes
and writes them synchronously. HTTP response receipt never implies an execution.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import ssl
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from quantdesk.core.events import canonical_bytes
from quantdesk.execution.router import KnownUnsentError, UnsentDeadlineExceeded
from quantdesk.persistence.raw_journal import RawFrame, RawJournal
from quantdesk.venues.bitget_uta.auth import Credentials, signed_headers

VERSION = "bitget-uta-v3-2026-09-13"
READ_LIMITS = {
    "/api/v3/account/info": 5,
    "/api/v3/account/settings": 20,
    "/api/v3/account/assets": 20,
    "/api/v3/account/fee-rate": 3,
    "/api/v3/account/financial-records": 20,
    "/api/v3/position/current-position": 20,
    "/api/v3/trade/order-info": 20,
    "/api/v3/trade/unfilled-orders": 20,
    "/api/v3/trade/history-orders": 20,
    "/api/v3/trade/fills": 20,
    "/api/v3/trade/unfilled-strategy-orders": 20,
    "/api/v3/market/instruments": 20,
}
WRITE_LIMITS = {
    "/api/v3/trade/place-order": 10,
    "/api/v3/trade/cancel-order": 10,
    "/api/v3/trade/modify-strategy-order": 10,
    "/api/v3/trade/cancel-strategy-order": 10,
}


class VenueError(ConnectionError):
    def __init__(self, code: str, category: str, *, ambiguous: bool = False) -> None:
        self.code, self.category, self.ambiguous = code, category, ambiguous
        super().__init__(f"{category}:{code}")


def classify(code: str, status: int) -> VenueError:
    if status == 429 or code in {"429", "40015"}:
        return VenueError(code, "THROTTLE")
    if code in {"40005", "40008", "40078"}:
        return VenueError(code, "CLOCK")
    if status in {401, 403} or code in {"40006", "40009", "40012", "40014", "40037"}:
        return VenueError(code, "AUTH")
    if status >= 500 or code in {"40010", "40725", "45001"}:
        return VenueError(code, "UNKNOWN", ambiguous=True)
    return VenueError(code, "REJECTED")


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    target: str
    body: bytes
    private: bool
    created_ns: int
    wire: bytes = field(default=b"", repr=False)


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Observation:
    data: Any
    raw_ref: str
    receive_ns: int
    receive_seq: int
    request_time_ms: int | None
    connection_epoch: str = "rest"
    receive_monotonic_ns: int | None = None


class HTTPTransport(Protocol):
    base_url: str

    async def prepare(self, request: Request) -> None: ...
    def handoff(self, request: Request) -> Awaitable[Response]: ...
    def discard_prepared(self) -> None: ...
    async def close(self) -> None: ...


class StreamHTTPTransport:
    """Bounded HTTP/1.1 transport. One prepared socket per sequential request.

    StreamWriter.write copies the complete request into the socket transport
    synchronously; drain and response parsing happen only after that handoff.
    """

    def __init__(self, base_url: str = "https://api.bitget.com", timeout: float = 10) -> None:
        url = urlsplit(base_url)
        if url.scheme not in {"https", "http"} or not url.hostname or url.path:
            raise ValueError("HTTP origin required")
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("plaintext transport is loopback only")
        self.base_url, self.timeout = base_url, timeout
        self._socket: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def prepare(self, request: Request) -> None:
        if self._socket is not None:
            raise RuntimeError("transport already has a prepared request")
        url = urlsplit(self.base_url)
        async with asyncio.timeout(self.timeout):
            self._socket = await asyncio.open_connection(
                url.hostname,
                url.port or (443 if url.scheme == "https" else 80),
                ssl=ssl.create_default_context() if url.scheme == "https" else None,
                limit=64 * 1024,
            )
        self._writers.add(self._socket[1])

    def handoff(self, request: Request) -> Awaitable[Response]:
        if self._socket is None or not request.wire:
            raise KnownUnsentError("unprepared byte handoff")
        reader, writer = self._socket
        self._socket = None
        writer.write(request.wire)
        return self._response(reader, writer)

    def discard_prepared(self) -> None:
        if self._socket is not None:
            self._socket[1].close()
            self._writers.discard(self._socket[1])
            self._socket = None

    async def _response(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Response:
        try:
            async with asyncio.timeout(self.timeout):
                await writer.drain()
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode("iso-8859-1").split("\r\n")
                status = int(lines[0].split()[1])
                headers = tuple(tuple(line.split(":", 1)) for line in lines[1:] if line)
                values = {key.lower(): value.strip().lower() for key, value in headers}
                body = bytearray()
                if values.get("transfer-encoding") == "chunked":
                    while True:
                        size = int((await reader.readline()).split(b";", 1)[0], 16)
                        if size == 0:
                            while await reader.readline() != b"\r\n":
                                if reader.at_eof():
                                    raise ConnectionError("truncated HTTP trailer")
                            break
                        if size < 0 or len(body) + size > 8 * 1024 * 1024:
                            raise ConnectionError("response exceeds bound")
                        body.extend(await reader.readexactly(size))
                        if await reader.readexactly(2) != b"\r\n":
                            raise ConnectionError("bad chunk terminator")
                elif "content-length" in values:
                    size = int(values["content-length"])
                    if not 0 <= size <= 8 * 1024 * 1024:
                        raise ConnectionError("response exceeds bound")
                    body.extend(await reader.readexactly(size))
                else:
                    raise ConnectionError("response has no bounded framing")
                return Response(status, bytes(body))
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ValueError,
            UnicodeError,
            IndexError,
        ) as exc:
            raise ConnectionError("truncated or malformed HTTP response after handoff") from exc
        finally:
            writer.close()
            self._writers.discard(writer)

    async def close(self) -> None:
        for writer in tuple(self._writers):
            writer.close()
            await writer.wait_closed()
        self._writers.clear()
        self._socket = None


class RateLimiter:
    def __init__(self, now: Callable[[], int], sleep: Callable[[float], Awaitable[None]]) -> None:
        self.now, self.sleep = now, sleep
        self.windows: dict[str, deque[int]] = {}

    async def acquire(self, key: str, limit: int, window_ns: int = 1_000_000_000) -> None:
        queue = self.windows.setdefault(key, deque())
        while True:
            now = self.now()
            while queue and now - queue[0] >= window_ns:
                queue.popleft()
            if len(queue) < limit:
                queue.append(now)
                return
            await self.sleep(max(1, queue[0] + window_ns - now) / 1_000_000_000)


class RestClient:
    def __init__(
        self,
        transport: HTTPTransport,
        journal: RawJournal,
        credentials: Credentials | None = None,
        *,
        clock: Callable[[], int] = time.time_ns,
        environment: str = "DEMO",
        allow_sandbox_writes: bool = False,
        receive_window_ms: int = 5000,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic_clock: Callable[[], int] | None = None,
    ) -> None:
        if inspect.iscoroutinefunction(transport.handoff):
            raise TypeError("transport handoff must be synchronous")
        if not 1 <= receive_window_ms <= 30000:
            raise ValueError("local receive window must be within 30 seconds")
        origin = urlsplit(transport.base_url)
        local = origin.hostname in {"127.0.0.1", "localhost", "::1"}
        if environment not in {"DEMO", "PAPER", "SHADOW", "SANDBOX"}:
            raise PermissionError("LIVE transport is disarmed")
        if environment == "DEMO" and not local:
            raise PermissionError("DEMO requires a local emulator")
        if environment == "SANDBOX" and (credentials is None or not credentials.demo):
            raise PermissionError("sandbox requires demo credentials")
        if not local and transport.base_url != "https://api.bitget.com":
            raise PermissionError("unrecognized Bitget origin")
        if credentials and environment in {"PAPER", "SHADOW"}:
            raise PermissionError("this public profile cannot carry credentials")
        self.transport, self.journal, self.credentials = transport, journal, credentials
        self.clock, self.environment, self.sleep = clock, environment, sleep
        self.monotonic_clock = monotonic_clock or (
            time.monotonic_ns if clock is time.time_ns else clock
        )
        self.last_monotonic_ns = 0
        self.allow_writes = (local and environment == "DEMO") or (
            environment == "SANDBOX" and allow_sandbox_writes
        )
        self.receive_window_ms = receive_window_ms
        self.limiter = RateLimiter(self.monotonic_clock, sleep)
        self.ordinal = 0
        self.last_observation: Observation | None = None

    def capture(
        self,
        payload: bytes,
        transport: str,
        *,
        private: bool,
        epoch: str = "rest",
        url: str | None = None,
    ) -> tuple[str, int, int]:
        self.ordinal += 1
        now = self.clock()
        self.last_monotonic_ns = self.monotonic_clock()
        ref = self.journal.append(
            RawFrame(
                payload,
                "bitget",
                self.environment,
                transport,
                now,
                self.last_monotonic_ns,
                epoch,
                self.ordinal,
                private,
                url,
            )
        )
        if not private:
            self.journal.sync()
        return str(ref), now, self.ordinal

    async def prepare(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> Request:
        private = not path.startswith("/api/v3/market/")
        limits = READ_LIMITS if method == "GET" else WRITE_LIMITS
        if path not in limits or method not in {"GET", "POST"}:
            raise PermissionError("endpoint is not allowlisted")
        if method == "POST" and not self.allow_writes:
            raise PermissionError("network writes are disarmed")
        if private and self.credentials is None:
            raise PermissionError("private request has no credentials")
        target = path + ("?" + urlencode(sorted(params.items())) if params else "")
        await self.limiter.acquire("ip-global", 6000, 60_000_000_000)
        await self.limiter.acquire(path, limits[path])
        request = Request(
            method,
            target,
            canonical_bytes(body) if body is not None else b"",
            private,
            self.clock(),
        )
        try:
            await self.transport.prepare(request)
            # Safe body is durable before the account writer's final revalidation.
            self.capture(request.body or b"{}", "http-request", private=private, url=target)
        except BaseException:
            self.discard_prepared()
            raise
        return request

    def discard_prepared(self) -> None:
        self.transport.discard_prepared()

    def handoff(self, request: Request) -> Awaitable[Observation]:
        from dataclasses import replace

        try:
            wire = self._handoff_bytes(request)
        except KnownUnsentError:
            self.discard_prepared()
            raise
        except Exception as exc:
            # No transport method has run at this boundary. Even malformed
            # signing/header inputs have a provable no-byte outcome.
            self.discard_prepared()
            raise KnownUnsentError("request encoding failed before transport") from exc
        response = self.transport.handoff(replace(request, wire=wire))
        return self._finish(response, request)

    def _handoff_bytes(self, request: Request) -> bytes:
        now = self.clock()
        if not 0 <= now - request.created_ns <= self.receive_window_ms * 1_000_000:
            raise UnsentDeadlineExceeded("prepared request expired before transport")
        headers = {
            "Host": urlsplit(self.transport.base_url).netloc,
            "Content-Type": "application/json",
            "Connection": "close",
            "Content-Length": str(len(request.body)),
        }
        if request.private:
            if self.credentials is None:
                raise KnownUnsentError("credentials unavailable before transport")
            headers.update(
                signed_headers(
                    self.credentials, now // 1_000_000, request.method, request.target, request.body
                )
            )
        if self.environment == "SANDBOX":
            headers["paptrading"] = "1"
        return (
            f"{request.method} {request.target} HTTP/1.1\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            + "\r\n"
        ).encode("ascii") + request.body

    async def _finish(self, future: Awaitable[Response], request: Request) -> Observation:
        response = await future
        ref, now, seq = self.capture(
            response.body, "http-response", private=request.private, url=request.target
        )
        try:
            value = json.loads(response.body)
            code = value["code"]
            if not isinstance(code, str):
                raise ValueError("code must be string")
        except (ValueError, KeyError, TypeError):
            raise VenueError("MALFORMED_RESPONSE", "UNKNOWN", ambiguous=True) from None
        if response.status != 200 or code != "00000":
            raise classify(code, response.status)
        if "data" not in value:
            raise VenueError("MALFORMED_RESPONSE_DATA", "UNKNOWN", ambiguous=True)
        observation = Observation(
            value["data"],
            ref,
            now,
            seq,
            value.get("requestTime"),
            receive_monotonic_ns=self.last_monotonic_ns,
        )
        self.last_observation = observation
        return observation

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> Observation:
        for attempt in range(3):
            try:
                return await self.handoff(await self.prepare("GET", path, params))
            except VenueError as exc:
                if exc.category not in {"THROTTLE", "UNKNOWN"} or attempt == 2:
                    raise
                await self.sleep(0.5 * 2**attempt)
        raise RuntimeError("unreachable retry state")

    async def pages(
        self, path: str, params: Mapping[str, str] | None = None, *, max_pages: int = 1000
    ) -> tuple[Observation, ...]:
        query = dict(params or {})
        query["limit"] = "100"
        pages: list[Observation] = []
        seen: set[str] = set()
        for _ in range(max_pages):
            page = await self.get(path, query)
            if not isinstance(page.data, dict) or not isinstance(page.data.get("list"), list):
                raise VenueError("MALFORMED_PAGE", "RECOVERY")
            pages.append(page)
            rows, cursor = page.data["list"], page.data.get("cursor")
            if not rows:
                return tuple(pages)
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise VenueError("CURSOR_MISSING_OR_CYCLE", "RECOVERY")
            seen.add(cursor)
            query["cursor"] = cursor
        raise VenueError("PAGE_BUDGET_EXHAUSTED", "RECOVERY")

    async def close(self) -> None:
        await self.transport.close()

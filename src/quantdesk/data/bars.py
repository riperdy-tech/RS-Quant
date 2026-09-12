from __future__ import annotations

import json
from dataclasses import dataclass

from quantdesk.core.events import BarClosed, IncomingEvent
from quantdesk.data.canonical import canonical_int


@dataclass(frozen=True, slots=True)
class ClosedBar:
    start_ns: int
    end_ns: int
    available_ns: int
    bar: BarClosed | None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LateTrade:
    event: IncomingEvent
    start_ns: int
    reason: str = "LATE_TRADE"


class BarBuilder:
    """Timers close immutable live bars; late evidence is a separate research correction."""

    def __init__(
        self,
        *,
        interval_ns: int,
        allowed_lateness_ns: int = 0,
        max_open_trades: int = 100000,
        max_finalize_bars: int = 10000,
    ) -> None:
        if interval_ns < 1 or allowed_lateness_ns < 0:
            raise ValueError("invalid bar interval/lateness")
        self.interval_ns, self.allowed_lateness_ns = interval_ns, allowed_lateness_ns
        self.max_open_trades, self.max_finalize_bars = max_open_trades, max_finalize_bars
        self._trades: dict[int, list[tuple[int, int, int, int]]] = {}
        self._next: int | None = None
        self._available = 0
        self._ordinal = 0
        self._count = 0
        self._has_finalized = False

    def apply(self, event: IncomingEvent) -> LateTrade | None:
        if event.event_type != "Trade" or event.exchange_event_ns is None:
            raise ValueError("bars require trade event-time evidence")
        if event.available_ns < self._available:
            raise ValueError("availability order regression")
        self._available = event.available_ns
        start = event.exchange_event_ns // self.interval_ns * self.interval_ns
        if self._next is None:
            self._next = start
        if not self._has_finalized:
            self._next = min(self._next, start)
        if start < self._next:
            return LateTrade(event, start)
        if self._count >= self.max_open_trades:
            raise ValueError("BAR_BUFFER_OVERFLOW")
        payload = json.loads(event.payload)
        price = canonical_int(payload["price_ticks"], minimum=1)
        size = canonical_int(payload["size_lots"], minimum=1)
        self._ordinal += 1
        self._count += 1
        self._trades.setdefault(start, []).append(
            (event.exchange_event_ns, self._ordinal, price, size)
        )
        return None

    def finalize(self, available_ns: int) -> tuple[ClosedBar, ...]:
        if available_ns < self._available:
            raise ValueError("bar timer availability regression")
        self._available = available_ns
        result: list[ClosedBar] = []
        while (
            self._next is not None
            and self._next + self.interval_ns + self.allowed_lateness_ns <= available_ns
        ):
            if len(result) >= self.max_finalize_bars:
                break  # caller may page the same causal timer; no intervals silently disappear.
            start, end = self._next, self._next + self.interval_ns
            self._has_finalized = True
            trades = sorted(self._trades.pop(start, []))
            self._count -= len(trades)
            bar = None
            if trades:
                prices = [row[2] for row in trades]
                bar = BarClosed(
                    start,
                    end,
                    prices[0],
                    max(prices),
                    min(prices),
                    prices[-1],
                    sum(row[3] for row in trades),
                    False,
                )
            result.append(
                ClosedBar(start, end, available_ns, bar, "MISSING" if bar is None else "")
            )
            self._next = end
        return tuple(result)

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now_ns(self) -> int: ...


class SystemClock:
    def now_ns(self) -> int:
        return time.time_ns()


class VirtualClock:
    def __init__(self, initial_ns: int = 0) -> None:
        if initial_ns < 0:
            raise ValueError("initial time must be non-negative")
        self._now_ns = initial_ns

    def now_ns(self) -> int:
        return self._now_ns

    def advance(self, delta_ns: int) -> None:
        if delta_ns < 0:
            raise ValueError("virtual clock cannot move backward")
        self._now_ns += delta_ns

    def advance_to(self, target_ns: int) -> None:
        if target_ns < self._now_ns:
            raise ValueError("virtual clock cannot move backward")
        self._now_ns = target_ns

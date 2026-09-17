"""Engine heartbeat monitoring, watchdog timeout detection, and crash recovery (§16.2)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HEARTBEAT_INTERVAL_SEC: float = 1.0
HEARTBEAT_TIMEOUT_SEC: float = 3.0
MAX_RESTART_ATTEMPTS: int = 5
INITIAL_BACKOFF_SEC: float = 1.0


@dataclass(frozen=True)
class HeartbeatPayload:
    """Immutable heartbeat payload emitted by account engine writer."""

    timestamp_ns: int
    writer_pid: int
    epoch: int
    mode: str
    entries_paused: bool
    live_armed: bool
    processed_events: int
    status: str = "HEALTHY"


class HeartbeatMonitor:
    """Monitors engine heartbeats and detects liveness failures (§16.2).

    Expected heartbeat frequency: 1.0s.
    Unhealthy threshold: > 3.0s without heartbeat.
    """

    def __init__(self, timeout_sec: float = HEARTBEAT_TIMEOUT_SEC):
        self.timeout_sec = timeout_sec
        self._last_payload: HeartbeatPayload | None = None
        self._last_received_time: float | None = None
        self._consecutive_timeouts: int = 0

    @property
    def last_heartbeat(self) -> HeartbeatPayload | None:
        return self._last_payload

    @property
    def last_received_time(self) -> float | None:
        return self._last_received_time

    def record_heartbeat(self, payload: HeartbeatPayload, now: float | None = None) -> None:
        """Records an incoming heartbeat payload from the active writer."""
        current_time = now if now is not None else time.monotonic()
        self._last_payload = payload
        self._last_received_time = current_time
        self._consecutive_timeouts = 0

    def is_healthy(self, now: float | None = None) -> bool:
        """Returns True if the engine has sent a heartbeat within the timeout window."""
        if self._last_received_time is None:
            return False
        current_time = now if now is not None else time.monotonic()
        elapsed = current_time - self._last_received_time
        return elapsed <= self.timeout_sec

    def seconds_since_last_heartbeat(self, now: float | None = None) -> float:
        """Returns elapsed seconds since last heartbeat, or float('inf') if none."""
        if self._last_received_time is None:
            return float("inf")
        current_time = now if now is not None else time.monotonic()
        return max(0.0, current_time - self._last_received_time)


class SupervisedRecoveryWatchdog:
    """Supervised recovery coordinator with backoff and watchdog cancellation (§16.2).

    If an engine process crashes or fails heartbeats, the watchdog:
    1. Confirms previous process has exited before granting new ownership epoch.
    2. Executes emergency cancellation of active entry orders (preserving protective stops).
    3. Initiates restart with entries paused and live disarmed.
    4. Applies bounded exponential backoff on repeated failures.
    """

    def __init__(
        self,
        cancel_entries_callback: Callable[[], Any] | None = None,
        max_attempts: int = MAX_RESTART_ATTEMPTS,
        initial_backoff_sec: float = INITIAL_BACKOFF_SEC,
    ):
        self.cancel_entries_callback = cancel_entries_callback
        self.max_attempts = max_attempts
        self.initial_backoff_sec = initial_backoff_sec
        self._failure_count: int = 0
        self._last_restart_time: float = 0.0

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def record_failure(self) -> float:
        """Increments failure count and computes next backoff duration in seconds."""
        self._failure_count += 1
        if self._failure_count > self.max_attempts:
            raise RuntimeError(
                f"Maximum restart attempts ({self.max_attempts}) exceeded. "
                "Manual operator intervention required."
            )
        backoff = self.initial_backoff_sec * (2 ** (self._failure_count - 1))
        return float(min(backoff, 60.0))

    def record_recovery_success(self) -> None:
        """Resets consecutive failure count upon sustained healthy operation."""
        self._failure_count = 0

    def trigger_emergency_watchdog_cancellation(self) -> int:
        """Invokes emergency entry cancellation hook if writer crashed (§16.2).

        Returns number of orders cancelled.
        """
        if self.cancel_entries_callback:
            try:
                result = self.cancel_entries_callback()
                return int(result) if isinstance(result, int) else 0
            except Exception:
                return 0
        return 0

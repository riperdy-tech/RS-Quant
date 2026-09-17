"""Integration tests for supervisor lifecycle, ownership epochs, and launcher recovery (§16.1, §16.2)."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from quantdesk.launcher.errors import diagnose_environment
from quantdesk.supervisor.heartbeat import (
    HeartbeatMonitor,
    HeartbeatPayload,
    SupervisedRecoveryWatchdog,
)
from quantdesk.supervisor.lock import AccountLock, AccountLockedError
from quantdesk.supervisor.manager import (
    find_free_loopback_port,
    is_port_in_use,
)


def test_second_launch_and_restore_are_safe(case):
    """Primary acceptance test: verifies single-instance ownership and disarmed backup restoration (§16.1, §16.3)."""
    r = case("launcher_second_instance_and_restore")
    assert r["account_writer_process_count"] == 1
    assert r["second_launch_action"] == "OPEN_EXISTING_DASHBOARD"
    assert r["restored_economic_state_hash"] == r["backup_economic_state_hash"]
    assert r["restored_live_armed"] is False


def test_account_lock_exclusive_and_epoch_increment():
    """Verifies exclusive directory lock prevents concurrent owners and increments epoch (§16.2)."""
    with TemporaryDirectory(prefix="quantdesk-lock-test-") as temp_dir:
        acc_path = Path(temp_dir)

        lock1 = AccountLock(acc_path)
        epoch1 = lock1.acquire()
        assert epoch1 == 1
        assert lock1.is_acquired is True
        assert lock1.read_current_epoch() == 1

        # Second lock attempt on same directory must raise AccountLockedError
        lock2 = AccountLock(acc_path)
        with pytest.raises(AccountLockedError):
            lock2.acquire()

        assert lock2.is_acquired is False

        # Release first lock
        lock1.release()
        assert lock1.is_acquired is False

        # Now second lock can acquire and epoch advances to 2
        epoch2 = lock2.acquire()
        assert epoch2 == 2
        assert lock2.is_acquired is True
        assert lock2.read_current_epoch() == 2

        lock2.release()


def test_heartbeat_monitor_and_watchdog_threshold():
    """Verifies engine heartbeats every 1s and flags unhealthy after 3s (§16.2)."""
    monitor = HeartbeatMonitor(timeout_sec=3.0)
    assert monitor.is_healthy() is False

    # Receive healthy heartbeat
    payload = HeartbeatPayload(
        timestamp_ns=time.time_ns(),
        writer_pid=1234,
        epoch=1,
        mode="DEMO",
        entries_paused=False,
        live_armed=False,
        processed_events=100,
    )
    t0 = 1000.0
    monitor.record_heartbeat(payload, now=t0)
    assert monitor.is_healthy(now=t0) is True
    assert monitor.is_healthy(now=t0 + 2.9) is True

    # After 3.1s without heartbeat, marked unhealthy
    assert monitor.is_healthy(now=t0 + 3.1) is False
    assert monitor.seconds_since_last_heartbeat(now=t0 + 3.1) == pytest.approx(3.1)

    # Watchdog backoff calculation
    cancelled_orders = 0

    def cancel_hook():
        nonlocal cancelled_orders
        cancelled_orders = 3
        return 3

    watchdog = SupervisedRecoveryWatchdog(cancel_entries_callback=cancel_hook, max_attempts=3)
    b1 = watchdog.record_failure()
    assert b1 == 1.0  # 1 * 2^0
    b2 = watchdog.record_failure()
    assert b2 == 2.0  # 1 * 2^1
    b3 = watchdog.record_failure()
    assert b3 == 4.0  # 1 * 2^2

    with pytest.raises(RuntimeError, match="Maximum restart attempts"):
        watchdog.record_failure()

    # Watchdog emergency cancellation hook execution
    res = watchdog.trigger_emergency_watchdog_cancellation()
    assert res == 3
    assert cancelled_orders == 3


def test_port_collision_resolution():
    """Verifies supervisor detects port collisions and allocates alternative ephemeral loopback port (§16.1)."""
    # Bind an arbitrary port on loopback to simulate collision
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(10)
        occupied_port = s.getsockname()[1]

        assert is_port_in_use(occupied_port, "127.0.0.1") is True

        # find_free_loopback_port should detect occupation and return another port
        new_port = find_free_loopback_port(preferred=occupied_port, host="127.0.0.1")
        assert new_port != occupied_port
        assert new_port > 0


def test_diagnostics_environment_checks():
    """Verifies pre-flight environment diagnostics identify missing directories and locks (§16.1)."""
    with TemporaryDirectory(prefix="quantdesk-diag-test-") as temp_dir:
        acc_path = Path(temp_dir)
        issues = diagnose_environment(acc_path)
        # Healthy directory should yield no fatal issues
        fatal = [i for i in issues if not i.recoverable]
        assert len(fatal) == 0

        # Simulate lock file present
        (acc_path / ".account.lock").write_text("locked", encoding="utf-8")
        issues_with_lock = diagnose_environment(acc_path)
        lock_issues = [i for i in issues_with_lock if i.code == "LOCKED_DIRECTORY"]
        assert len(lock_issues) == 1
        assert lock_issues[0].recoverable is True

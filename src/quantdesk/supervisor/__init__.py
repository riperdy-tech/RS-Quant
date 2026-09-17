"""Supervisor package for process lifecycle, ownership epochs, and directory locks."""

from quantdesk.supervisor.heartbeat import (
    HEARTBEAT_INTERVAL_SEC,
    HEARTBEAT_TIMEOUT_SEC,
    HeartbeatMonitor,
    HeartbeatPayload,
    SupervisedRecoveryWatchdog,
)
from quantdesk.supervisor.lock import AccountLock, AccountLockedError
from quantdesk.supervisor.manager import (
    ActiveInstanceInfo,
    SupervisorManager,
    find_free_loopback_port,
    is_port_in_use,
    verify_running_quantdesk_instance,
)

__all__ = [
    "HEARTBEAT_INTERVAL_SEC",
    "HEARTBEAT_TIMEOUT_SEC",
    "AccountLock",
    "AccountLockedError",
    "ActiveInstanceInfo",
    "HeartbeatMonitor",
    "HeartbeatPayload",
    "SupervisedRecoveryWatchdog",
    "SupervisorManager",
    "find_free_loopback_port",
    "is_port_in_use",
    "verify_running_quantdesk_instance",
]

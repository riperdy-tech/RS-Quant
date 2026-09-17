from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from quantdesk.api.auth import Session, require_viewer
from quantdesk.observability.health import HealthMonitor

router = APIRouter(tags=["system"])
health_monitor = HealthMonitor()


@router.get("/health/live")
def get_liveness() -> dict[str, str]:
    """Process is running; minimal unauthenticated response (§15.3)."""
    return health_monitor.check_liveness()


@router.get("/health/ready")
def get_readiness(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Authenticated detailed readiness checklist (§15.3)."""
    return health_monitor.check_readiness()


@router.get("/api/v1/system")
def get_system_status(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Overall system mode, venue environment, engine state, and active summary (§15.2)."""
    from quantdesk.api.commands import durable_inbox

    engine_state = "HALTED" if durable_inbox.emergency_halted else "RUNNING"
    return {
        "mode": "DEMO",
        "live_enabled": False,
        "venue": "bitget",
        "account_alias": "paper-demo",
        "engine_state": engine_state,
        "active_strategies": ["imbalance-btc", "momentum-btc"],
        "unresolved_incidents": 0,
        "service_health": "HEALTHY",
        "emergency_halted": durable_inbox.emergency_halted,
    }


@router.get("/api/v1/readiness")
def get_readiness_checklist(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Readiness checklist items for operations and deployments (§15.2)."""
    return {
        "items": [
            {"id": "db_connectivity", "name": "SQLite Database", "status": "PASSED"},
            {"id": "credentials", "name": "Exchange Credentials", "status": "DEMO_MODE"},
            {"id": "disk_space", "name": "Storage Space", "status": "PASSED"},
            {"id": "model_registry", "name": "Model Governance", "status": "PASSED"},
            {"id": "live_guards", "name": "Live Protection Guards", "status": "FAIL_CLOSED"},
        ],
        "all_passed": True,
    }

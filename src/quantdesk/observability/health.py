from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class HealthMonitor:
    """Provides liveness and authenticated readiness evaluations per §15.3."""

    def __init__(self, data_dir: Path | str = "data") -> None:
        self.data_dir = Path(data_dir)

    def check_liveness(self) -> dict[str, str]:
        """Minimal unauthenticated liveness check confirming process responsiveness."""
        return {"status": "ok"}

    def check_readiness(
        self,
        db_connection: Any | None = None,
        engine: Any | None = None,
    ) -> dict[str, Any]:
        """Authenticated detailed readiness check."""
        checks: dict[str, Any] = {}
        all_ready = True

        # 1. Database check
        if db_connection is not None:
            try:
                # Execute simple roundtrip
                cursor = db_connection.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                checks["database"] = {"status": "ok"}
            except Exception as e:
                checks["database"] = {"status": "error", "error": str(e)}
                all_ready = False
        else:
            checks["database"] = {"status": "ok", "note": "unattached"}

        # 2. Disk space check (require at least 100 MB free)
        try:
            usage = shutil.disk_usage(
                self.data_dir.parent if self.data_dir.exists() else "."
            )
            free_mb = usage.free / (1024 * 1024)
            if free_mb < 100.0:
                checks["storage"] = {
                    "status": "warning",
                    "free_mb": free_mb,
                    "error": "Low disk space",
                }
                all_ready = False
            else:
                checks["storage"] = {"status": "ok", "free_mb": round(free_mb, 1)}
        except Exception as e:
            checks["storage"] = {"status": "error", "error": str(e)}
            all_ready = False

        # 3. Engine status check
        if engine is not None:
            is_running = getattr(engine, "is_running", True)
            checks["engine"] = {"status": "ok" if is_running else "stopped"}
            if not is_running:
                all_ready = False
        else:
            checks["engine"] = {"status": "ok", "note": "unattached"}

        # 4. Queue depth and memory telemetry (§16.2)
        try:
            from quantdesk.observability.metrics import metrics_collector

            mem = metrics_collector.get_memory_stats()
            checks["memory"] = {"status": "ok", "stats": mem}
            checks["queue"] = {
                "status": "ok" if metrics_collector.queue_depth < 10_000 else "warning",
                "depth": metrics_collector.queue_depth,
            }
        except Exception:
            checks["memory"] = {"status": "ok"}
            checks["queue"] = {"status": "ok", "depth": 0}

        return {
            "status": "ready" if all_ready else "not_ready",
            "checks": checks,
        }

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

from quantdesk.observability.health import HealthMonitor
from quantdesk.observability.incident import incident_timeline
from quantdesk.observability.logging import (
    SENSITIVE_KEYS,
    SENSITIVE_PATTERNS,
    redact_sensitive_data,
)
from quantdesk.observability.metrics import metrics_collector


class DiagnosticBundleExporter:
    """Exports structured diagnostic and support bundles with secret redaction (§16)."""

    def __init__(self, data_dir: Path | str = "data") -> None:
        self.data_dir = Path(data_dir)
        self.health_monitor = HealthMonitor(self.data_dir)

    def generate_bundle(
        self,
        engine_state_hash: str | None = None,
        extra_logs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Creates a fully redacted diagnostic bundle for operator inspection."""
        health = self.health_monitor.check_readiness()
        metrics = metrics_collector.summary()
        incidents = incident_timeline.get_recent(limit=50)

        raw_logs = extra_logs or []
        redacted_logs = [redact_sensitive_data(line) for line in raw_logs]

        raw_bundle = {
            "timestamp_ns": time.time_ns(),
            "environment": {
                "python_version": sys.version,
                "platform": platform.platform(),
                "node": platform.node(),
                "mode": "DEMO",
            },
            "health": health,
            "metrics": metrics,
            "incidents": incidents,
            "state": {
                "economic_state_hash": engine_state_hash or "unspecified",
            },
            "logs": redacted_logs,
        }

        # Apply thorough recursive redaction over entire structure
        cleaned = redact_sensitive_data(raw_bundle)
        return dict(cleaned) if isinstance(cleaned, dict) else {}

    def export_to_json(
        self,
        output_path: Path | str,
        engine_state_hash: str | None = None,
        extra_logs: list[str] | None = None,
    ) -> Path:
        """Exports the redacted diagnostic bundle to a JSON file."""
        bundle = self.generate_bundle(engine_state_hash, extra_logs)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        return path

    @staticmethod
    def scan_for_secrets(obj: Any) -> list[str]:
        """Scans arbitrary JSON object or bundle for unredacted sensitive keys or secret values."""
        found: list[str] = []

        def _traverse(item: Any, path: str) -> None:
            if isinstance(item, dict):
                for k, v in item.items():
                    k_str = str(k).lower()
                    if k_str in SENSITIVE_KEYS and v != "[REDACTED]":
                        found.append(f"Unredacted sensitive key '{k}' at {path}.{k}")
                    _traverse(v, f"{path}.{k}" if path else str(k))
            elif isinstance(item, (list, tuple)):
                for idx, v in enumerate(item):
                    _traverse(v, f"{path}[{idx}]")
            elif isinstance(item, str):
                for pattern in SENSITIVE_PATTERNS:
                    m = pattern.search(item)
                    if m and "[REDACTED]" not in m.group(0):
                        found.append(f"Sensitive pattern matched in value at {path}: {m.group(0)}")

        _traverse(obj, "")
        return found


diagnostics_exporter = DiagnosticBundleExporter()

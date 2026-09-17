from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class IncidentSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


@dataclass
class IncidentRecord:
    incident_id: str
    severity: IncidentSeverity
    incident_type: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    resolved: bool = False
    timestamp_ns: int = field(default_factory=time.time_ns)
    resolution_timestamp_ns: int | None = None
    resolution_notes: str | None = None


class IncidentTimeline:
    """Maintains a persistent incident timeline and UI diagnostic explanations (§16, §16.2)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.incidents: list[IncidentRecord] = []
        if self.db_path:
            self._init_db()

    def _init_db(self) -> None:
        if not self.db_path:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    severity TEXT NOT NULL,
                    incident_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    resolved INTEGER NOT NULL,
                    timestamp_ns INTEGER NOT NULL,
                    resolution_timestamp_ns INTEGER,
                    resolution_notes TEXT
                )
            """)
            conn.commit()

    def record_incident(
        self,
        incident_id: str,
        severity: IncidentSeverity,
        incident_type: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> IncidentRecord:
        """Records a new operational incident to timeline."""
        record = IncidentRecord(
            incident_id=incident_id,
            severity=severity,
            incident_type=incident_type,
            summary=summary,
            details=dict(details or {}),
        )
        self.incidents.append(record)

        if self.db_path:
            import json

            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO incidents (
                        incident_id, severity, incident_type, summary, details_json,
                        resolved, timestamp_ns, resolution_timestamp_ns, resolution_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.incident_id,
                        record.severity.value,
                        record.incident_type,
                        record.summary,
                        json.dumps(record.details),
                        1 if record.resolved else 0,
                        record.timestamp_ns,
                        record.resolution_timestamp_ns,
                        record.resolution_notes,
                    ),
                )
                conn.commit()

        return record

    def resolve_incident(self, incident_id: str, notes: str | None = None) -> bool:
        """Marks an active incident as resolved."""
        for inc in self.incidents:
            if inc.incident_id == incident_id:
                inc.resolved = True
                inc.resolution_timestamp_ns = time.time_ns()
                inc.resolution_notes = notes
                if self.db_path:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute(
                            """
                            UPDATE incidents
                            SET resolved = 1, resolution_timestamp_ns = ?, resolution_notes = ?
                            WHERE incident_id = ?
                            """,
                            (inc.resolution_timestamp_ns, inc.resolution_notes, incident_id),
                        )
                        conn.commit()
                return True
        return False

    def get_unresolved(self) -> list[IncidentRecord]:
        """Returns all currently active / unresolved incidents."""
        return [inc for inc in self.incidents if not inc.resolved]

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Returns recent incidents formatted for operator UI."""
        return [asdict(inc) for inc in reversed(self.incidents[-limit:])]


incident_timeline = IncidentTimeline()

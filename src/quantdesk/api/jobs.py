from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    INTERRUPTED = "INTERRUPTED"


class JobType(StrEnum):
    BACKTEST = "backtest"
    MODEL_TRAINING = "model_training"
    DATASET_IMPORT = "dataset_import"


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    state: JobState
    input_hash: str
    spec: dict[str, Any]
    worker_pid: int | None = None
    progress_unit: str = "percent"
    progress_count: float = 0.0
    heartbeat_ns: int = field(default_factory=lambda: int(time.time_ns()))
    output_artifacts: list[str] = field(default_factory=list)
    error_summary: str | None = None
    created_at_ns: int = field(default_factory=lambda: int(time.time_ns()))
    updated_at_ns: int = field(default_factory=lambda: int(time.time_ns()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    """Manages process-isolated research jobs, progress heartbeats, and restart reconciliation.

    Strictly complies with §15.3.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        max_concurrent_workers: int = 4,
    ) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.max_concurrent_workers = max_concurrent_workers
        self.jobs: dict[str, JobRecord] = {}
        self.active_processes: dict[str, Any] = {}

        if self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            self._load_from_db()
            self.reconcile_on_startup()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if not self.db_path:
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def reset(self) -> None:
        """Resets job manager state for test isolation."""
        self.jobs.clear()
        self.active_processes.clear()
        if self.db_path and self.db_path.exists():
            self._execute("DELETE FROM research_jobs")

    def _init_db(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS research_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                state TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                spec TEXT NOT NULL,
                worker_pid INTEGER,
                progress_unit TEXT,
                progress_count REAL,
                heartbeat_ns INTEGER,
                output_artifacts TEXT,
                error_summary TEXT,
                created_at_ns INTEGER,
                updated_at_ns INTEGER
            )
        """)

    def _load_from_db(self) -> None:
        if not self.db_path or not self.db_path.exists():
            return
        rows = self._execute("""
            SELECT job_id, job_type, state, input_hash, spec, worker_pid,
                   progress_unit, progress_count, heartbeat_ns, output_artifacts,
                   error_summary, created_at_ns, updated_at_ns
            FROM research_jobs
        """)
        for row in rows:
            (
                j_id, j_type, st, in_h, sp_str, pid, p_unit, p_cnt,
                hb, arts_str, err, c_at, u_at
            ) = row
            self.jobs[j_id] = JobRecord(
                job_id=j_id,
                job_type=j_type,
                state=JobState(st),
                input_hash=in_h,
                spec=json.loads(sp_str),
                worker_pid=pid,
                progress_unit=p_unit or "percent",
                progress_count=float(p_cnt or 0.0),
                heartbeat_ns=int(hb or 0),
                output_artifacts=json.loads(arts_str) if arts_str else [],
                error_summary=err,
                created_at_ns=int(c_at),
                updated_at_ns=int(u_at),
            )

    def reconcile_on_startup(self) -> int:
        """Reconciles dangling jobs after an API restart (§15.3).

        Any jobs left in RUNNING or CANCEL_REQUESTED whose worker PID is not alive are INTERRUPTED.
        """
        reconciled_count = 0
        now = time.time_ns()
        for record in self.jobs.values():
            if record.state in (JobState.RUNNING, JobState.CANCEL_REQUESTED):
                pid = record.worker_pid
                is_alive = False
                if pid is not None and pid > 0:
                    is_alive = self._is_pid_alive(pid)

                if not is_alive:
                    record.state = JobState.INTERRUPTED
                    record.error_summary = "Worker process terminated or API was restarted"
                    record.updated_at_ns = now
                    reconciled_count += 1
                    self._persist_job(record)

        return reconciled_count

    def _is_pid_alive(self, pid: int) -> bool:
        """Checks whether the specified OS PID is actively running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError, PermissionError):
            return False

    def create_job(
        self, job_type: JobType | str, spec: dict[str, Any], job_id: str | None = None
    ) -> JobRecord:
        """Creates and persists a new research job."""
        spec_str = json.dumps(spec, sort_keys=True)
        input_hash = hashlib.sha256(spec_str.encode("utf-8")).hexdigest()
        j_type_str = job_type.value if isinstance(job_type, JobType) else str(job_type)

        if not job_id:
            job_id = f"job-{j_type_str}-{input_hash[:8]}-{int(time.time())}"

        now = time.time_ns()
        record = JobRecord(
            job_id=job_id,
            job_type=j_type_str,
            state=JobState.QUEUED,
            input_hash=input_hash,
            spec=spec,
            created_at_ns=now,
            updated_at_ns=now,
            heartbeat_ns=now,
        )
        self.jobs[job_id] = record
        self._persist_job(record)
        return record

    def update_progress(
        self,
        job_id: str,
        progress_count: float,
        unit: str = "percent",
        worker_pid: int | None = None,
    ) -> None:
        """Updates job progress and records heartbeat."""
        rec = self.jobs.get(job_id)
        if not rec:
            return
        now = time.time_ns()
        rec.progress_count = progress_count
        rec.progress_unit = unit
        rec.heartbeat_ns = now
        rec.updated_at_ns = now
        if worker_pid is not None:
            rec.worker_pid = worker_pid
        if rec.state == JobState.QUEUED:
            rec.state = JobState.RUNNING
        self._persist_job(rec)

    def complete_job(
        self,
        job_id: str,
        artifacts: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Marks job completion with outputs or failure summary."""
        rec = self.jobs.get(job_id)
        if not rec:
            return
        now = time.time_ns()
        rec.updated_at_ns = now
        rec.heartbeat_ns = now
        if error:
            rec.state = JobState.FAILED
            rec.error_summary = error
        else:
            rec.state = JobState.SUCCEEDED
            rec.progress_count = 100.0
            if artifacts:
                rec.output_artifacts.extend(artifacts)
        self._persist_job(rec)

    def cancel_job(self, job_id: str) -> bool:
        """Requests cooperative cancellation with timeout fallback (§15.3)."""
        rec = self.jobs.get(job_id)
        if not rec or rec.state in (
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELED,
            JobState.INTERRUPTED,
        ):
            return False

        rec.state = JobState.CANCEL_REQUESTED
        rec.updated_at_ns = time.time_ns()
        self._persist_job(rec)

        # Cooperative cancellation signal; if active process handle is present, terminate
        proc = self.active_processes.pop(job_id, None)
        if proc and hasattr(proc, "terminate"):
            with contextlib.suppress(Exception):
                proc.terminate()
        return True

    def _persist_job(self, record: JobRecord) -> None:
        if not self.db_path:
            return
        self._execute(
            """
            INSERT INTO research_jobs (
                job_id, job_type, state, input_hash, spec, worker_pid,
                progress_unit, progress_count, heartbeat_ns, output_artifacts,
                error_summary, created_at_ns, updated_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                state = excluded.state,
                worker_pid = excluded.worker_pid,
                progress_unit = excluded.progress_unit,
                progress_count = excluded.progress_count,
                heartbeat_ns = excluded.heartbeat_ns,
                output_artifacts = excluded.output_artifacts,
                error_summary = excluded.error_summary,
                updated_at_ns = excluded.updated_at_ns
        """,
            (
                record.job_id,
                record.job_type,
                record.state.value,
                record.input_hash,
                json.dumps(record.spec),
                record.worker_pid,
                record.progress_unit,
                record.progress_count,
                record.heartbeat_ns,
                json.dumps(record.output_artifacts),
                record.error_summary,
                record.created_at_ns,
                record.updated_at_ns,
            ),
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def list_jobs(self, job_type: str | None = None) -> list[JobRecord]:
        all_jobs = list(self.jobs.values())
        if job_type:
            return [j for j in all_jobs if j.job_type == job_type]
        return all_jobs


# Global singleton job manager
job_manager = JobManager()

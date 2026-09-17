from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from quantdesk.api.jobs import JobManager, JobState, JobType


def test_job_restart_reconciliation_marks_dead_workers_interrupted():
    """Verifies that API restart reconciles dead worker PIDs to INTERRUPTED per §15.3."""
    with TemporaryDirectory(prefix="quantdesk-jobs-") as temp_dir:
        db_path = Path(temp_dir) / "jobs.sqlite"

        # 1. Start JobManager and spawn a job
        jm1 = JobManager(db_path=db_path)
        job = jm1.create_job(
            job_type=JobType.BACKTEST,
            spec={"strategy_id": "momentum-btc", "dataset_id": "btc-2026"},
        )
        assert job.state == JobState.QUEUED

        # 2. Worker begins running with a fictitious PID that does not exist
        dead_pid = 99999999
        jm1.update_progress(job.job_id, progress_count=45.0, worker_pid=dead_pid)
        assert jm1.get_job(job.job_id).state == JobState.RUNNING

        # 3. Simulate API restart: discard jm1 from memory and instantiate fresh jm2 from disk
        jm2 = JobManager(db_path=db_path)
        recovered_job = jm2.get_job(job.job_id)

        # 4. Verification: dead worker was reconciled to INTERRUPTED (§15.3)
        assert recovered_job is not None
        assert recovered_job.state == JobState.INTERRUPTED
        assert recovered_job.progress_count == 45.0
        assert "terminated or API was restarted" in str(recovered_job.error_summary)


def test_job_cancellation_and_completion_lifecycle():
    """Exercises cooperative cancellation and artifact completion (§15.3)."""
    with TemporaryDirectory(prefix="quantdesk-jobs-") as temp_dir:
        db_path = Path(temp_dir) / "jobs.sqlite"
        jm = JobManager(db_path=db_path)

        # 1. Create and complete a training job
        job = jm.create_job(
            job_type=JobType.MODEL_TRAINING,
            spec={"algorithm": "lightgbm", "dataset_id": "ds-1"},
        )
        jm.update_progress(job.job_id, progress_count=50.0)
        jm.complete_job(job.job_id, artifacts=["model_manifest.json", "model.txt"])

        finished = jm.get_job(job.job_id)
        assert finished.state == JobState.SUCCEEDED
        assert finished.progress_count == 100.0
        assert "model_manifest.json" in finished.output_artifacts

        # 2. Create another job and cancel it
        job_cancel = jm.create_job(
            job_type=JobType.BACKTEST,
            spec={"strategy_id": "imbalance-btc"},
        )
        jm.update_progress(job_cancel.job_id, progress_count=15.0)

        # Request cancel
        success = jm.cancel_job(job_cancel.job_id)
        assert success is True
        assert jm.get_job(job_cancel.job_id).state == JobState.CANCEL_REQUESTED

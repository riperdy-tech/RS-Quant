from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from quantdesk.api.auth import Session, require_operator, require_viewer
from quantdesk.api.jobs import JobType, job_manager
from quantdesk.api.security import confine_path

router = APIRouter(prefix="/api/v1", tags=["research"])

ARTIFACTS_DIR = Path("artifacts")
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


class BacktestJobRequest(BaseModel):
    dataset_id: str
    strategy_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    initial_cash: float = 10000.0


class TrainingJobRequest(BaseModel):
    dataset_id: str
    algorithm: str = "lightgbm"
    parameters: dict[str, Any] = Field(default_factory=dict)


class DatasetImportRequest(BaseModel):
    source_path: str
    instrument_id: str
    timeframe: str = "1m"


@router.get("/datasets")
def list_datasets(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Lists imported datasets and metadata manifests (§15.2)."""
    return [
        {
            "dataset_id": "demo_btc_2026",
            "instrument_id": "BTCUSDT",
            "timeframe": "1m",
            "format": "parquet",
            "capabilities": ["OHLCV", "L2", "TRADES"],
            "rows": 10000,
            "origin": "demo-only",
        }
    ]


@router.post("/datasets/imports", status_code=status.HTTP_202_ACCEPTED)
def start_dataset_import(
    req: DatasetImportRequest,
    session: Session = Depends(require_operator),
) -> dict[str, Any]:
    is_absolute_or_traversal = (
        ".." in req.source_path
        or req.source_path.startswith("/")
        or (len(req.source_path) > 1 and req.source_path[1] == ":")
    )
    if is_absolute_or_traversal:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal or absolute path is prohibited in source_path",
        )
    job = job_manager.create_job(
        job_type=JobType.DATASET_IMPORT,
        spec=req.model_dump(),
    )
    return {"job_id": job.job_id, "status": job.state.value}


@router.get("/backtests")
def list_backtests(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Lists completed backtests and run manifests (§15.2)."""
    return [
        {
            "backtest_id": "bt-demo-01",
            "strategy_id": "momentum-btc",
            "dataset_id": "demo_btc_2026",
            "net_pnl": "450.20",
            "sharpe_ratio": 1.85,
            "max_drawdown_pct": 2.1,
            "trades_count": 42,
        }
    ]


@router.post("/backtests", status_code=status.HTTP_202_ACCEPTED)
def queue_backtest(
    req: BacktestJobRequest,
    session: Session = Depends(require_operator),
) -> dict[str, Any]:
    """Queues a process-isolated backtest job (§15.3)."""
    job = job_manager.create_job(
        job_type=JobType.BACKTEST,
        spec=req.model_dump(),
    )
    return {"job_id": job.job_id, "status": job.state.value}


@router.get("/models")
def list_models(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Lists registered models, champions, and drift indicators (§15.2)."""
    return [
        {
            "model_id": "lgbm-champion",
            "algorithm": "lightgbm",
            "status": "ACTIVE",
            "environment": "DEMO",
            "log_loss": 0.35,
            "brier_score": 0.12,
            "drift_psi": 0.05,
        }
    ]


@router.post("/models/training", status_code=status.HTTP_202_ACCEPTED)
def queue_model_training(
    req: TrainingJobRequest,
    session: Session = Depends(require_operator),
) -> dict[str, Any]:
    """Queues a candidate model training job (§15.3)."""
    job = job_manager.create_job(
        job_type=JobType.MODEL_TRAINING,
        spec=req.model_dump(),
    )
    return {"job_id": job.job_id, "status": job.state.value}


@router.get("/jobs")
def list_jobs(
    job_type: str | None = None,
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Lists research jobs and worker statuses (§15.3)."""
    return [j.to_dict() for j in job_manager.list_jobs(job_type)]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Retrieves progress, heartbeats, and status of a research job (§15.3)."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    session: Session = Depends(require_operator),
) -> dict[str, Any]:
    """Requests cooperative cancellation for a research job (§15.3)."""
    success = job_manager.cancel_job(job_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel job {job_id} (already terminal or not found)",
        )
    return {"job_id": job_id, "status": "CANCEL_REQUESTED"}


@router.get("/artifacts/{artifact_filename}/download")
def download_artifact(
    artifact_filename: str,
    session: Session = Depends(require_viewer),
) -> FileResponse:
    """Downloads a confined output artifact file safely preventing traversal (§15.4)."""
    safe_path = confine_path(ARTIFACTS_DIR, artifact_filename)
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file not found")
    return FileResponse(safe_path)

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from quantdesk.api.auth import Session, require_operator, require_viewer
from quantdesk.api.commands import durable_inbox

router = APIRouter(prefix="/api/v1", tags=["commands"])


class CommandPreviewRequest(BaseModel):
    type: str
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandSubmissionRequest(BaseModel):
    command_id: str
    type: str
    target: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    confirmation_token: str | None = None
    confirmation_text: str | None = None


@router.post("/command-previews")
def create_command_preview(
    req: CommandPreviewRequest,
    session: Session = Depends(require_operator),
) -> dict[str, Any]:
    """Generates an action preview and single-use confirmation token (§15.3)."""
    return durable_inbox.create_preview(
        command_type=req.type,
        target=req.target,
        payload=req.payload,
        actor=session.username,
    )


@router.post("/commands", status_code=status.HTTP_202_ACCEPTED)
def submit_command(
    req: CommandSubmissionRequest,
    response: Response,
    session: Session = Depends(require_operator),
) -> dict[str, Any]:
    """Submits a durable command with idempotency and CAS revision checks (§15.3)."""
    status_code, status_str = durable_inbox.submit(
        command_id=req.command_id,
        body=req.model_dump(),
        actor=session.username,
    )

    if status_code == 409:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Command conflict: {status_str}",
        )
    elif status_code == 403:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Confirmation error: {status_str}",
        )

    response.status_code = status.HTTP_202_ACCEPTED
    return {
        "command_id": req.command_id,
        "status": status_str,
        "status_url": f"/api/v1/commands/{req.command_id}",
    }


@router.get("/commands/{command_id}")
def get_command_status(
    command_id: str,
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns durable status and execution history of a command (§15.3)."""
    rec = durable_inbox.get_record(command_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Command {command_id} not found",
        )
    return rec.to_dict()

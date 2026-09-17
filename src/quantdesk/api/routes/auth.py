from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from quantdesk.api.auth import Session, auth_manager, get_current_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class BootstrapRequest(BaseModel):
    bootstrap_token: str
    admin_password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/bootstrap")
def bootstrap_admin(req: BootstrapRequest, response: Response) -> dict[str, Any]:
    """One-time admin enrollment using the startup token (§15.4)."""
    admin_user = auth_manager.bootstrap(
        token=req.bootstrap_token,
        admin_password=req.admin_password,
    )
    # Auto-login the admin upon bootstrap
    session = auth_manager.create_session(admin_user, response)
    return {
        "status": "ok",
        "username": admin_user.username,
        "role": admin_user.role.value,
        "csrf_token": session.csrf_token,
    }


@router.post("/login")
def login(req: LoginRequest, response: Response) -> dict[str, Any]:
    """Authenticates user with Argon2id and creates a secure session (§15.4)."""
    user = auth_manager.authenticate(req.username, req.password)
    session = auth_manager.create_session(user, response)
    return {
        "username": user.username,
        "role": user.role.value,
        "csrf_token": session.csrf_token,
    }


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    """Terminates active session and invalidates cookies."""
    session_id = request.cookies.get("session_id")
    if session_id:
        auth_manager.terminate_session(session_id, response)
    return {"status": "logged_out"}


@router.get("/me")
def get_current_profile(
    session: Session = Depends(get_current_session),
) -> dict[str, Any]:
    """Returns currently authenticated user profile and active role."""
    return {
        "username": session.username,
        "role": session.role.value,
        "csrf_token": session.csrf_token,
    }

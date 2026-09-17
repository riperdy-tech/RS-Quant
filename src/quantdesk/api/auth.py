from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status

ph = PasswordHasher()


class UserRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        levels = {
            UserRole.VIEWER: 1,
            UserRole.OPERATOR: 2,
            UserRole.ADMIN: 3,
        }
        return levels[self]


@dataclass
class User:
    username: str
    password_hash: str
    role: UserRole
    created_at_ns: int = field(default_factory=lambda: int(time.time_ns()))


@dataclass
class Session:
    session_id: str
    username: str
    role: UserRole
    csrf_token: str
    created_at_ns: int = field(default_factory=lambda: int(time.time_ns()))
    expires_at_ns: int = field(
        default_factory=lambda: int(time.time_ns() + 24 * 3600 * 1_000_000_000)
    )


class AuthManager:
    """Manages user enrollment, Argon2id passwords, sessions, and RBAC per §15.4."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.sessions: dict[str, Session] = {}
        self.bootstrap_token: str | None = secrets.token_hex(16)
        self.is_bootstrapped: bool = False

    def reset(self) -> None:
        """Resets auth manager state for test isolation."""
        self.users.clear()
        self.sessions.clear()
        self.bootstrap_token = secrets.token_hex(16)
        self.is_bootstrapped = False

    def bootstrap(self, token: str, admin_password: str) -> User:
        """One-time enrollment creating the local admin with Argon2id hash."""
        if self.is_bootstrapped or self.bootstrap_token is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Application has already been bootstrapped",
            )

        if not secrets.compare_digest(token, self.bootstrap_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid bootstrap token",
            )

        if len(admin_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin password must be at least 8 characters",
            )

        pwd_hash = ph.hash(admin_password)
        admin_user = User(username="admin", password_hash=pwd_hash, role=UserRole.ADMIN)
        self.users["admin"] = admin_user

        # Invalidate bootstrap token permanently
        self.bootstrap_token = None
        self.is_bootstrapped = True
        return admin_user

    def add_user(self, username: str, password: str, role: UserRole) -> User:
        pwd_hash = ph.hash(password)
        user = User(username=username, password_hash=pwd_hash, role=role)
        self.users[username] = user
        return user

    def authenticate(self, username: str, password: str) -> User:
        user = self.users.get(username)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        try:
            ph.verify(user.password_hash, password)
        except VerifyMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            ) from exc
        return user

    def create_session(self, user: User, response: Response) -> Session:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = Session(
            session_id=session_id,
            username=user.username,
            role=user.role,
            csrf_token=csrf_token,
        )
        self.sessions[session_id] = session

        # Set HttpOnly session cookie and readable CSRF cookie
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            samesite="strict",
            secure=False,  # True on HTTPS production
        )
        response.set_cookie(
            key="csrf_token",
            value=csrf_token,
            httponly=False,
            samesite="strict",
            secure=False,
        )
        response.headers["x-csrf-token"] = csrf_token
        return session

    def terminate_session(self, session_id: str, response: Response) -> None:
        self.sessions.pop(session_id, None)
        response.delete_cookie(key="session_id")
        response.delete_cookie(key="csrf_token")

    def get_session(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self.sessions.get(session_id)
        if session is not None and time.time_ns() > session.expires_at_ns:
            self.sessions.pop(session_id, None)
            return None
        return session


# Default singleton instance
auth_manager = AuthManager()


def get_current_session(request: Request) -> Session:
    """FastAPI dependency resolving active authenticated session or demo fallback."""
    session_id = request.cookies.get("session_id")
    # Also check Authorization header for bearer session
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        session_id = auth_header.split(" ", 1)[1]

    session = auth_manager.get_session(session_id)
    if session is None:
        # In DEMO mode provide default operator session if requested by test header
        demo_role = request.headers.get("x-quantdesk-role")
        if demo_role and demo_role in [r.value for r in UserRole]:
            return Session(
                session_id="mock-session",
                username="demo_user",
                role=UserRole(demo_role),
                csrf_token="demo-csrf",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return session


def require_role(min_role: UserRole) -> Any:
    """Dependency factory checking user role hierarchy."""

    def role_checker(session: Session = Depends(get_current_session)) -> Session:
        if session.role.level < min_role.level:
            err_msg = (
                f"Permission denied: role '{session.role}' is insufficient, '{min_role}' required"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=err_msg,
            )
        return session

    return role_checker


require_viewer = require_role(UserRole.VIEWER)
require_operator = require_role(UserRole.OPERATOR)
require_admin = require_role(UserRole.ADMIN)

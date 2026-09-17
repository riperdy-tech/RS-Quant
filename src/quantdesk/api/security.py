from __future__ import annotations

import secrets
from collections.abc import Sequence
from pathlib import Path

from fastapi import HTTPException, Request, status

DEFAULT_ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "testserver",
    "127.0.0.1:8000",
    "localhost:8000",
    "testserver:80",
    "127.0.0.1:5173",
    "localhost:5173",
]
DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://testserver",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]


def generate_csrf_token() -> str:
    """Generates a cryptographically strong CSRF token."""
    return secrets.token_urlsafe(32)


def validate_host(request: Request, allowed_hosts: Sequence[str] = DEFAULT_ALLOWED_HOSTS) -> None:
    """Validates the HTTP Host header against allowed hosts to prevent DNS rebinding (§15.4)."""
    host = request.headers.get("host")
    if not host:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Host header",
        )
    # Strip port if present for comparison
    host_clean = host.split(":")[0]
    allowed_clean = [h.split(":")[0] for h in allowed_hosts]
    if host not in allowed_hosts and host_clean not in allowed_clean:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Host '{host}' is not permitted",
        )


def validate_origin(
    request: Request, allowed_origins: Sequence[str] = DEFAULT_ALLOWED_ORIGINS
) -> None:
    """Validates Origin or Referer header on mutating requests (§15.4)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    if origin:
        if origin not in allowed_origins:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Origin '{origin}' is not permitted",
            )
        return

    if referer and not any(referer.startswith(allowed) for allowed in allowed_origins):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Referer '{referer}' is not permitted",
        )


def verify_csrf(request: Request) -> None:
    """Verifies that state-changing requests include a valid CSRF token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    # In development/demo mode with bearer token or explicit header
    token_header = request.headers.get("x-csrf-token")
    token_cookie = request.cookies.get("csrf_token")

    if (
        not token_header
        or not token_cookie
        or not secrets.compare_digest(token_header, token_cookie)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token",
        )


def confine_path(base_dir: Path | str, relative_path: str) -> Path:
    """Safely confines an artifact or dataset path within a base directory (§15.4)."""
    base = Path(base_dir).resolve()
    target = (base / relative_path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access outside resource directory is prohibited",
        ) from exc
    return target

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from quantdesk.api.commands import durable_inbox
from quantdesk.api.jobs import job_manager
from quantdesk.api.routes import auth, commands, events, research, system, trading
from quantdesk.api.security import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_ALLOWED_ORIGINS,
    validate_host,
    validate_origin,
    verify_csrf,
)
from quantdesk.observability.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle management for QuantDesk API (§15.3)."""
    logger.info("Starting QuantDesk Control API on 127.0.0.1...")
    # 1. Reconcile dead jobs on API restart
    reconciled = job_manager.reconcile_on_startup()
    if reconciled > 0:
        logger.warning(f"Reconciled {reconciled} interrupted research jobs from prior run")

    # 2. Start live Bitget market data feed service
    from quantdesk.venues.bitget_uta.live_feed import live_feed_service

    await live_feed_service.start()

    yield

    logger.info("Shutting down QuantDesk Control API...")
    await live_feed_service.stop()


def create_app(db_path: Path | str | None = None) -> FastAPI:
    """FastAPI Application factory configuring routes, middleware, and security (§15, §15.4)."""
    app = FastAPI(
        title="QuantDesk Control API",
        version="0.1.0",
        description="Deterministic trading research and execution control API per §15.",
        lifespan=lifespan,
    )

    if db_path:
        p = Path(db_path)
        durable_inbox.db_path = p
        durable_inbox._init_db()
        durable_inbox._load_from_db()
        job_manager.db_path = p
        job_manager._init_db()
        job_manager._load_from_db()
        job_manager.reconcile_on_startup()

    # Security & Error Handling Middleware
    @app.middleware("http")
    async def security_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 1. Validate Host header to prevent DNS rebinding
        try:
            validate_host(request, DEFAULT_ALLOWED_HOSTS)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": "FORBIDDEN_HOST", "message": exc.detail}},
            )

        # 2. Validate Origin header on mutating requests
        try:
            validate_origin(request, DEFAULT_ALLOWED_ORIGINS)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": "FORBIDDEN_ORIGIN", "message": exc.detail}},
            )

        # 3. Check CSRF token for state-changing calls (except bootstrap and login)
        if (
            request.url.path not in ("/api/v1/auth/bootstrap", "/api/v1/auth/login")
            and "csrf_token" in request.cookies
        ):
            try:
                verify_csrf(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"error": {"code": "CSRF_FAILED", "message": exc.detail}},
                )

        response: Response = await call_next(request)

        # 4. Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    # Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"HTTP_{exc.status_code}", "message": exc.detail}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": {"code": "VALIDATION_ERROR", "message": str(exc.errors())}},
        )

    # Register Routers
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(trading.router)
    app.include_router(commands.router)
    app.include_router(research.router)
    app.include_router(events.router)

    # Serve static frontend with SPA routing fallback if built (§17)
    dist_dir = Path("web/dist")
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static_assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> Response:
            if full_path.startswith("api/") or full_path.startswith("health/"):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
            file_path = dist_dir / full_path
            if full_path and file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(dist_dir / "index.html")

    return app


app = create_app()

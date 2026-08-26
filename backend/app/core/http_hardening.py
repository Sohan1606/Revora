"""HTTP hardening middleware: request IDs, security headers, global rate budget,
generic 500s (no stack leakage)."""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.ratelimit import RateLimitExceeded, enforce_global_limit

logger = logging.getLogger("revora.http")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def install_hardening(app: FastAPI) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.monotonic()

        try:
            enforce_global_limit(request)
        except RateLimitExceeded as exc:
            response = JSONResponse(
                status_code=429, content={"detail": exc.detail},
                headers=exc.headers or {})
            response.headers["X-Request-ID"] = request_id
            _apply(response)
            return response

        try:
            response = await call_next(request)
        except Exception:
            # Never leak internals; full traceback goes to structured logs only.
            logger.exception("unhandled error", extra={
                "request_id": request_id, "path": request.url.path})
            response = JSONResponse(
                status_code=500,
                content={"detail": "internal_server_error", "request_id": request_id},
            )

        duration_ms = round((time.monotonic() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        _apply(response)
        if request.url.path not in ("/api/health",):  # access noise control
            logger.info("request", extra={
                "request_id": request_id, "method": request.method,
                "path": request.url.path, "status": response.status_code,
                "duration_ms": duration_ms})
        return response

    def _apply(response) -> None:
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)

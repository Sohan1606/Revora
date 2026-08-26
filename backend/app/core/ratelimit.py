"""In-process sliding-window rate limiter.

Honest scope note: this is per-process state — correct for a single-instance
hackathon deployment. Multi-instance production needs Redis (documented in
docs/SECURITY_AUDIT.md as an accepted, stated limitation).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

_windows: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int):
        super().__init__(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded — slow down.",
            headers={"Retry-After": str(retry_after)},
        )


def _hit(key: str, limit: int, window_seconds: int) -> None:
    if not get_settings().RATE_LIMIT_ENABLED:
        return
    now = time.monotonic()
    with _lock:
        window = _windows[key]
        cutoff = now - window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= limit:
            raise RateLimitExceeded(window_seconds)
        window.append(now)


def client_rate_key(request: Request, scope: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{scope}:{ip}"


def rate_limit(limit: int, window_seconds: int, scope: str):
    """Route dependency factory: limit requests per client IP within a window."""

    def _dependency(request: Request) -> None:
        _hit(client_rate_key(request, scope), limit, window_seconds)

    return _dependency


def enforce_global_limit(request: Request, limit: int = 300, window_seconds: int = 60) -> None:
    """Bare-API-wide budget per IP (called from middleware)."""
    _hit(client_rate_key(request, "global"), limit, window_seconds)

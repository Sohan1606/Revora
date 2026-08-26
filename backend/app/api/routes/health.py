"""Liveness/readiness endpoint. Performs a real `SELECT 1` against the configured DB."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database import get_db

router = APIRouter(tags=["system"])
_settings = get_settings()


@router.get("/health")
def health(db: Session = Depends(get_db)) -> Response:
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    payload = {
        "status": "ok" if db_ok else "degraded",
        "app": _settings.APP_NAME,
        "version": _settings.APP_VERSION,
        "env": _settings.ENV,
        "time": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "database": "connected" if db_ok else "unavailable",
            "razorpay_configured": _settings.is_razorpay_configured,
            "razorpay_webhook_configured": _settings.is_razorpay_webhook_configured,
        },
    }
    code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(
        content=json.dumps(payload),
        status_code=code,
        media_type="application/json",
    )

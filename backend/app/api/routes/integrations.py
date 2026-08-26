"""Integration status routes.

- GET  /integrations/razorpay  → configuration state (no secrets, ever)
- POST /integrations/razorpay/verify → REAL credential check (read-only ping to
  Razorpay), updates merchant_integrations status. Fails clearly when unset.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_minimum
from app.core.config import get_settings
from app.infrastructure.database import get_db
from app.infrastructure.models import MerchantIntegration, User

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/razorpay")
def razorpay_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    integration = db.execute(
        select(MerchantIntegration).where(
            MerchantIntegration.merchant_id == user.merchant_id,
            MerchantIntegration.provider == "razorpay",
        )
    ).scalar_one_or_none()

    key_mode = None
    if settings.RAZORPAY_KEY_ID.startswith("rzp_test_"):
        key_mode = "test"
    elif settings.RAZORPAY_KEY_ID.startswith("rzp_live_"):
        key_mode = "live (refused by design)"

    return {
        "configured": settings.is_razorpay_configured,
        "mode": key_mode,
        "webhook_configured": settings.is_razorpay_webhook_configured,
        "last_verified_status": integration.status if integration else None,
        "last_error": integration.last_error if integration else None,
        "message": None if settings.is_razorpay_configured else (
            "Razorpay integration not configured — add Razorpay Test Mode "
            "credentials to .env."
        ),
    }


@router.post("/razorpay/verify")
def razorpay_verify(
    user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if not settings.is_razorpay_configured:
        raise HTTPException(503, "Razorpay integration not configured — add Razorpay "
                                 "Test Mode credentials to .env.")
    if settings.RAZORPAY_KEY_ID.startswith("rzp_live_"):
        raise HTTPException(400, "Razorpay LIVE keys detected — REVORA is Test Mode "
                                 "only by design. Refusing to proceed.")

    from app.integrations.razorpay.client import RazorpayAPIError, get_client
    integration = db.execute(
        select(MerchantIntegration).where(
            MerchantIntegration.merchant_id == user.merchant_id,
            MerchantIntegration.provider == "razorpay",
        )
    ).scalar_one_or_none()
    if integration is None:
        integration = MerchantIntegration(merchant_id=user.merchant_id, provider="razorpay")
        db.add(integration)

    try:
        client = get_client()
        result = client.ping()  # read-only, no money movement possible
    except RazorpayAPIError as exc:
        integration.status = "error"
        integration.last_error = f"HTTP {exc.status}: {exc.detail}"
        db.commit()
        raise HTTPException(502, f"Credential check failed — {exc}")

    integration.status = "connected"
    integration.last_error = None
    integration.config = {"verified_at": datetime.now(timezone.utc).isoformat(),
                          "mode": client.mode, "key_prefix": client.key_id[:9]}
    db.commit()
    return {"status": "connected", "mode": client.mode,
            "key_prefix": client.key_id[:9], "ping": "ok (read-only)"}

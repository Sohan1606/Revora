"""Analytics routes — control center + action performance."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.domain.recovery.analytics import action_performance, control_center
from app.infrastructure.database import get_db
from app.infrastructure.models import User

router = APIRouter(tags=["analytics"])


@router.get("/control-center")
def control_center_endpoint(
    user: User = Depends(get_current_user), db=Depends(get_db),
) -> dict:
    return control_center(db, user.merchant_id)


@router.get("/analytics/action-performance")
def action_performance_endpoint(
    user: User = Depends(get_current_user), db=Depends(get_db),
) -> dict:
    return {"actions": action_performance(db, user.merchant_id)}

"""Append-only audit writer. Every material system action goes through here."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.models import AuditEvent


def record_audit(
    db: Session,
    *,
    event_type: str,
    merchant_id: str | None = None,
    case_id: str | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        merchant_id=merchant_id,
        case_id=case_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        payload=payload or {},
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    return event

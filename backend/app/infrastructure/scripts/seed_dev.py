"""Seed local-dev merchant + role users. LOCAL ONLY — refuses to run otherwise.

Run from backend/:  python -m app.infrastructure.scripts.seed_dev
Requires DEV_JWT_SECRET in .env to use the dev token endpoint afterwards.
"""
from __future__ import annotations

import sys

from app.core.config import get_settings
from app.infrastructure.database import Base, SessionLocal, engine
import app.infrastructure.models  # noqa: F401 — registers tables
from app.infrastructure.models import Merchant, User

DEV_USERS = [
    ("dev-owner@revora.local", "Dev Owner", "owner"),
    ("dev-admin@revora.local", "Dev Admin", "admin"),
    ("dev-operator@revora.local", "Dev Operator", "operator"),
    ("dev-viewer@revora.local", "Dev Viewer", "viewer"),
]


def seed_dev_users(db) -> None:
    merchant = db.query(Merchant).filter(Merchant.name == "REVORA Dev Merchant").one_or_none()
    if merchant is None:
        merchant = Merchant(name="REVORA Dev Merchant", business_type="dev")
        db.add(merchant)
        db.flush()
        print(f"Created merchant {merchant.id}")
    else:
        print(f"Merchant exists {merchant.id}")

    for email, name, role in DEV_USERS:
        if db.query(User).filter(User.email == email).one_or_none() is None:
            db.add(User(merchant_id=merchant.id, email=email, full_name=name, role=role))
            print(f"Created user {email} ({role})")
        else:
            print(f"User exists {email}")
    db.commit()


def main() -> int:
    settings = get_settings()
    if settings.ENV != "local":
        print("REFUSED: seed_dev is local-only (ENV != local).")
        return 1
    if not settings.DEV_JWT_SECRET:
        print("WARNING: DEV_JWT_SECRET is not set in backend/.env — sign-in will return 503.")
        print("         Run 'python -m app.infrastructure.scripts.dev_up' which sets it automatically.")

    # Self-sufficient + idempotent: ensure schema exists before seeding.
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        seed_dev_users(db)
    finally:
        db.close()

    print("\nOK. One-command launcher (recommended):")
    print("  python -m app.infrastructure.scripts.dev_up")
    return 0


if __name__ == "__main__":
    sys.exit(main())

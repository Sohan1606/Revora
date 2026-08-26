"""Merchant, users (RBAC), and integration credentials metadata.

Secrets NEVER live here — only env vars. This table stores non-secret config/state.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base, IdMixin, TimestampMixin


class Merchant(Base, IdMixin, TimestampMixin):
    __tablename__ = "merchants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_type: Mapped[str | None] = mapped_column(String(100))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="merchant")


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    merchant_id: Mapped[str | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # RBAC hierarchy enforced in app/domain (Phase 6): owner > admin > operator > viewer
    role: Mapped[str] = mapped_column(
        Enum("owner", "admin", "operator", "viewer", name="user_role",
             native_enum=False, validate_strings=True),
        default="viewer", nullable=False,
    )
    # Supabase Auth subject (`sub` claim) — linked in Phase 5.
    supabase_user_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    merchant: Mapped[Merchant | None] = relationship(back_populates="users")


class MerchantIntegration(Base, IdMixin, TimestampMixin):
    __tablename__ = "merchant_integrations"
    __table_args__ = (UniqueConstraint("merchant_id", "provider", name="uq_integration"),)

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(
        Enum("razorpay", "resend", "twilio", name="integration_provider",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    # Non-secret configuration only (e.g. webhook URL hints, from-address).
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        Enum("pending", "connected", "error", name="integration_status",
             native_enum=False, validate_strings=True),
        default="pending", nullable=False,
    )
    last_error: Mapped[str | None] = mapped_column(Text)

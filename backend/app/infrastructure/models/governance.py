"""Governance: policies, executions, outcomes, messages, append-only audit events."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base, IdMixin, TimestampMixin

from app.infrastructure.models.recovery import ACTION_TYPES


class PolicyVersion(Base, IdMixin, TimestampMixin):
    """Versioned merchant policy — immutable once activated; edits create a new version."""
    __tablename__ = "policy_versions"

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyRule(Base, IdMixin, TimestampMixin):
    __tablename__ = "policy_rules"

    policy_version_id: Mapped[str] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_type: Mapped[str] = mapped_column(
        Enum("block", "require_approval", "limit", "schedule", name="rule_type",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Execution(Base, IdMixin, TimestampMixin):
    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_execution_idempotency"),)

    case_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("decisions.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    action_type: Mapped[str] = mapped_column(
        Enum(*ACTION_TYPES, name="action_type", native_enum=False, validate_strings=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "executing", "succeeded", "failed", "skipped", "cancelled",
             name="execution_status", native_enum=False, validate_strings=True),
        default="pending", nullable=False, index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Outcome(Base, IdMixin, TimestampMixin):
    __tablename__ = "outcomes"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_cases.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True,
    )
    outcome: Mapped[str] = mapped_column(
        Enum("recovered", "not_recovered", "partial", "expired", "stopped", name="outcome_kind",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    amount_recovered_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Authoritative source only — never a client-side claim.
    source: Mapped[str] = mapped_column(
        Enum("webhook", "provider_api", "simulator", name="outcome_source",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    evidence_payment_id: Mapped[str | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL")
    )


class Message(Base, IdMixin, TimestampMixin):
    __tablename__ = "messages"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[str] = mapped_column(
        Enum("email", "sms", "whatsapp", "in_app", name="message_channel",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(50), default="simulated", nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        Enum("queued", "sent", "delivered", "bounced", "suppressed", "failed",
             name="message_status", native_enum=False, validate_strings=True),
        default="queued", nullable=False, index=True,
    )
    template_key: Mapped[str | None] = mapped_column(String(100))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base, IdMixin):
    """Append-only. No updated_at by design. Every material system action lands here."""
    __tablename__ = "audit_events"

    merchant_id: Mapped[str | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    actor_type: Mapped[str] = mapped_column(
        Enum("system", "user", "policy_engine", "model", name="actor_type",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(String(255))
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

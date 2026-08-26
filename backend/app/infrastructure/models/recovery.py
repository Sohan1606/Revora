"""Recovery domain: risk events, failure classification, cases, decisions."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base, IdMixin, TimestampMixin

RISK_SOURCES = ("payment_failed", "checkout_abandoned", "subscription_failed", "receivable_overdue")
FAILURE_CAUSES = (
    "insufficient_funds_temporary", "insufficient_funds_persistent", "auth_required",
    "method_expired", "hard_decline", "processor_issue", "customer_intent", "unknown",
)
ACTION_TYPES = (
    "wait", "retry", "request_method_update", "offer_alternative_method",
    "send_message", "escalate", "no_action",
)
CASE_STATES = (
    "at_risk", "analyzed", "action_selected", "awaiting_approval",
    "executed", "observing", "recovered", "stopped", "escalated", "closed",
)


class RevenueRiskEvent(Base, IdMixin, TimestampMixin):
    __tablename__ = "revenue_risk_events"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_risk_source"),
        CheckConstraint("amount_paise >= 0", name="ck_risk_amount_positive"),
    )

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        Enum(*RISK_SOURCES, name="risk_source_type",
             native_enum=False, validate_strings=True),
        nullable=False, index=True,
    )
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("open", "case_created", "resolved", "expired", name="risk_event_status",
             native_enum=False, validate_strings=True),
        default="open", nullable=False, index=True,
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FailureClassification(Base, IdMixin, TimestampMixin):
    __tablename__ = "failure_classifications"

    risk_event_id: Mapped[str] = mapped_column(
        ForeignKey("revenue_risk_events.id", ondelete="CASCADE"),
        unique=True, index=True, nullable=False,
    )
    primary_cause: Mapped[str] = mapped_column(
        Enum(*FAILURE_CAUSES, name="failure_cause",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    secondary_causes: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    rationale: Mapped[dict] = mapped_column(JSON, default=dict)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoveryCase(Base, IdMixin, TimestampMixin):
    __tablename__ = "recovery_cases"

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    risk_event_id: Mapped[str] = mapped_column(
        ForeignKey("revenue_risk_events.id", ondelete="RESTRICT"),
        unique=True, nullable=False,
    )
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    state: Mapped[str] = mapped_column(
        Enum(*CASE_STATES, name="case_state",
             native_enum=False, validate_strings=True),
        default="at_risk", nullable=False, index=True,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    decisions: Mapped[list["Decision"]] = relationship(back_populates="case")


class Decision(Base, IdMixin, TimestampMixin):
    __tablename__ = "decisions"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    policy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL")
    )
    chosen_action: Mapped[str] = mapped_column(
        Enum(*ACTION_TYPES, name="action_type",
             native_enum=False, validate_strings=True),
        nullable=False,
    )
    action_params: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_recovery_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0, nullable=False)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        Enum("proposed", "approved_by_policy", "blocked_by_policy",
             "requires_approval", "superseded", "executed", name="decision_status",
             native_enum=False, validate_strings=True),
        default="proposed", nullable=False, index=True,
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    case: Mapped[RecoveryCase] = relationship(back_populates="decisions")
    candidates: Mapped[list["CandidateAction"]] = relationship(back_populates="decision")


class CandidateAction(Base, IdMixin, TimestampMixin):
    """Every action the optimizer evaluated for a decision — evidence for the audit trail."""
    __tablename__ = "candidate_actions"

    decision_id: Mapped[str] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    action_type: Mapped[str] = mapped_column(
        Enum(*ACTION_TYPES, name="action_type", native_enum=False, validate_strings=True),
        nullable=False,
    )
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    p_recovery: Mapped[float] = mapped_column(Numeric(5, 4), default=0, nullable=False)
    expected_value_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    intervention_cost_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    friction_score: Mapped[float] = mapped_column(Numeric(4, 3), default=0, nullable=False)
    allowed_by_policy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)

    decision: Mapped[Decision] = relationship(back_populates="candidates")

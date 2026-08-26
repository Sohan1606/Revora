"""Experiments: treatment/control assignment for incremental-recovery measurement."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base, IdMixin, TimestampMixin


class Experiment(Base, IdMixin, TimestampMixin):
    __tablename__ = "experiments"

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    strategy_treatment: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy_control: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("draft", "running", "completed", "archived", name="experiment_status",
             native_enum=False, validate_strings=True),
        default="draft", nullable=False, index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ExperimentAssignment(Base, IdMixin):
    """Immutable once assigned — no post-outcome reassignment (evaluation integrity)."""
    __tablename__ = "experiment_assignments"
    __table_args__ = (UniqueConstraint("experiment_id", "case_id", name="uq_assignment"),)

    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    arm: Mapped[str] = mapped_column(
        Enum("treatment", "control", name="experiment_arm",
             native_enum=False, validate_strings=True),
        nullable=False, index=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

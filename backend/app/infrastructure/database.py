"""Database engine, session factory, and declarative base.

Money columns are integer paise everywhere (Razorpay convention). Never floats.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterator
from uuid import uuid4

from sqlalchemy import DateTime, Uuid, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

_settings = get_settings()
_url = _settings.effective_database_url

_connect_args: dict = {}
_engine_kwargs: dict = {"pool_pre_ping": True}
if _url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
    # StaticPool keeps one shared connection so init + request scopes see the same DB.
    _engine_kwargs["poolclass"] = StaticPool
else:
    # Fail FAST (5s) with a clear error if the database is unreachable — never hang.
    _connect_args = {"connect_timeout": 5}

engine = create_engine(_url, connect_args=_connect_args, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session; always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """Injectable session factory (overridable in tests) for background tasks,
    which must not reuse request-scoped sessions."""
    return SessionLocal

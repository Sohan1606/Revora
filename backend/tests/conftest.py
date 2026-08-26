"""Shared fixtures: isolated in-memory SQLite + app client on the same engine."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401 — register tables
from app.infrastructure.database import Base, get_db
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolate_models_dir(tmp_path, monkeypatch):
    """Tests must never read real model artifacts from the repo (hermetic suites).
    Individual tests may point MODELS_DIR at their own artifacts explicitly.
    Teardown also clears the bundle cache so XGBoost boosters loaded during tests
    are freed DURING the run — not at interpreter shutdown (which is what emits
    harmless destructor warnings on Windows)."""
    from app.intelligence.recovery_score import ml_provider
    monkeypatch.setattr(ml_provider, "MODELS_DIR", tmp_path / "no_models")
    ml_provider._cache.clear()
    yield
    ml_provider._cache.clear()
    import gc

    gc.collect()


@pytest.fixture(autouse=True)
def _rate_limits_off(monkeypatch):
    """Rate limiting is production hardening; disabling it keeps test suites
    hermetic (login-heavy matrix tests). Dedicated limiter tests re-enable it."""
    from app.core.config import get_settings
    from app.core import ratelimit
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_ENABLED", False, raising=False)
    ratelimit._windows.clear()
    yield
    ratelimit._windows.clear()


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = S()
    yield session
    session.close()


@pytest.fixture()
def client(engine, db):
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        session = S()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c

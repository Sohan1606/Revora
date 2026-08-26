"""Regression (audit finding): two concurrent RUNNING experiments crashed every
decision with MultipleResultsFound. Invariants now enforced:
1) start refuses while another experiment is running (409 at the API)
2) get_running_experiment is robust to legacy duplicate running rows"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.infrastructure.database import Base
from app.infrastructure.models import Experiment, Merchant


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    s.add(Merchant(id=uuid.UUID(int=1), name="M"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def _experiment(db, name, status="draft", minutes_ago=0):
    e = Experiment(merchant_id=uuid.UUID(int=1), name=name,
                   strategy_treatment="revora_nba", strategy_control="naive_dunning",
                   status=status,
                   started_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
                   if status == "running" else None)
    db.add(e)
    db.commit()
    return e


def test_cannot_start_second_running_experiment(db):
    from app.domain.experiments.engine import start_experiment

    first = _experiment(db, "first", status="running", minutes_ago=5)
    second = _experiment(db, "second")
    with pytest.raises(ValueError, match="already running"):
        start_experiment(db, second)
    # restarting the same one is idempotent-legal
    assert start_experiment(db, first).status == "running"


def test_get_running_experiment_survives_legacy_duplicates(db):
    from app.domain.experiments.engine import get_running_experiment

    _experiment(db, "older", status="running", minutes_ago=10)
    _experiment(db, "newer", status="running", minutes_ago=1)

    latest = get_running_experiment(db, uuid.UUID(int=1))  # must not raise
    assert latest is not None and latest.name == "newer"

"""Schema integrity — table presence and money-as-paise invariants."""
from __future__ import annotations

from sqlalchemy import inspect

from app.infrastructure.database import Base
import app.infrastructure.models  # noqa: F401


EXPECTED_TABLES = {
    "merchants", "users", "merchant_integrations",
    "customers", "orders", "payments", "webhook_events",
    "revenue_risk_events", "failure_classifications", "recovery_cases",
    "candidate_actions", "decisions",
    "policy_versions", "policy_rules", "executions", "outcomes",
    "messages", "audit_events",
    "experiments", "experiment_assignments",
}


def test_all_mvp_tables_present() -> None:
    tables = set(Base.metadata.tables.keys())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"


def test_money_columns_are_integer_paise() -> None:
    for table, col in [
        ("orders", "amount_paise"),
        ("payments", "amount_paise"),
        ("revenue_risk_events", "amount_paise"),
        ("recovery_cases", "amount_paise"),
        ("candidate_actions", "expected_value_paise"),
        ("outcomes", "amount_recovered_paise"),
    ]:
        python_type = Base.metadata.tables[table].columns[col].type.python_type
        assert python_type is int, f"{table}.{col} must be integer paise, got {python_type}"


def test_webhook_event_id_unique_for_idempotency() -> None:
    col = Base.metadata.tables["webhook_events"].columns["event_id"]
    assert col.unique, "webhook event_id must be unique (idempotent processing)"


def test_audit_events_append_only_no_updated_at() -> None:
    cols = set(Base.metadata.tables["audit_events"].columns.keys())
    assert "updated_at" not in cols, "audit trail is append-only"
    assert "created_at" in cols

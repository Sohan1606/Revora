"""ORM models — single source of truth for the schema.

Conventions:
- Money = integer paise (BigInteger), never floats.
- Enums = non-native VARCHAR + CHECK (portable across Postgres/SQLite).
- audit_events is append-only (no updated_at).
- Synthetic data rows must carry is_synthetic=True (evaluation corpus only).
"""
from app.infrastructure.models.merchant import Merchant, User, MerchantIntegration
from app.infrastructure.models.payments import Customer, Order, Payment, WebhookEvent
from app.infrastructure.models.recovery import (
    RevenueRiskEvent,
    FailureClassification,
    RecoveryCase,
    CandidateAction,
    Decision,
)
from app.infrastructure.models.governance import (
    PolicyVersion,
    PolicyRule,
    Execution,
    Outcome,
    Message,
    AuditEvent,
)
from app.infrastructure.models.experiments import Experiment, ExperimentAssignment

__all__ = [
    "Merchant", "User", "MerchantIntegration",
    "Customer", "Order", "Payment", "WebhookEvent",
    "RevenueRiskEvent", "FailureClassification", "RecoveryCase",
    "CandidateAction", "Decision",
    "PolicyVersion", "PolicyRule", "Execution", "Outcome", "Message", "AuditEvent",
    "Experiment", "ExperimentAssignment",
]

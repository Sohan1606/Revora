# REVORA — Architecture (APPROVED — do not redesign)

## System diagram (locked)

```
                 ┌─────────────────────┐
                 │       REACT         │
                 │     EXPERIENCE      │
                 └──────────┬──────────┘
                            │ HTTPS
                            ▼
                 ┌─────────────────────┐
                 │      FASTAPI        │
                 │   PRODUCT API       │
                 └──────────┬──────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        POSTGRES        ML ENGINE      RAZORPAY
        SOURCE OF       PREDICT         PAYMENT
         TRUTH          RANK            EVENTS
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                   ┌─────────────────┐
                   │ POLICY ENGINE   │
                   │ HARD CONSTRAINTS│
                   └────────┬────────┘
                            ▼
                   ┌─────────────────┐
                   │ ACTION EXECUTOR │
                   └────────┬────────┘
                            ▼
                        OUTCOME
                            │
                            ▼
                   ┌─────────────────┐
                   │ EXPERIMENT /    │
                   │ ATTRIBUTION     │
                   └────────┬────────┘
                            ▼
                   INCREMENTAL MONEY
```

## Stack decisions (from approved tech-stack review)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React + TypeScript + Vite + Tailwind | Phase 10 |
| Backend | Python 3.13 + FastAPI + Pydantic | Modular monolith |
| Database | PostgreSQL (Supabase in prod; docker/local dev) | Source of truth |
| Auth | Supabase Auth + JWT + RBAC | Phase 5–6 |
| ML | scikit-learn baseline → XGBoost, calibrated | Phase 8 |
| Optimization | OR-Tools (only when budget optimizer is built) | Phase: optional P2 |
| LLM | Optional, isolated adapter; never executes money actions | Phase: optional P2 |
| Payments | Razorpay Test Mode REST + webhooks (signature-verified, idempotent) | Phase 7/11 |
| Messaging | Resend (primary), Twilio (optional) behind SIMULATION/REAL flag | Phase 11 |
| Deploy | Vercel (frontend) + Render (backend) + Supabase (DB) | Phase 14 |

## Module boundaries (modular monolith)

- `app/api` — HTTP layer only: request validation, auth deps, responses. No business logic.
- `app/domain/recovery` — risk-event detection, case lifecycle, next-best-action orchestration.
- `app/domain/payments` — payment/order state ingestion + normalization.
- `app/domain/policies` — deterministic policy engine; hard rules override any model output.
- `app/domain/experiments` — treatment/control assignment, incremental-recovery computation.
- `app/intelligence/*` — pure prediction/estimation code. Takes features in, returns scores out. **Never** talks to the DB or external APIs directly.
- `app/integrations/*` — outbound adapters (Razorpay, Resend, Twilio). Secrets only from env.
- `app/infrastructure` — engine, session, ORM models, init scripts.

## Hard invariants

1. Money amounts are stored as **integer paise** (Razorpay convention). Never floats.
2. No action executes without passing the deterministic policy gate.
3. Every decision/action/outcome writes an append-only audit event.
4. Webhook processing is idempotent (event-id dedupe) and signature-verified.
5. ML model version + inputs are persisted with every decision.
6. `NO_ACTION` / `WAIT` are always valid candidate actions.
7. Synthetic/simulated data is allowed **only** for the evaluation corpus and is labeled as such in the DB and UI — never presented as live merchant data.

## Razorpay integration contract (Phase 11)

- Verify `X-Razorpay-Signature` = HMAC-SHA256(raw body, webhook secret), timing-safe compare.
- Dedupe on Razorpay event id (`webhook_events.event_id` unique).
- Acknowledge fast (<5s): persist raw event → return 200 → process asynchronously.
- Test Mode only. Keys via env: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`.

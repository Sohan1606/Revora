# REVORA — Judge Defense: Ranked Weaknesses, Honest Answers, Interview Prep

Written as the brutal-judge review requested in the brief: attack points ranked,
the honest answer for each, and what was already done about it. Everything here is
defensible from the repo — no claim requires trusting us.

---

## Ranked weaknesses (with the honest answer for each)

### W1 — "Your evaluation data is synthetic." (Highest-probability attack)
**Answer:** Yes — declared, by design, and demanded by the brief ("synthetic transaction
histories"). The DGP has *known ground truth* (cause×action effectiveness, payday boost,
VIP responsiveness, retry fatigue), so held-out metrics measure whether the pipeline
**recovers known structure**, not a claim about real-world data. Every synthetic row is
`is_synthetic=true` and `source="simulator"`; live and synthetic metrics are computed
separately everywhere (API + UI). Replacing the corpus with real Razorpay test-mode
webhook events requires zero code changes — that's the same ingestion path.
**Evidence:** `docs/ML.md`, `domain/recovery/corpus.py` (DGP table), control-center split.

### W2 — "XGBoost ≈ logistic regression (AUC 0.6907 vs baseline 0.6886). Where's the AI value?"
**Answer:** We report it honestly, and it's expected: the DGP is near-additive. The value
of the system is the **decision layer** — expected-value selection, policy gates, bounded
execution, incremental measurement — which no baseline logistic model gives you. We ship
the baseline comparison *because* we measured instead of assuming. As real data grows
(with interactions we didn't synthesize), the calibrated XGBootstrap has capacity the
linear model lacks.
**Evidence:** `models/evaluation_report.json`, `docs/ML.md`.

### W3 — "Incremental recovery is measured on simulated outcomes."
**Answer:** The *machinery* is real: deterministic hash-based 50/50 assignment, immutable
assignment rows, control-arm exclusion from treatment stats, results from authoritative
outcome rows only, honest arm sizes. The outcome *feed* is the simulator until Razorpay
webhooks replace it — the same observe path, different source label. We never present
simulated incremental revenue as live money.
**Evidence:** `domain/experiments/engine.py`, `source="simulator"` labels.

### W4 — "Your retry doesn't re-charge the card."
**Answer:** Correct and deliberate — Razorpay (like all PSPs) forbids server-side
charges without customer authorization. Our retry = **real test-mode Order creation**
(the customer-completion path), which is the compliant real-world retry mechanism. The
client has no capture/refund/transfer methods at all (test-enforced), and live keys are
refused by code.
**Evidence:** `integrations/razorpay/client.py`, `test_client_no_money_movement_surface`.

### W5 — "Rate limiter is in-process."
**Answer:** Stated limitation, right for single-instance deployment (which is what a
hackathon runs). Multi-instance needs Redis; the interface is a drop-in
(`core/ratelimit.py` isolates the policy from the store).
**Evidence:** `docs/SECURITY_AUDIT.md` F1 scope note.

### W6 — "No async workers; webhook processing is FastAPI BackgroundTasks."
**Answer:** Volume-appropriate: processing is two inserts + light classification
(milliseconds). Fast-ack + idempotency + persisted events with failure states are the
parts that matter at this scale; a queue (Redis/RQ) is the horizontal next step and the
layer boundary (`processor.py`) already isolates it.

### W7 — "SQLite locally / small scale overall."
**Answer:** Local dev convenience only; production runs Supabase Postgres (same ORM,
no code change). All list endpoints paginate; queries are indexed; the batch metrics
are aggregate SQL.

### W8 — "Human approval only covers offers above a threshold."
**Answer:** The gate framework (`require_approval_above_paise` per action, policy-versioned)
covers any action — thresholds are merchant policy, not hardcoded. Rejected actions are
never re-proposed (human override is respected; tested).

---

## "Why this technology?" — quick answers

- **FastAPI + Pydantic:** typed contracts, automatic OpenAPI (judges can read the API),
  async webhooks; Python keeps the ML stack native.
- **PostgreSQL/Supabase:** relational money data, ACID, row-level growth path; Supabase
  adds managed auth so JWT verification — not password storage — is our attack surface.
- **React+Vite+TS+Tailwind:** fast typed console; hash routing = zero-config static
  hosting; Tailwind keeps the operator UI dense and consistent.
- **XGBoost + calibration:** tabular probability estimation with calibrated outputs;
  logistic baseline kept as the honest control.
- **Rules+ML hybrid classifier:** hard decline codes are deterministic (rules); ambiguous
  text needs the model; unknown stays `unknown` at 0.20 — we never guess.
- **No LangChain/agents框架:** the decision loop is a deterministic state machine with
  ML inside it — that's a feature (auditable, testable), not a missing feature.

## What happens on failure? (memorize the one-liners)

- Razorpay down → execution fails with `razorpay_api_error`, case returns to `analyzed`
  (retryable), audit records it. Unconfigured → policy blocks retry candidates visibly;
  executor fails closed with the exact remediation message.
- Bad webhook → signature rejected 400; duplicate → 200 duplicate (idempotent);
  processing error → event row marked `failed` with the error, never dropped.
- Model artifact unusable → one warning, poison-cache, empirical fallback; decisions
  continue (tested).
- Unhandled exception → generic 500 + request-id; traceback only in structured logs.
- DB unreachable → health 503, connect timeout 5s (no hangs).

## Demo flow for judges (5 minutes)

1. Landing (30s): thesis + labeled corpus stats — "no invented numbers".
2. Demo Lab (90s): run `insufficient_funds` → walk the trace: diagnosis → candidates
   with P/EV/cost → policy verdicts incl. a **blocked** action → execution → outcome.
3. Case detail (60s): the same case — full candidate table, audit trail, state machine.
4. Experiments (60s): create + start, run a few scenarios, show treatment vs control +
   incremental recovery definition.
5. Control center (30s): live vs synthetic separation; estimate-basis mix
   (model/empirical/prior transparency).
6. Evidence (30s): `docs/ML.md` held-out table + `docs/TEST_REPORT.md` +
   `docs/SECURITY_AUDIT.md` — measured, audited, bounded.

## Mock interview — top 12 questions to expect

1. Walk me through what happens 10 seconds after a real payment fails.
2. Why is `no_action` ever the best decision? (EV math on the whiteboard.)
3. How do you know your recoveries weren't going to happen anyway? (holdout design.)
4. What's your P(recovery) model's weakest feature? (honesty about the corpus.)
5. A merchant sets max_contacts=0 — what does your system do?
6. How would you move from simulator outcomes to real ones, with zero downtime?
7. Where exactly can your policy engine override the model? Show the code path.
8. What breaks at 10× traffic first? (rate limiter → Redis, background queue, Postgres.)
9. Why should Razorpay not just build this? (positioning: decision layer + honest
   attribution across PSPs; but be graceful — they might.)
10. What's the single metric you'd track in production? (incremental recovered ₹ per
    ₹100 at-risk, per cause.)
11. Show me a decision you disagree with in your own logs — find one live.
12. What did you cut, and why? (LLM explanations, budget optimizer, degradation module
    — scoped out deliberately for loop integrity over feature count.)

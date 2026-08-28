> 🎯 **Razorpay AI Buildathon · Track: AI Revenue Recovery**
>
> **[▶ LIVE DEMO](https://revora-eight-beta.vercel.app)** ·
> [![CI](https://github.com/Sohan1606/Revora/actions/workflows/ci.yml/badge.svg)](https://github.com/Sohan1606/Revora/actions/workflows/ci.yml) ·
> 129 backend tests · 18/18 browser E2E · npm audit 0 · pip_audit clean
>
> **Sign in to the live demo:** create your own account (email + password) — your first
> login provisions your merchant workspace with the owner role. Then open **Demo Lab →
> Run scenario** to watch a full recovery decision with all evidence.

# REVORA — Revenue Recovery Decision Intelligence

**Razorpay AI Buildathon · Track 03: AI Revenue Recovery**

> **Find revenue at risk. Determine the best safe action. Execute it. Prove the incremental money recovered.**

REVORA is not a chatbot, an email generator, a dunning dashboard, or a retry wrapper.
It is a **decision engine**: it detects revenue at risk, diagnoses why it is at risk,
evaluates every permissible recovery action by expected value (including **doing
nothing**), enforces deterministic safety policy, executes bounded actions, and
measures **incremental** recovery against a naive-dunning control group.

---

## 1. The problem

Merchants lose revenue across payment failures, and their tools answer *"what
happened?"* — not *"what should happen next, is it worth it, and did it work?"*
Reminders and blind retries ignore failure cause, intervention cost, customer
fatigue, and whether the customer would have paid anyway.

## 2. What REVORA does (the closed loop)

```
Detect → Diagnose → Decide (EV over candidates) → Policy gate → Execute (bounded)
→ Observe outcome → Attribute (treatment vs control) → Audit (append-only)
```

Every decision persists **all candidates evaluated** — probabilities, expected
values, costs, and policy verdicts (including blocked ones) — as inspectable
evidence. `NO_ACTION` and `WAIT` are always valid candidates; `no_action`'s EV is
0 by definition because organic recovery is never claimed as system-attributed.

## 3. Why it is different

| Most "AI recovery" tools | REVORA |
|---|---|
| Send more reminders | Expected-value action selection with explicit do-nothing |
| Gross "recovered" totals | Treatment-vs-control **incremental** recovery |
| Model decides | Model **recommends**; deterministic policy constrains and executes |
| Black box | Every decision auditable with basis labels (`model:` / `empirical(n,k)` / `cold_start_prior`) |
| Fake-agnostic demo theater | Fail-closed integrations, honest metrics, simulated data always labeled |

## 4. Architecture (approved, locked)

React+TS+Vite+Tailwind → FastAPI (modular monolith) → PostgreSQL/Supabase, with
ML engine (calibrated XGBoost + logistic baseline), Razorpay Test Mode adapter
(signature-verified, idempotent webhooks), deterministic policy engine, bounded
action executor, outcome attribution, append-only audit. Full detail:
**`docs/ARCHITECTURE.md`**.

## 5. Real vs simulated — stated plainly

| Component | Status |
|---|---|
| Decision engine, policy gate, state machine, executor, experiments, audit | **Real** (116 backend tests) |
| Razorpay integration (REST client, retry = real test-mode Order creation, webhooks) | **Real code, TEST MODE only;** runs when `rzp_test_*` keys are configured; otherwise clearly reported "not configured" and everything else keeps working |
| Supabase auth + Postgres | **Real** in production; local dev uses signed-JWT dev login + SQLite |
| Evaluation corpus + Demo Lab world-responses | **Synthetic by design** — every row `is_synthetic=true`, every outcome `source="simulator"`, never blended with live metrics |
| Messaging (email/SMS) | **Simulated by default** — every message row carries `provider="simulated"` |

## 6. ML methodology (honest)

Synthetic corpus with **known ground truth** (cause×action effectiveness, payday
timing, VIP responsiveness, retry fatigue) → logistic baseline vs **calibrated
XGBoost** → **held-out 20%** evaluation (never training data):

| metric (n_test=1200, seed 42) | logistic baseline | XGBoost calibrated (served) |
|---|---|---|
| AUC | 0.6886 | **0.6907** |
| Brier | 0.2124 | **0.2120** |

The model is at **parity** with the baseline on this near-linear DGP — reported
as-is. The system's value is the decision layer, not model supremacy. Provider
chain: trained model (only for actions it saw, feature-version matched) →
empirical base rates → labeled cold-start prior. Full detail: **`docs/ML.md`**,
evidence: `backend/models/evaluation_report.json`.

## 7. Incremental recovery methodology

Deterministic hash-based 50/50 assignment (immutable rows), treatment = REVORA
policy vs control = naive immediate-dunning, results from authoritative outcome
rows only, control arm excluded from the treatment model's statistics, arm sizes
reported honestly (no survivorship hiding). `incremental = treatment recovered −
control recovered`.

## 8. Security posture (actual, not absolute)

JWT auth (Supabase or fail-closed local dev), server-side RBAC (4 roles),
per-merchant tenant isolation on every query, signature-verified + idempotent
webhooks, rate limiting (429/Retry-After), 1 MiB webhook cap, security headers,
generic 500s with request IDs, money as integer paise, secrets env-only and
never logged. Accepted limitations stated in **`docs/SECURITY_AUDIT.md`**
(e.g., in-process rate limiter; single-instance deploy).

## 9. Testing (verified counts)

- **Backend: 116/116 pytest** (auth/RBAC matrices, decision engine, policy,
  executor idempotency, experiments, ML bounds + resilience, webhooks, security).
- **Browser E2E: 18/18** (Playwright, desktop 1440×900 + mobile 390×844,
  production build): landing → login → control center → cases → case detail with
  candidate/policy evidence → policies → experiment lifecycle + real computed
  results → demo lab → zero horizontal overflow → no severe console errors.
- Dependency audits: `pip_audit` clean; `npm audit` **0 vulnerabilities**.
- Evidence: **`docs/TEST_REPORT.md`**, screenshots in `docs/screenshots/`.

## 10. Run locally (Windows/macOS/Linux)

Easiest: double-click **`start-backend.bat`** then **`start-frontend.bat`**, open
http://localhost:5173 → one-click demo sign-in. Or:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.infrastructure.scripts.dev_up          # .env secret + schema + seed + API
# second terminal:
cd frontend && npm install && npm run dev            # http://localhost:5173
# optional labeled demo data + native model (reproducible, seed=42):
#   python -m app.infrastructure.scripts.generate_corpus --n 3000 --seed 42
#   python -m app.infrastructure.scripts.train_model --seed 42
# run the suite:  pytest
```

No Razorpay or Supabase credentials are required for local development or the
core demo. Razorpay Test Mode keys (`rzp_test_*` only — live keys refused by code)
can be added later in `backend/.env`.

## 11. Deployment

Render (backend) + Vercel (frontend) + Supabase (Postgres+Auth), all free tier —
configs committed (`render.yaml`, `vercel.json`, CI in `.github/workflows/`).
Click-by-click: **`docs/DEPLOY.md`**.

## 12. Judge demo flow (deterministic, no hardcoded results)

1. Landing (`/`) → thesis + labeled corpus stats
2. Console → Control Center → revenue at risk (live/synthetic separated)
3. Demo Lab → run a scenario → diagnosis, candidates, EV, policy verdicts
4. Recovery Cases → open the case → candidate table + audit trail
5. Experiments → create/start → run scenarios → real treatment/control/incremental
6. Architecture & evidence: `docs/` (ML, tests, security, judge-defense)

For likely judge attacks + honest answers: **`docs/JUDGE_DEFENSE.md`**.

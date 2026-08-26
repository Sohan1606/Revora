# REVORA — Test Report (verified 2026-08-26, stabilization audit)

**Exact counts from this audit's real runs:**
- **Backend: 116/116 passed** (`pytest`, ~43s)
- **Browser E2E: 18/18 passed** (Playwright + headless Chromium, production build
  via `vite preview`, desktop 1440×900 + mobile 390×844)
- **Dependency audits:** `pip_audit` clean · `npm audit` 0 vulnerabilities
- **Frontend build:** `tsc -b && vite build` clean; client-bundle secret scan clean

**Reproduce:** `cd backend && pytest` · `cd frontend && npm run build && npx vite preview & node e2e/smoke.mjs`

> History note: earlier iterations of this file said 104/104 and the security
> audit said 110/110 — both were true at their respective times; the suite has
> since grown (security hardening, ML resilience, serving-gate regression,
> experiment invariants). This file now reflects the single verified count:
> **116**. Documentation is updated whenever the count changes.

---

## Defects found & fixed during this stabilization audit

| # | Defect | Root cause | Fix + regression test |
|---|---|---|---|
| 1 | **Shipped model artifact never served model estimates** (every decision fell back to cold_start) | `train_model` didn't record `metadata["actions"]`, so the provider's serving gate rejected all actions | Actions recorded at train time; artifact retrained (same corpus/seed → metrics recomputed for real); `test_serving_gate_regression.py` |
| 2 | **500 on every decision when two experiments were running** | `get_running_experiment` used `scalar_one_or_none()`; starting a 2nd experiment wasn't prevented | One-running-experiment enforced (409) + query made robust (latest wins); `test_experiment_invariants.py` |
| 3 | Duplicate Supabase env names in `backend/.env.example` | copy/paste drift | Deduplicated; every placeholder empty |
| 4 | Empty bool env lines (`AUTO_CREATE_TABLES=`) crashed Settings | pydantic can't parse `""` as bool | Tolerant validator (empty → False) |
| 5 | E2E flakiness (policies/case-detail checks) | instant `isVisible()` raced async fetches | `waitFor` semantics + unique per-run experiment names |
| 6 | Stale servers on reused ports misled verification | previous-turn processes held 8000 | audit rerun on freshly started servers only |

## XGBoost shutdown warnings (documented, not hidden)

Some Windows machines emit XGBoost Booster destructor warnings **after** the
pytest summary. Investigation result: the warnings come from XGBoost's C-library
lifecycle during Python interpreter finalization (boosters still referenced at
exit); on Linux (CI) they do not occur and could not be reproduced even when
holding a booster to interpreter exit. **Mitigation applied:** the test suite now
clears the model-bundle cache and force-collects in an autouse fixture teardown,
freeing boosters deterministically during the run instead of at shutdown. No
test was weakened; no genuine error is silenced; model behavior unchanged. If a
warning still appears on a specific Windows build, it is a known harmless
XGBoost shutdown artifact (post-summary, zero test impact) — this is stated
rather than hidden.

## Coverage by area (tests are the source of truth, not this table)

| Area | Evidence (test files) |
|---|---|
| Authentication (dev + supabase JWT paths, no enumeration, fail-closed) | `test_auth_rbac.py` |
| RBAC matrix — every role × every endpoint; tenant isolation | `test_phase12_presubmission.py`, `test_phase9_apis.py` |
| Ingestion / detection / classification / state machine | `test_phase4_core.py` |
| NBA engine, EV logic, NO_ACTION/WAIT, policy gate, executor idempotency, approvals, outcomes | `test_phase7_logic.py` |
| Corpus integrity, features, training, held-out metrics, provider chain, artifact resilience | `test_phase8_ml.py`, `test_ml_resilience.py`, `test_serving_gate_regression.py` |
| APIs, webhooks (signature/idempotency/captured), simulator, analytics, experiments | `test_phase9_apis.py` |
| Razorpay client contract, live-key refusal, fail-closed, retry paths | `test_phase11_integrations.py` |
| Invalid inputs, empty/error states, ML bounds, determinism | `test_phase12_presubmission.py` |
| Rate limits, size cap, headers, generic 500s, secret hygiene | `test_phase13_security.py` |
| Experiment invariants (one-running, legacy duplicates) | `test_experiment_invariants.py` |

## Browser E2E (18 checks, production build)

Landing (hero/CTA/corpus-label, loop section) · dev login redirect · control
center · **corpus visibility under dev merchant** · demo-lab scenario with
candidate table · case detail (audit trail + **candidate EV table + policy
verdicts**) · policies page (versioned policy) · experiments (empty state,
create→start→running, **real computed treatment/control/incremental from the
engine** — recorded values are whatever the engine computed, including negative
incremental on tiny samples) · mobile 0px horizontal overflow (landing+console) ·
no severe console errors. Screenshots: `docs/screenshots/`.

## Known gaps / accepted limitations (unchanged, stated)

1. Chromium-only browser automation (Safari/Firefox manual).
2. Supabase end-to-end login tested with synthetic JWT secrets + live REST
   liveness only (no dashboard test users created).
3. Razorpay real API intentionally not called (no credentials by design);
   contract-verified via injected transport; `verify` endpoint performs the real
   check once keys exist.
4. Sandbox preview blocks external media — landing video/fonts fall back
   gracefully (verified); full fidelity in a networked browser.
5. In-process rate limiter (single-instance); Redis noted for multi-instance.

**Critical/high issues open: 0.**

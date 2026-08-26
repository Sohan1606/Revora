# REVORA — Build Roadmap & Pipeline

Rule: each phase is implemented → run → tested → fixed → reported, with exact next steps.
Nothing is marked done until it runs. No fake functionality, ever.

## Build phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Repository setup | ✅ Done | git repo, .gitignore, compose, env templates |
| 2 | Project structure | ✅ Done | modular monolith skeleton, health API, tests green |
| 3 | Database | ✅ Done | 19-table ORM schema, init script, health DB check |
| 4 | Backend core (risk detection, cases, diagnosis stubs wired to real DB) | ✅ Done | ingestion (idempotent), rules-v1 classifier, state machine, audit |
| 5 | Authentication (JWT: Supabase mode + fail-closed local dev mode) | ✅ Done | real HS256 verify; dev issuer exists only in ENV=local; verified suite |
| 6 | Authorization + RBAC (owner/admin/operator/viewer) | ✅ Done | role hierarchy + tenant isolation verified over live HTTP |
| 7 | Core business logic (next-best-action engine, policy gate, orchestrator state machine, audit) | ✅ Done | EV-based NBA w/ full candidate evidence, deterministic policy gate, bounded idempotent executor, approvals, outcomes; verified suite |
| 8 | AI/ML (recovery-probability model, root-cause classifier, calibration, evaluation on held-out synthetic corpus) | ✅ Done | corpus via real pipeline (labeled), logreg baseline + calibrated XGB, honest held-out metrics, provider chain; verified suite — see docs/ML.md |
| 9 | APIs (all product endpoints incl. experiments/attribution) | ✅ Done | cases/decisions/approvals, analytics, experiments (incremental recovery), policies, Razorpay webhook (signature+idempotent+fast-ack), simulator; verified suite |
| 10 | Frontend (React+Vite+TS+Tailwind: control center, case detail, decision panel, experiments) | ✅ Done | operator console, full-stack smoke verified; landing page direction still queued (pipeline P1) |
| 11 | Integrations (Razorpay test-mode REST + webhooks; Resend behind SIMULATED/REAL flag) | ✅ Done | Razorpay client fail-closed + live-refused; retry = real test-mode order creation; Supabase JIT provisioning; verified suite. Resend remains SIMULATED by default (declared) |
| 12 | Testing (auth, roles, features, APIs, DB, error/empty/loading states, responsiveness) | ✅ Done | backend suite + Playwright browser E2E (desktop+mobile) — docs/TEST_REPORT.md |
| 13 | Security hardening (rate limiting, headers, secret mgmt, input/output validation, logging) | ✅ Done | 5 findings fixed & tested, deps clean (pip_audit + npm audit = 0) — docs/SECURITY_AUDIT.md |
| 14 | Deployment (Vercel + Render + Supabase, env config, CORS, health checks, e2e verify) | ⬜ | |

## Post-build pipeline (executed against the REAL repo — not before)

These were requested up front. They depend on a completed build and will be run against
the actual codebase, with real findings only. No fabricated reports.

| # | Task | Status | Dependency |
|---|---|---|---|
| P1 | Landing page — merged design delivered: MotionSites fixed-stage identity (choreographed entrance, red #c81b1c, SG/JB type, graded video plate) + cinematic scroll + three.js 3D flow-field hero; real corpus stats only, never invented | ✅ Done | live at `/` |
| P2 | Fake-functionality audit (hardcoded values, fake stats, mock APIs, static charts, dead buttons) | ⬜ | Phases 4–12 |
| P3 | Security audit (authn/authz/RBAC/SQLi/XSS/CSRF/SSRF/IDOR/rate limits/secrets/uploads/exposure/deps/validation/logging/errors) with severity + fix + test per finding | ⬜ | Phase 13 |
| P4 | Pre-submission test report (15 areas listed in brief) | ⬜ | Phase 12 |
| P5 | Production-style deployment + end-to-end verification | ⬜ | Phase 14 |
| P6 | Brutal-judge weakness review + mock interview | ⬜ | P2–P5 |

## Known intentional simulations (must stay clearly labeled)

- **Synthetic evaluation corpus** (payment/recovery history) — required by the Buildathon brief
  ("synthetic transaction histories"); stored with `is_synthetic = true` and surfaced in the UI.
- **SIMULATED messaging mode** — the approved stack decision: demo default is simulated delivery,
  real Resend/Twilio wired behind env flag. Never presented as real delivery.
- **Razorpay Test Mode** — mandated by the track. All "payments" are test-mode artifacts.

Anything NOT on this list must be real.

| 15 | Stabilization audit (2026-08-26): serving-gate fix + artifact retrained, experiment invariants, env parsing, docs synchronized to verified counts (116 backend / 18 browser) | ✅ Done | see docs/TEST_REPORT.md |

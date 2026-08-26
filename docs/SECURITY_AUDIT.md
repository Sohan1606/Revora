# REVORA — Security Audit (Phase 13)

**Date:** 2026-08-26 · **Posture after fixes:** all High/Medium findings fixed & tested;
accepted risks stated explicitly. **Verification:** 116/116 backend tests (incl. dedicated
hardening tests), `pip_audit` clean, `npm audit` clean (after upgrades), 18/18 browser checks.
Re-verified during the 2026-08-26 stabilization audit; new findings from that
pass are recorded in docs/TEST_REPORT.md (experiment-invariant fix, env parsing,
serving-gate regression).

## Findings register (severity → location → scenario → fix → test)

### F1 — HIGH · No rate limiting (fixed)
- **Location:** entire API; especially `POST /api/auth/dev/token`, `POST /api/webhooks/razorpay`
- **Scenario:** brute-force token issuance (account enumeration via timing), webhook
  signature brute force, general API flooding.
- **Fix:** sliding-window limiter (`app/core/ratelimit.py`): 10/min dev-token, 120/min
  webhook, 300/min global per IP; `429` + `Retry-After`.
- **Test:** `test_dev_token_rate_limited` (429 observed; Retry-After header present).
- **Accepted scope note:** limiter state is in-process — correct for single-instance
  deployment; multi-instance needs Redis (documented; not hackathon scope).

### F2 — MEDIUM · Unbounded webhook body size (fixed)
- **Location:** `POST /api/webhooks/razorpay`
- **Scenario:** multi-MB bodies burn CPU on signature hashing (DoS vector).
- **Fix:** 1 MiB cap via Content-Length → `413`.
- **Test:** `test_webhook_payload_size_capped`.

### F3 — MEDIUM · Missing security headers (fixed)
- **Location:** all responses.
- **Scenario:** clickjacking/frame embedding, MIME sniffing, referrer leakage.
- **Fix:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store` (financial data).
- **Test:** `test_security_headers_present`.

### F4 — MEDIUM · Unhandled-exception detail leakage (fixed)
- **Location:** any unexpected error path.
- **Scenario:** stack traces / internal details in 500 responses aid reconnaissance.
- **Fix:** global handler → generic `{"detail":"internal_server_error","request_id":…}`;
  full traceback only in structured logs (keyed by request id).
- **Test:** `test_unhandled_errors_are_generic` (forced RuntimeError; secret string absent
  from response; request_id present).

### F5 — MEDIUM · npm vulnerabilities: vite≤6.4.2/esbuild, react-router 6 (fixed)
- **Scenario:** esbuild dev-server request reading (dev only); open redirect via
  backslash in router Links (GHSA-wrjc-x8rr-h8h6).
- **Fix:** upgraded `vite 6.4.3` + `react-router-dom 7.18.2`; build + full browser E2E
  re-verified (13/13). **`npm audit`: 0 vulnerabilities.**
- **Test:** E2E suite re-run post-upgrade; `npm audit` exit 0.

### F6 — LOW · Request tracing absent (fixed)
- **Fix:** `X-Request-ID` per request (echoed on responses, present in logs) +
  `X-Response-Time-Ms` for latency visibility.

## Areas verified with no finding

| Area | Verification |
|---|---|
| **Authentication** | HS256 signatures timing-safe (PyJWT); exp/iss/aud enforced; claims never authenticate alone (row must exist+active); dev issuer 404 outside local/supabase; identical 401s (no enumeration); fail-closed 503 when unconfigured. Tests: `test_auth_rbac.py` (10). |
| **Authorization / RBAC** | Server-side role hierarchy on all 22 endpoints (full matrix test); UI hiding is cosmetic only. |
| **SQL injection** | 100% SQLAlchemy ORM/query builders — parameterized everywhere; the only raw SQL is the literal `SELECT 1` health probe. No string interpolation into SQL anywhere (grep-verified). |
| **XSS** | React default-escapes; **no `dangerouslySetInnerHTML`** anywhere in src (grep-verified); API returns JSON with nosniff. |
| **CSRF** | Auth is Bearer-header JWT (no cookies) → classic CSRF inapplicable; CORS restricted to `CORS_ORIGINS` allowlist. |
| **SSRF** | Outbound HTTP goes only to the fixed Razorpay base URL (env-overridable for tests, not user-influenced); no user-supplied URLs are fetched anywhere. |
| **IDOR / tenant isolation** | Every data query scoped by `current_user.merchant_id`; cross-tenant reads → 404 (tested for cases, decisions, experiments, policies, admin). |
| **Secret management** | Env-only (`pydantic-settings`); `.env` git-ignored (enforced in `.gitignore`); no secrets in code (audit), logs, health, or OpenAPI (test-enforced); Razorpay client never logs auth material; key prefix exposure limited to `rzp_test_*` first 9 chars. |
| **File uploads** | None exist — nothing to exploit (stated). |
| **Data exposure** | Public endpoints expose only labeled synthetic aggregates; audit payloads contain business events only; webhook events store payloads locally, never echoed unauthenticated. |
| **Dependency vulnerabilities** | `pip_audit`: 0 findings on requirements.txt. `npm audit`: 0 after upgrades. |
| **Input validation** | Pydantic on every body (types, patterns, bounds); pagination clamped (1–200); UUID path params validated → 404 not 500; enum checks on roles/outcomes/scenarios. |
| **Output validation** | Money always integer paise (schema test); ML estimates bounded and basis-labeled (test); policy definitions validated before persistence. |
| **Logging** | Structured JSON; exceptions logged server-side only; no tokens/secrets in log calls (grep-verified); access logs exclude `/api/health` noise. |
| **Error handling** | 404/409/413/422/429/503 semantics implemented and tested; webhook processing failures recorded on the event row (never dropped). |

## Standing recommendations (deployment phase)
1. HTTPS everywhere (enforced by hosts Render/Vercel) + HSTS at the proxy.
2. Rotate the Supabase service-role key + JWT secret (they were shared in chat during
   development) — do this BEFORE creating demo users / final submission.
3. `RATE_LIMIT_ENABLED=true` (default) in every deployed environment.
4. Supabase Dashboard → disable new sign-ups before judging if you want a controlled demo
   (auth → providers), or keep JIT enabled to let judges self-serve accounts.

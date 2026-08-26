# REVORA — Deployment Guide (Phase 14)

Target stack (all free tiers): **Render** (FastAPI backend) + **Vercel** (React frontend)
+ **Supabase** (Postgres + Auth). Razorpay Test Mode keys are optional and enable the
real retry-order path; everything else works without them.

**Total time: ~20 minutes.** Prerequisite: the repo pushed to GitHub (see the handoff
commands).

---

## 0. Rotate the Supabase keys (2 min, once)

The service-role key and JWT secret were shared in chat during development. Before going
public: Supabase Dashboard → your project → **Settings → API** → regenerate
**JWT Secret** and **service_role key**. Use the NEW values below.

## 1. Supabase — database (5 min)

1. Your project already exists. Copy the connection string:
   **Project Settings → Database → Connection string → URI**, pick the
   **Connection pooler / Transaction mode** string (port `6543`).
2. Convert it to SQLAlchemy format for Render:
   ```
   postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   (swap the scheme from `postgresql://` to `postgresql+psycopg://`).
3. Keep this string ready for step 2.

## 2. Render — backend (7 min)

1. **render.com** → sign in with GitHub → **New → Web Service** → pick your repo.
2. Settings (or commit `render.yaml` and use Blueprint — values marked `sync:false`
   are filled in the dashboard):
   - **Root directory:** `backend`
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance:** Free
   - **Health check:** `/api/health`
3. **Environment variables:**
   | Key | Value |
   |---|---|
   | `ENV` | `production` |
   | `DATABASE_URL` | the Supabase pooler string from step 1 |
   | `AUTO_CREATE_TABLES` | `true` (creates the 20 tables on first boot) |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_JWT_SECRET` | the (rotated) JWT secret |
   | `SUPABASE_SERVICE_ROLE_KEY` | the (rotated) service key |
   | `CORS_ORIGINS` | your Vercel URL (step 3) — set after first deploy, then redeploy |
   | `RATE_LIMIT_ENABLED` | `true` |
   | `MESSAGING_MODE` | `simulated` |
   | `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | optional `rzp_test_*` values |
4. Deploy → wait for `Live` → open `https://<service>.onrender.com/api/health` →
   expect `{"status":"ok","checks":{"database":"connected"}}`.
5. **First login:** in Supabase → Authentication → Users → **Add user** (your email +
   password). Sign in on the deployed frontend — the first Supabase login
   auto-provisions your merchant + owner role (JIT, audited).

## 3. Vercel — frontend (5 min)

1. **vercel.com** → sign in with GitHub → **Add New → Project** → import the repo.
2. Vercel reads `vercel.json` (root `frontend`, build `npm run build`, output `dist`).
3. **Environment variable** (Production + Preview):
   - `VITE_API_URL` = `https://<service>.onrender.com`
4. Deploy → open the URL → landing page → **Open the console** → sign in with the
   Supabase account → you're in.
5. Paste the Vercel URL into Render's `CORS_ORIGINS` (step 2) and redeploy the backend.

## 4. Razorpay Test Mode (optional, 5 min)

1. dashboard.razorpay.com → **Settings → API Keys** → copy Test `Key Id`/`Key Secret`
   (`rzp_test_*`) into Render env.
2. **Settings → Webhooks** → add webhook → URL:
   `https://<service>.onrender.com/api/webhooks/razorpay` → events:
   `payment.failed`, `payment.captured` → set a secret → paste into
   `RAZORPAY_WEBHOOK_SECRET`.
3. Redeploy. Verify from the console: **Policies-adjacent integrations** —
   `GET /api/integrations/razorpay` shows `configured: true, mode: test`, and
   `POST /api/integrations/razorpay/verify` performs a real read-only credential check.
4. No Live keys anywhere — `rzp_live_*` is refused by code.

## 5. Post-deploy verification checklist

- [ ] `https://<api>.onrender.com/api/health` → `status: ok`, database connected
- [ ] Landing page loads; corpus stats show `—` or labeled numbers (never invented)
- [ ] Supabase sign-in works; first user becomes owner
- [ ] Demo Lab → Run scenario → full trace renders
- [ ] Case detail shows candidate table + policy verdicts + audit trail
- [ ] Rate limit sanity: 31 rapid logins → 429 (liveness of hardening)
- [ ] (If Razorpay configured) `verify` endpoint → `connected`, mode `test`

## Local development (unchanged)

Double-click `start-backend.bat` + `start-frontend.bat`, or
`python -m app.infrastructure.scripts.dev_up`. Local SQLite by default; production
uses Supabase Postgres. Docs: `README.md`, `docs/` (architecture, ML, tests, security).

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../lib/api";
import { useAuth } from "../lib/auth";

const DEMO_ACCOUNTS: [string, string][] = [
  ["dev-owner@revora.local", "Owner (full access)"],
  ["dev-admin@revora.local", "Admin"],
  ["dev-operator@revora.local", "Operator (decides & acts)"],
  ["dev-viewer@revora.local", "Viewer (read-only)"],
];

export default function Login() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [serverOk, setServerOk] = useState<boolean | null>(null);
  const [env, setEnv] = useState<string>("local");

  const checkServer = () => {
    setServerOk(null);
    fetch(`${import.meta.env.VITE_API_URL ?? ""}/api/health`)
      .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
        setServerOk(ok);
        setEnv(body?.env ?? "local");
      })
      .catch(() => setServerOk(false));
  };

  useEffect(() => {
    checkServer();
  }, []);

  const production = env === "production";

  async function finish(token: string) {
    setToken(token);
    await refresh();
    navigate("/console");
  }

  async function devSignIn(emailAddress: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ access_token: string }>("/auth/dev/token", {
        method: "POST",
        body: JSON.stringify({ email: emailAddress }),
      });
      await finish(res.access_token);
    } catch (err: any) {
      setError(devErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function devErrorMessage(err: any): string {
    if (err?.status === 429) return "Too many sign-in attempts — wait about a minute and try again.";
    if (err?.status === 401) return err.message || "No account found for that email.";
    if (err?.status === 503) return "Auth isn't configured on the server (DEV_JWT_SECRET missing in backend/.env).";
    return err?.message ?? "Login failed";
  }

  async function productionSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      await finish(res.access_token);
    } catch (err: any) {
      if (err?.status === 429) setError("Too many sign-in attempts — wait a minute and retry.");
      else if (err?.status === 401) setError(err.message || "Invalid email or password.");
      else if (err?.status === 503) setError("Server auth is misconfigured (Supabase JWT secret mismatch).");
      else setError(err?.message ?? "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-zinc-950 p-4">
      <form onSubmit={production ? productionSubmit : (e) => { e.preventDefault(); }} className="card w-96 space-y-4">
        <div>
          <div className="text-2xl font-semibold tracking-wide text-red-500">REVORA</div>
          <div className="text-sm text-zinc-400">Revenue Recovery Decision Intelligence</div>
        </div>

        {/* Server status — answers "why is nothing happening" instantly */}
        <div
          className={`flex items-center justify-between rounded-md border px-3 py-2 text-xs ${
            serverOk === null
              ? "border-zinc-700 bg-zinc-900 text-zinc-400"
              : serverOk
                ? "border-emerald-800 bg-emerald-950/50 text-emerald-300"
                : "border-red-800 bg-red-950/50 text-red-300"
          }`}
        >
          <span className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                serverOk === null ? "bg-zinc-500" : serverOk ? "bg-emerald-400" : "bg-red-500"
              }`}
            />
            {serverOk === null
              ? "Checking server…"
              : serverOk
                ? "Server online — sign-in ready"
                : "Server OFFLINE — reload the page; if it stays red, the preview is sleeping"}
          </span>
          <button type="button" onClick={checkServer} className="underline hover:no-underline">
            Retry
          </button>
        </div>

        {production ? (
          <>
            <div>
              <label className="mb-1 block text-xs text-zinc-400">Email</label>
              <input
                className="input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="username"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-400">Password</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                minLength={6}
              />
            </div>
            {error && (
              <div className="rounded border border-red-800 bg-red-950/60 px-3 py-3 text-sm text-red-200">
                <div className="mb-1 font-semibold">Sign-in failed</div>
                {error}
              </div>
            )}
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="text-[11px] leading-relaxed text-zinc-600">
              Production sign-in runs through Supabase Auth. Your first login
              automatically creates your merchant workspace with the owner role.
            </p>
          </>
        ) : (
          <>
            <div>
              <div className="mb-1.5 text-xs text-zinc-400">
                Or sign in instantly as a demo role (no typing, no password):
              </div>
              <div className="grid grid-cols-2 gap-2">
                {DEMO_ACCOUNTS.map(([mail, label]) => (
                  <button
                    key={mail}
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEmail(mail);
                      void devSignIn(mail);
                    }}
                    className="btn-ghost justify-center text-left"
                  >
                    <span>
                      <span className="block text-sm text-zinc-100">{label}</span>
                      <span className="block text-[10px] text-zinc-500">{mail}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-400">Email</label>
              <input
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="dev-owner@revora.local"
              />
            </div>
            {error && (
              <div className="rounded border border-red-800 bg-red-950/60 px-3 py-3 text-sm text-red-200">
                <div className="mb-1 font-semibold">Sign-in failed</div>
                {error}
              </div>
            )}
            <button
              type="button"
              className="btn-primary w-full"
              disabled={busy || !email}
              onClick={() => void devSignIn(email)}
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="text-[11px] leading-relaxed text-zinc-600">
              Dev sign-in issues a locally-signed JWT and exists only while the backend runs in
              local mode without Supabase. In production this console authenticates via Supabase
              Auth.
            </p>
          </>
        )}
      </form>
    </div>
  );
}

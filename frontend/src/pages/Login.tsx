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
  const [email, setEmail] = useState("dev-owner@revora.local");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [serverOk, setServerOk] = useState<boolean | null>(null);

  const checkServer = () => {
    setServerOk(null);
    fetch(`${import.meta.env.VITE_API_URL ?? ""}/api/health`)
      .then((r) => setServerOk(r.ok))
      .catch(() => setServerOk(false));
  };

  useEffect(() => {
    checkServer();
  }, []);

  async function signIn(emailAddress: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ access_token: string }>("/auth/dev/token", {
        method: "POST",
        body: JSON.stringify({ email: emailAddress }),
      });
      setToken(res.access_token);
      await refresh();
      navigate("/console");
    } catch (err: any) {
      if (err?.status === 429) {
        setError("Too many sign-in attempts — wait about a minute and try again.");
      } else if (err?.status === 401) {
        setError(err.message || "No account found for that email.");
      } else if (err?.status === 503) {
        setError("Auth isn't configured on the server (DEV_JWT_SECRET missing in backend/.env).");
      } else {
        setError(err.message ?? "Login failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await signIn(email);
  }

  return (
    <div className="flex h-screen items-center justify-center bg-zinc-950">
      <form onSubmit={submit} className="card w-96 space-y-4">
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

        <div>
          <label className="mb-1 block text-xs text-zinc-400">Email</label>
          <input
            className="input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@merchant.in"
          />
        </div>
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
                  void signIn(mail);
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
          Dev sign-in issues a locally-signed JWT and exists only while the backend runs in
          local mode without Supabase. In production this console authenticates via Supabase
          Auth. Seeded accounts: dev-owner@ / dev-admin@ / dev-operator@ / dev-viewer@revora.local
        </p>
      </form>
    </div>
  );
}

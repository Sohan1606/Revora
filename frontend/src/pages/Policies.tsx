import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { atLeast, dt } from "../lib/format";
import { useAuth } from "../lib/auth";

interface PolicyRow {
  id: string;
  version: string;
  is_active: boolean;
  definition: Record<string, any>;
  created_at: string;
  activated_at: string | null;
}

export default function Policies() {
  const { user } = useAuth();
  const isAdmin = atLeast(user?.role, "admin");
  const [rows, setRows] = useState<PolicyRow[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [version, setVersion] = useState("");
  const [definition, setDefinition] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ policies: PolicyRow[] }>("/policies").then((d) => setRows(d.policies));
  }, []);
  useEffect(load, [load]);

  async function create() {
    try {
      const parsed = JSON.parse(definition);
      await api("/policies", {
        method: "POST",
        body: JSON.stringify({ version, definition: parsed }),
      });
      setMsg(`Policy ${version} created (inactive). Activate to apply to future decisions.`);
      setVersion("");
      setDefinition("");
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function activate(id: string) {
    try {
      await api(`/policies/${id}/activate`, { method: "POST" });
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Recovery Policies</h1>
        <p className="text-sm text-zinc-500">
          Deterministic hard constraints. Policy rules always override model recommendations;
          activating a version applies to future decisions only (history is immutable).
        </p>
      </div>

      {msg && <div className="card text-sm text-zinc-300">{msg}</div>}

      {isAdmin && (
        <div className="card space-y-2">
          <div className="text-xs uppercase tracking-wide text-zinc-500">New policy version</div>
          <div className="flex gap-2">
            <input className="input w-48" placeholder="version (e.g. tight-v3)"
                   value={version} onChange={(e) => setVersion(e.target.value)} />
            <textarea
              className="input mono text-xs"
              rows={4}
              placeholder='{"max_retries": 3, "max_contacts": 3, ...} — full definition JSON'
              value={definition}
              onChange={(e) => setDefinition(e.target.value)}
            />
          </div>
          <button className="btn-primary" disabled={!version || !definition} onClick={create}>
            Create version
          </button>
        </div>
      )}

      <div className="space-y-3">
        {rows.length === 0 && (
          <div className="card text-sm text-zinc-600">
            No policy versions yet — one is created automatically with the first
            decision; after that, every change is a new version.
          </div>
        )}
        {rows.map((p) => (
          <div key={p.id} className="card">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="mono font-medium">{p.version}</span>
                {p.is_active ? (
                  <span className="badge bg-emerald-900/60 text-emerald-300">active</span>
                ) : (
                  <span className="badge bg-zinc-800 text-zinc-500">inactive</span>
                )}
                <span className="text-xs text-zinc-600">
                  created {dt(p.created_at)}
                  {p.activated_at && ` · activated ${dt(p.activated_at)}`}
                </span>
              </div>
              <div className="flex gap-2">
                <button className="btn-ghost"
                        onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                  {expanded === p.id ? "Hide" : "Show"} definition
                </button>
                {isAdmin && !p.is_active && (
                  <button className="btn-primary" onClick={() => activate(p.id)}>Activate</button>
                )}
              </div>
            </div>
            {expanded === p.id && (
              <pre className="mono mt-3 max-h-72 overflow-auto rounded bg-zinc-950 p-3 text-xs text-zinc-400">
                {JSON.stringify(p.definition, null, 2)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

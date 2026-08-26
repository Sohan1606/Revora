import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { dt, rupees } from "../lib/format";

interface CaseRow {
  id: string;
  state: string;
  amount_paise: number;
  is_synthetic: boolean;
  opened_at: string;
  customer_id: string | null;
}

const STATES = ["", "at_risk", "analyzed", "action_selected", "awaiting_approval",
  "executed", "observing", "recovered", "stopped", "escalated", "closed"];

const STATE_COLOR: Record<string, string> = {
  recovered: "bg-emerald-900/60 text-emerald-300",
  stopped: "bg-zinc-800 text-zinc-400",
  escalated: "bg-amber-900/60 text-amber-300",
  awaiting_approval: "bg-amber-900/60 text-amber-300",
};

export default function Cases() {
  const [rows, setRows] = useState<CaseRow[]>([]);
  const [total, setTotal] = useState(0);
  const [state, setState] = useState("");
  const [synthetic, setSynthetic] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (off: number, append: boolean) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "50", offset: String(off) });
      if (state) params.set("state", state);
      if (synthetic) params.set("is_synthetic", synthetic);
      const data = await api<{ total: number; cases: CaseRow[] }>(
        `/recovery/cases?${params}`
      );
      setTotal(data.total);
      setRows((prev) => (append ? [...prev, ...data.cases] : data.cases));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [state, synthetic]);

  useEffect(() => {
    setOffset(0);
    load(0, false);
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <h1 className="text-xl font-semibold">Recovery Cases</h1>
        <div className="flex items-center gap-2 text-sm">
          <select className="input w-44" value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">All states</option>
            {STATES.filter(Boolean).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <select className="input w-40" value={synthetic} onChange={(e) => setSynthetic(e.target.value)}>
            <option value="">Live + synthetic</option>
            <option value="false">Live only</option>
            <option value="true">Synthetic only</option>
          </select>
        </div>
      </div>

      {error && <div className="card text-red-300">Failed to load: {error}</div>}

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs uppercase tracking-wide text-zinc-500">
              <th className="px-4 py-3">Case</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">State</th>
              <th className="px-4 py-3">Data</th>
              <th className="px-4 py-3">Opened</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td className="px-4 py-3">
                  <Link to={`/console/cases/${c.id}`} className="mono text-red-400 hover:text-red-300">
                    {c.id.slice(0, 8)}…
                  </Link>
                </td>
                <td className="mono px-4 py-3">{rupees(c.amount_paise)}</td>
                <td className="px-4 py-3">
                  <span className={`badge ${STATE_COLOR[c.state] ?? "bg-zinc-800 text-zinc-300"}`}>
                    {c.state}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {c.is_synthetic ? (
                    <span className="badge bg-amber-900/60 text-amber-300">synthetic</span>
                  ) : (
                    <span className="badge bg-emerald-900/50 text-emerald-300">live</span>
                  )}
                </td>
                <td className="px-4 py-3 text-zinc-400">{dt(c.opened_at)}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-600">
                  No cases match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-zinc-500">
        <span>
          {rows.length} of {total} shown
        </span>
        {rows.length < total && (
          <button
            className="btn-ghost"
            disabled={loading}
            onClick={() => {
              const next = offset + 50;
              setOffset(next);
              load(next, true);
            }}
          >
            {loading ? "Loading…" : "Load more"}
          </button>
        )}
      </div>
    </div>
  );
}

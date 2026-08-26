import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { pct, rupees } from "../lib/format";

interface Summary {
  revenue_at_risk_paise: number;
  active_cases: number;
  recovered_paise: number;
  cases_total: number;
  recovered_cases: number;
}

interface ControlCenterData {
  summary: { live: Summary; synthetic: Summary };
  cases_by_state: Record<string, number>;
  action_mix: Record<string, number>;
  model_mix: Record<string, number>;
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mono mt-1 text-2xl font-semibold text-zinc-100">{value}</div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

export default function ControlCenter() {
  const [data, setData] = useState<ControlCenterData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ControlCenterData>("/control-center")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="card text-red-300">Failed to load: {error}</div>;
  if (!data) return <div className="text-zinc-500">Loading control center…</div>;

  const live = data.summary.live;
  const syn = data.summary.synthetic;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Control Center</h1>
        <p className="text-sm text-zinc-500">
          Live merchant metrics — synthetic evaluation data is reported separately and never
          blended in.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-400">Live</h2>
        <div className="grid grid-cols-4 gap-4">
          <Metric label="Revenue at risk" value={rupees(live.revenue_at_risk_paise)}
                  sub={`${live.active_cases} active cases`} />
          <Metric label="Recovered" value={rupees(live.recovered_paise)} />
          <Metric label="Cases (all time)" value={String(live.cases_total)} />
          <Metric label="Recovery rate"
                  value={live.cases_total ? pct(live.recovered_cases / live.cases_total) : "—"}
                  sub={`${live.recovered_cases} recovered cases`} />
        </div>
        {live.cases_total === 0 && (
          <div className="card text-sm text-zinc-500">
            No live cases yet — connect Razorpay (Phase 11) or run the{" "}
            <span className="text-zinc-300">Demo Lab</span> to create labeled synthetic cases.
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-zinc-400">
          Synthetic evaluation corpus
          <span className="badge bg-amber-900/60 text-amber-300">labeled synthetic</span>
        </h2>
        <div className="grid grid-cols-4 gap-4">
          <Metric label="Revenue at risk" value={rupees(syn.revenue_at_risk_paise)}
                  sub={`${syn.active_cases} active cases`} />
          <Metric label="Recovered (simulated)" value={rupees(syn.recovered_paise)} />
          <Metric label="Cases (all time)" value={String(syn.cases_total)} />
          <Metric label="Recovery rate"
                  value={syn.cases_total ? pct(syn.recovered_cases / syn.cases_total) : "—"}
                  sub={`${syn.recovered_cases} recovered cases`} />
        </div>
      </section>

      <section className="grid grid-cols-3 gap-4">
        <div className="card">
          <h3 className="mb-2 text-xs uppercase tracking-wide text-zinc-500">Cases by state</h3>
          <div className="space-y-1 text-sm">
            {Object.entries(data.cases_by_state).length === 0 && (
              <div className="text-zinc-600">No live cases.</div>
            )}
            {Object.entries(data.cases_by_state).map(([state, n]) => (
              <div key={state} className="flex justify-between">
                <span className="text-zinc-400">{state}</span>
                <span className="mono text-zinc-200">{n}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h3 className="mb-2 text-xs uppercase tracking-wide text-zinc-500">Decision mix</h3>
          <div className="space-y-1 text-sm">
            {Object.entries(data.action_mix).length === 0 && (
              <div className="text-zinc-600">No decisions yet.</div>
            )}
            {Object.entries(data.action_mix).map(([action, n]) => (
              <div key={action} className="flex justify-between">
                <span className="text-zinc-400">{action}</span>
                <span className="mono text-zinc-200">{n}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h3 className="mb-2 text-xs uppercase tracking-wide text-zinc-500">Estimate basis</h3>
          <div className="space-y-1 text-sm">
            {Object.entries(data.model_mix).length === 0 && (
              <div className="text-zinc-600">No decisions yet.</div>
            )}
            {Object.entries(data.model_mix).map(([version, n]) => (
              <div key={version} className="flex justify-between">
                <span className="text-zinc-400">{version}</span>
                <span className="mono text-zinc-200">{n}</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

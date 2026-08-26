import { useState } from "react";
import { api } from "../lib/api";
import { atLeast, pct, rupees } from "../lib/format";
import { useAuth } from "../lib/auth";

const SCENARIOS = [
  ["insufficient_funds", "Insufficient funds (temporary)"],
  ["expired_card", "Expired card"],
  ["hard_decline", "Hard decline"],
  ["processor_issue", "Processor/gateway issue"],
  ["auth_required", "Authentication required"],
  ["customer_cancelled", "Customer cancelled"],
];

interface Trace {
  scenario: string;
  simulation: boolean;
  case_id: string;
  cause: string;
  case_state: string;
  decision: {
    id: string; action: string; status: string;
    expected_recovery_paise: number; model_version: string | null;
    explanation: Record<string, any>;
  };
  execution: { status: string; result: any; error: string | null } | null;
  outcome: { outcome: string; amount_recovered_paise: number; source: string } | null;
  experiment_arm: string | null;
}

export default function Simulator() {
  const { user } = useAuth();
  const canRun = atLeast(user?.role, "operator");
  const [scenario, setScenario] = useState(SCENARIOS[0][0]);
  const [amount, setAmount] = useState(8999);
  const [outcome, setOutcome] = useState("random");
  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const data = await api<Trace>(`/simulator/scenarios/${scenario}/run`, {
        method: "POST",
        body: JSON.stringify({ amount_paise: Math.round(amount * 100), outcome }),
      });
      setTrace(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Demo Lab</h1>
        <p className="text-sm text-zinc-500">
          Run a failure scenario end-to-end: detection → diagnosis → decision (all candidates +
          policy verdicts) → bounded execution → outcome.{" "}
          <span className="text-amber-400">
            Everything here is labeled synthetic (is_synthetic=true, source="simulator").
          </span>
        </p>
      </div>

      <div className="card grid grid-cols-4 items-end gap-3">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Scenario</label>
          <select className="input" value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {SCENARIOS.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Amount (₹)</label>
          <input className="input mono" type="number" value={amount}
                 min={1} onChange={(e) => setAmount(Number(e.target.value))} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">World response</label>
          <select className="input" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="random">random (honest draw)</option>
            <option value="recovered">customer pays</option>
            <option value="not_recovered">customer doesn't pay</option>
          </select>
        </div>
        <button className="btn-primary" disabled={!canRun || busy} onClick={run}>
          {busy ? "Running…" : "Run scenario"}
        </button>
      </div>

      {error && <div className="card text-red-300">{error}</div>}
      {!canRun && (
        <div className="card text-sm text-zinc-500">
          Your role ({user?.role}) can't operate the simulator — operator or above required.
        </div>
      )}

      {trace && (
        <div className="space-y-3">
          <div className="card grid grid-cols-5 gap-3 text-sm">
            <div>
              <div className="text-xs uppercase text-zinc-500">Diagnosis</div>
              <div className="mt-1">{trace.cause}</div>
            </div>
            <div>
              <div className="text-xs uppercase text-zinc-500">Chosen action</div>
              <div className="mono mt-1 text-red-400">{trace.decision.action}</div>
              <div className="text-[11px] text-zinc-600">{trace.decision.status}</div>
            </div>
            <div>
              <div className="text-xs uppercase text-zinc-500">Expected recovery</div>
              <div className="mono mt-1">{rupees(trace.decision.expected_recovery_paise)}</div>
              <div className="text-[11px] text-zinc-600">{trace.decision.model_version}</div>
            </div>
            <div>
              <div className="text-xs uppercase text-zinc-500">Execution</div>
              <div className={`mt-1 ${trace.execution?.status === "succeeded" ? "text-emerald-400" : "text-red-400"}`}>
                {trace.execution?.status ?? "—"}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase text-zinc-500">Outcome</div>
              <div className="mt-1">
                {trace.outcome
                  ? `${trace.outcome.outcome} · ${rupees(trace.outcome.amount_recovered_paise)}`
                  : "pending"}
              </div>
              <div className="text-[11px] text-zinc-600">
                {trace.outcome?.source}
                {trace.experiment_arm && ` · arm: ${trace.experiment_arm}`}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="mb-2 text-xs uppercase tracking-wide text-zinc-500">
              Candidate actions evaluated
            </div>
            <table className="w-full text-xs">
              <thead className="text-left text-zinc-500">
                <tr>
                  <th className="py-1 pr-4">action</th>
                  <th className="py-1 pr-4">P(recovery)</th>
                  <th className="py-1 pr-4">expected value</th>
                  <th className="py-1 pr-4">policy</th>
                </tr>
              </thead>
              <tbody className="mono">
                {(trace.decision.explanation?.candidates ?? []).map((c: any) => (
                  <tr key={c.action} className="border-t border-zinc-800/60">
                    <td className={`py-1.5 pr-4 ${c.action === trace.decision.action ? "text-red-400" : ""}`}>
                      {c.action}
                    </td>
                    <td className="py-1.5 pr-4">{pct(c.p)}</td>
                    <td className="py-1.5 pr-4">{rupees(c.ev_paise)}</td>
                    <td className="py-1.5 pr-4">
                      {c.policy === "allowed" ? (
                        <span className="text-emerald-400">allowed</span>
                      ) : (
                        <span className="text-red-400">{c.policy}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="text-xs text-zinc-600">
            Open case <span className="mono">{trace.case_id.slice(0, 8)}…</span> in Recovery Cases
            for the full audit trail.
          </div>
        </div>
      )}
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import { atLeast, dt, pct, rupees } from "../lib/format";
import { useAuth } from "../lib/auth";

interface Candidate {
  action: string;
  p_recovery: number;
  expected_value_paise: number;
  intervention_cost_paise: number;
  allowed_by_policy: boolean;
  blocked_reason: string | null;
}

interface DecisionRow {
  id: string;
  chosen_action: string;
  status: string;
  expected_recovery_paise: number;
  confidence: number;
  model_version: string | null;
  decided_at: string;
  explanation: Record<string, any>;
  candidates: Candidate[];
  executions: { id: string; status: string; result: any; error: string | null }[];
}

interface CaseDetailData {
  id: string;
  state: string;
  amount_paise: number;
  is_synthetic: boolean;
  opened_at: string;
  retry_count: number;
  contact_count: number;
  next_action_at: string | null;
  diagnosis: { primary_cause: string; confidence: number; model_version: string } | null;
  decisions: DecisionRow[];
  messages: any[];
  outcome: { outcome: string; amount_recovered_paise: number; source: string } | null;
  audit: { event_type: string; actor_type: string; created_at: string; payload: any }[];
}

export default function CaseDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [data, setData] = useState<CaseDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api<CaseDetailData>(`/recovery/cases/${id}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [id]);

  useEffect(load, [load]);

  async function act(path: string, label: string) {
    setBusy(true);
    setMsg(null);
    try {
      await api(path, { method: "POST" });
      setMsg(`${label} ✓`);
      load();
    } catch (e: any) {
      setMsg(`${label} failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <div className="card text-red-300">Failed to load: {error}</div>;
  if (!data) return <div className="text-zinc-500">Loading case…</div>;

  const canOperate = atLeast(user?.role, "operator");
  const pendingDecision = data.decisions.find((d) => d.status === "requires_approval");
  const executableDecision = data.decisions.find((d) => d.status === "approved_by_policy");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mono text-xl font-semibold">Case {data.id.slice(0, 8)}…</h1>
        <span className="badge bg-zinc-800 text-zinc-300">{data.state}</span>
        {data.is_synthetic ? (
          <span className="badge bg-amber-900/60 text-amber-300">synthetic</span>
        ) : (
          <span className="badge bg-emerald-900/50 text-emerald-300">live</span>
        )}
        {data.outcome && (
          <span className={`badge ${data.outcome.outcome === "recovered"
            ? "bg-emerald-900/60 text-emerald-300" : "bg-zinc-800 text-zinc-400"}`}>
            outcome: {data.outcome.outcome} ({data.outcome.source})
          </span>
        )}
      </div>

      {msg && <div className="card text-sm text-zinc-300">{msg}</div>}

      <div className="grid grid-cols-4 gap-4">
        <div className="card">
          <div className="text-xs uppercase text-zinc-500">Amount at risk</div>
          <div className="mono mt-1 text-xl">{rupees(data.amount_paise)}</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase text-zinc-500">Diagnosis</div>
          <div className="mt-1 text-sm">
            {data.diagnosis
              ? `${data.diagnosis.primary_cause} (${pct(data.diagnosis.confidence)})`
              : "—"}
          </div>
          <div className="text-[11px] text-zinc-600">
            classifier: {data.diagnosis?.model_version}
          </div>
        </div>
        <div className="card">
          <div className="text-xs uppercase text-zinc-500">Budget used</div>
          <div className="mono mt-1 text-sm">
            retries {data.retry_count} · contacts {data.contact_count}
          </div>
          {data.next_action_at && (
            <div className="text-[11px] text-zinc-600">next action {dt(data.next_action_at)}</div>
          )}
        </div>
        <div className="card flex flex-col justify-center gap-2">
          {canOperate && ["analyzed", "escalated"].includes(data.state) && (
            <button className="btn-primary" disabled={busy}
                    onClick={() => act(`/recovery/cases/${data.id}/decide`, "Decide")}>
              Run decision engine
            </button>
          )}
          {canOperate && executableDecision && (
            <button className="btn-ghost" disabled={busy}
                    onClick={() => act(`/recovery/decisions/${executableDecision.id}/execute`, "Execute")}>
              Execute {executableDecision.chosen_action}
            </button>
          )}
          {canOperate && pendingDecision && (
            <div className="flex gap-2">
              <button className="btn-primary flex-1" disabled={busy}
                      onClick={() => act(`/recovery/decisions/${pendingDecision.id}/approve`, "Approve")}>
                Approve {pendingDecision.chosen_action}
              </button>
              <button className="btn-ghost flex-1" disabled={busy}
                      onClick={() => act(`/recovery/decisions/${pendingDecision.id}/reject`, "Reject")}>
                Reject
              </button>
            </div>
          )}
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-400">Decisions</h2>
        {data.decisions.length === 0 && (
          <div className="card text-sm text-zinc-600">
            No decisions yet. Run the decision engine to evaluate candidate actions.
          </div>
        )}
        {data.decisions.map((d) => (
          <div key={d.id} className="card space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="mono font-semibold text-zinc-100">{d.chosen_action}</span>
              <span className="badge bg-zinc-800 text-zinc-400">{d.status}</span>
              {d.model_version && (
                <span className="badge bg-zinc-800 text-zinc-500">{d.model_version}</span>
              )}
              <span className="text-zinc-500">EV {rupees(d.expected_recovery_paise)}</span>
              <span className="text-zinc-500">· decided {dt(d.decided_at)}</span>
              {d.explanation?.excluded_rejected_actions?.length > 0 && (
                <span className="text-zinc-600">
                  excluded (human-rejected): {d.explanation.excluded_rejected_actions.join(", ")}
                </span>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-zinc-500">
                  <tr>
                    <th className="py-1 pr-4">candidate</th>
                    <th className="py-1 pr-4">P(recovery)</th>
                    <th className="py-1 pr-4">expected value</th>
                    <th className="py-1 pr-4">cost</th>
                    <th className="py-1 pr-4">policy</th>
                  </tr>
                </thead>
                <tbody className="mono">
                  {d.candidates.map((c) => (
                    <tr key={c.action} className="border-t border-zinc-800/60">
                      <td className={`py-1.5 pr-4 ${c.action === d.chosen_action ? "text-red-400" : ""}`}>
                        {c.action}
                      </td>
                      <td className="py-1.5 pr-4">{pct(c.p_recovery)}</td>
                      <td className="py-1.5 pr-4">{rupees(c.expected_value_paise)}</td>
                      <td className="py-1.5 pr-4">{rupees(c.intervention_cost_paise)}</td>
                      <td className="py-1.5 pr-4">
                        {c.allowed_by_policy ? (
                          <span className="text-emerald-400">allowed</span>
                        ) : (
                          <span className="text-red-400" title={c.blocked_reason ?? ""}>
                            blocked · {c.blocked_reason?.split(":")[0]}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {d.executions.map((e) => (
              <div key={e.id} className="rounded border border-zinc-800 bg-zinc-950/60 p-2 text-xs">
                <span className={`badge mr-2 ${
                  e.status === "succeeded" ? "bg-emerald-900/60 text-emerald-300"
                  : e.status === "failed" ? "bg-red-900/60 text-red-300"
                  : "bg-zinc-800 text-zinc-400"}`}>
                  execution {e.status}
                </span>
                {e.result?.simulated && (
                  <span className="badge mr-2 bg-amber-900/60 text-amber-300">simulated delivery</span>
                )}
                {e.error && <span className="text-red-400">{e.error}</span>}
                {e.result?.wait_hours && <span className="text-zinc-400">waits {e.result.wait_hours}h</span>}
              </div>
            ))}
          </div>
        ))}
      </section>

      <section className="card">
        <h2 className="mb-2 text-sm font-medium uppercase tracking-wide text-zinc-400">
          Audit trail
        </h2>
        <div className="space-y-1 text-xs">
          {data.audit.map((a, i) => (
            <div key={i} className="flex gap-3">
              <span className="mono w-36 shrink-0 text-zinc-500">{dt(a.created_at)}</span>
              <span className="w-40 shrink-0 text-zinc-300">{a.event_type}</span>
              <span className="text-zinc-600">{a.actor_type}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

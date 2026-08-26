import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { atLeast, pct, rupees } from "../lib/format";
import { useAuth } from "../lib/auth";

interface ExperimentRow {
  id: string;
  name: string;
  status: string;
  strategies: { treatment: string; control: string };
}

interface Results {
  name: string;
  status: string;
  treatment: { n: number; recovered: number; recovered_paise: number };
  control: { n: number; recovered: number; recovered_paise: number };
  treatment_rate: number;
  control_rate: number;
  incremental_recovered_paise: number;
  relative_uplift: number;
  total_assigned: number;
}

export default function Experiments() {
  const { user } = useAuth();
  const isAdmin = atLeast(user?.role, "admin");
  const [rows, setRows] = useState<ExperimentRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [name, setName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ experiments: ExperimentRow[] }>("/experiments").then((d) => setRows(d.experiments));
  }, []);
  useEffect(load, [load]);

  useEffect(() => {
    if (!selected) return setResults(null);
    api<Results>(`/experiments/${selected}`)
      .then(setResults)
      .catch(() => setResults(null));
  }, [selected]);

  async function create() {
    try {
      await api("/experiments", { method: "POST", body: JSON.stringify({ name, hypothesis }) });
      setName("");
      setHypothesis("");
      setMsg("Experiment created (draft).");
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function control(id: string, action: "start" | "stop") {
    try {
      await api(`/experiments/${id}/${action}`, { method: "POST" });
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  const maxBar = Math.max(results?.treatment.recovered_paise ?? 0, results?.control.recovered_paise ?? 0, 1);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Experiments</h1>
        <p className="text-sm text-zinc-500">
          Treatment (REVORA NBA policy) vs control (naive dunning). Incremental recovery =
          treatment recovered − control recovered, from authoritative outcome rows only.
          Assignment is deterministic and immutable.
        </p>
      </div>

      {isAdmin && (
        <div className="card space-y-2">
          <div className="text-xs uppercase tracking-wide text-zinc-500">New experiment</div>
          <div className="flex gap-2">
            <input className="input" placeholder="Name" value={name}
                   onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="Hypothesis (optional)" value={hypothesis}
                   onChange={(e) => setHypothesis(e.target.value)} />
            <button className="btn-primary shrink-0" disabled={name.length < 3} onClick={create}>
              Create draft
            </button>
          </div>
        </div>
      )}
      {msg && <div className="card text-sm text-zinc-300">{msg}</div>}

      <div className="card p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs uppercase text-zinc-500">
              <th className="px-4 py-3">Experiment</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Treatment</th>
              <th className="px-4 py-3">Control</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-zinc-600">
                No experiments yet.
              </td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}
                  className={`cursor-pointer border-b border-zinc-800/50 hover:bg-zinc-800/30 ${
                    selected === r.id ? "bg-zinc-800/40" : ""}`}
                  onClick={() => setSelected(r.id)}>
                <td className="px-4 py-3">{r.name}</td>
                <td className="px-4 py-3">
                  <span className="badge bg-zinc-800 text-zinc-300">{r.status}</span>
                </td>
                <td className="mono px-4 py-3 text-xs text-zinc-400">{r.strategies.treatment}</td>
                <td className="mono px-4 py-3 text-xs text-zinc-400">{r.strategies.control}</td>
                <td className="px-4 py-3 text-right">
                  {isAdmin && r.status === "draft" && (
                    <button className="btn-primary mr-2"
                            onClick={(e) => { e.stopPropagation(); control(r.id, "start"); }}>
                      Start
                    </button>
                  )}
                  {isAdmin && r.status === "running" && (
                    <button className="btn-ghost"
                            onClick={(e) => { e.stopPropagation(); control(r.id, "stop"); }}>
                      Stop
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {results && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">{results.name} — results</h2>
            <span className="badge bg-zinc-800 text-zinc-400">
              {results.total_assigned} assigned
            </span>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <div className="text-xs uppercase text-zinc-500">Treatment recovered</div>
              <div className="mono text-xl text-emerald-400">
                {rupees(results.treatment.recovered_paise)}
              </div>
              <div className="text-xs text-zinc-500">
                {results.treatment.recovered}/{results.treatment.n} ({pct(results.treatment_rate)})
              </div>
              <div className="mt-1 h-2 rounded bg-zinc-800">
                <div className="h-2 rounded bg-emerald-500"
                     style={{ width: `${(results.treatment.recovered_paise / maxBar) * 100}%` }} />
              </div>
            </div>
            <div>
              <div className="text-xs uppercase text-zinc-500">Control recovered</div>
              <div className="mono text-xl text-zinc-300">
                {rupees(results.control.recovered_paise)}
              </div>
              <div className="text-xs text-zinc-500">
                {results.control.recovered}/{results.control.n} ({pct(results.control_rate)})
              </div>
              <div className="mt-1 h-2 rounded bg-zinc-800">
                <div className="h-2 rounded bg-zinc-500"
                     style={{ width: `${(results.control.recovered_paise / maxBar) * 100}%` }} />
              </div>
            </div>
            <div>
              <div className="text-xs uppercase text-zinc-500">Incremental recovery</div>
              <div className={`mono text-xl ${results.incremental_recovered_paise >= 0
                ? "text-emerald-400" : "text-red-400"}`}>
                {results.incremental_recovered_paise >= 0 ? "+" : ""}
                {rupees(results.incremental_recovered_paise)}
              </div>
              <div className="text-xs text-zinc-500">
                rate uplift {pct(results.relative_uplift)}
              </div>
            </div>
          </div>
          <p className="text-[11px] text-zinc-600">
            Cases without outcomes are excluded from recovered counts and reported in arm sizes —
            no survivorship hiding.
          </p>
        </div>
      )}
    </div>
  );
}

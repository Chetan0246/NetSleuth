/** Page 1 — Overview Dashboard. Every number is counted by the backend. */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, RefreshCw } from "lucide-react";

import { api, ApiError } from "../lib/api";
import { formatTime, pct, useAsync } from "../lib/useAsync";
import { useSessionContext } from "../App";
import {
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  Metric,
  ModeBadge,
  ProbabilityBar,
  StatusBadge,
} from "../components/ui";

export default function OverviewPage() {
  const navigate = useNavigate();
  const { setSession } = useSessionContext();
  const overview = useAsync(() => api.overview(), []);
  const sessions = useAsync(() => api.listSessions(8), []);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [openError, setOpenError] = useState<ApiError | null>(null);

  const openSession = async (sessionId: string) => {
    if (openingId) return;
    setOpeningId(sessionId);
    setOpenError(null);
    try {
      const full = await api.getSession(sessionId);
      setSession(full);
      navigate("/lab");
    } catch (cause) {
      setOpenError(
        cause instanceof ApiError
          ? cause
          : new ApiError(0, "unknown_error", String(cause)),
      );
    } finally {
      setOpeningId(null);
    }
  };

  if (overview.loading) return <Loading label="Loading dashboard…" />;
  if (overview.error)
    return <ErrorNotice error={overview.error} onRetry={overview.reload} />;
  if (!overview.data) return null;

  const { stats, recent_diagnoses, latest_experiment } = overview.data;
  const isFresh = stats.sessions === 0 && stats.diagnoses === 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Overview</h2>
          <p className="text-xs text-slate-600">
            Current lab state and recent diagnostic activity. Statistics are counted from the
            backend database, not hardcoded.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ModeBadge mode="SIMULATED LAB" />
          <button
            type="button"
            onClick={() => {
              overview.reload();
              sessions.reload();
            }}
            className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50"
          >
            <RefreshCw size={14} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Metric label="Lab sessions" value={stats.sessions} hint="saved in SQLite" />
        <Metric label="Diagnoses" value={stats.diagnoses} hint="adaptive + baseline runs" />
        <Metric label="Observations" value={stats.observations} hint="recorded probe results" />
        <Metric label="Experiments" value={stats.experiments} hint="suite executions" />
        <Metric
          label="Experiment runs"
          value={stats.experiment_runs}
          hint="stored scenario runs"
        />
      </div>

      {isFresh && (
        <Card title="Getting started">
          <EmptyState
            title="No lab sessions yet"
            description="Create a lab session from one of the built-in topologies, inject a controlled fault, then run an evidence-guided diagnosis. The dashboard will fill in as you work; nothing here is pre-populated with sample data."
            action={
              <Link
                to="/lab"
                className="inline-flex items-center gap-1 rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
              >
                Open Network Lab
                <ArrowRight size={14} aria-hidden="true" />
              </Link>
            }
          />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Recent lab sessions"
          subtitle="Click a session to load it into the lab."
          actions={
            <Link to="/lab" className="text-xs font-medium text-sky-700 hover:underline">
              Open Network Lab
            </Link>
          }
        >
          {openError && (
            <div className="mb-2">
              <ErrorNotice error={openError} onRetry={() => setOpenError(null)} />
            </div>
          )}
          {sessions.loading ? (
            <Loading />
          ) : sessions.error ? (
            <ErrorNotice error={sessions.error} onRetry={sessions.reload} />
          ) : !sessions.data || sessions.data.sessions.length === 0 ? (
            <p className="py-4 text-sm text-slate-600">No sessions stored yet.</p>
          ) : (
            <ul className="divide-y divide-slate-200">
              {sessions.data.sessions.map((session) => (
                <li key={session.id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <button
                      type="button"
                      disabled={openingId !== null}
                      onClick={() => void openSession(session.id)}
                      className="truncate text-left text-sm font-medium text-sky-800 hover:underline disabled:opacity-50"
                    >
                      {session.name}
                    </button>
                    <p className="text-xs text-slate-600">
                      <code>{session.template_id}</code> · {session.node_count} nodes ·{" "}
                      {session.link_count} links · updated {formatTime(session.updated_at)}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    {session.active_fault_count > 0 ? (
                      <span className="rounded border border-red-300 bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-900">
                        {session.active_fault_count} active fault
                        {session.active_fault_count === 1 ? "" : "s"}
                      </span>
                    ) : (
                      <span className="rounded border border-green-300 bg-green-50 px-1.5 py-0.5 text-[11px] font-medium text-green-900">
                        healthy
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Recent diagnoses"
          subtitle="Latest conclusion per run, with the probes it took."
          actions={
            <Link to="/diagnose" className="text-xs font-medium text-sky-700 hover:underline">
              Run Diagnosis
            </Link>
          }
        >
          {recent_diagnoses.length === 0 ? (
            <p className="py-4 text-sm text-slate-600">
              No diagnoses have been run yet.
            </p>
          ) : (
            <ul className="divide-y divide-slate-200">
              {recent_diagnoses.map((item) => (
                <li key={item.id} className="space-y-1 py-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm">
                      <code className="text-xs">{item.id}</code> →{" "}
                      <strong>{item.destination_node_id}</strong>
                      {item.destination_service ? ` / ${item.destination_service}` : ""}
                    </span>
                    <StatusBadge status={item.status} />
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
                    <span>strategy: {item.strategy}</span>
                    <span>
                      probes: {item.probes_used}
                      {item.max_probes !== undefined && item.max_probes !== null
                        ? `/${item.max_probes}`
                        : ""}
                    </span>
                    <span>{formatTime(item.started_at)}</span>
                  </div>
                  {item.top_hypothesis && (
                    <div className="space-y-0.5">
                      <p className="text-xs font-medium text-slate-800">
                        leading: <code>{item.top_hypothesis}</code>
                      </p>
                      <ProbabilityBar
                        value={item.top_probability ?? 0}
                        label={item.top_hypothesis}
                      />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card
        title="Latest experiment"
        subtitle="Adaptive probing versus the fixed-order baseline, from stored run records."
      >
        {!latest_experiment ? (
          <EmptyState
            title="No experiment has been run"
            description="The Experiment Studio executes the ground-truth scenario suite with fixed seeds and computes accuracy and probe-count metrics from the resulting run records."
            action={
              <Link
                to="/experiments"
                className="inline-flex items-center gap-1 rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
              >
                Open Experiment Studio
                <ArrowRight size={14} aria-hidden="true" />
              </Link>
            }
          />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <StatusBadge status={latest_experiment.status} />
              <span>
                {latest_experiment.name} · {latest_experiment.completed_runs}/
                {latest_experiment.total_runs} runs · {formatTime(latest_experiment.created_at)}
              </span>
            </div>
            {latest_experiment.metrics && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-sm">
                  <caption className="sr-only">
                    Latest experiment metrics by strategy
                  </caption>
                  <thead>
                    <tr className="border-b border-slate-300 text-left text-xs uppercase tracking-wide text-slate-600">
                      <th scope="col" className="py-1.5 pr-3">Strategy</th>
                      <th scope="col" className="py-1.5 pr-3">Runs</th>
                      <th scope="col" className="py-1.5 pr-3">Top-1</th>
                      <th scope="col" className="py-1.5 pr-3">Top-3</th>
                      <th scope="col" className="py-1.5 pr-3">Mean probes</th>
                      <th scope="col" className="py-1.5">Inconclusive</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(latest_experiment.metrics.by_strategy).map(
                      ([strategy, block]) => (
                        <tr key={strategy} className="border-b border-slate-100">
                          <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                            {strategy}
                          </th>
                          <td className="py-1.5 pr-3 tabular-nums">{block.runs}</td>
                          <td className="py-1.5 pr-3 tabular-nums">
                            {pct(block.top1_accuracy, 1)}
                          </td>
                          <td className="py-1.5 pr-3 tabular-nums">
                            {pct(block.top3_accuracy, 1)}
                          </td>
                          <td className="py-1.5 pr-3 tabular-nums">
                            {block.mean_probes_all?.toFixed(2) ?? "—"}
                          </td>
                          <td className="py-1.5 tabular-nums">
                            {pct(block.inconclusive_rate, 1)}
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

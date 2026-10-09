/**
 * Page 5 — Experiment Studio.
 *
 * Runs the ground-truth scenario suite and renders the resulting metrics as charts
 * and tables. Every value comes from stored experiment run records returned by the
 * API; there is no placeholder data, and the page shows the actual run counts and
 * the measured experiment wall-clock time alongside the charts.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Download, Play } from "lucide-react";

import {
  api,
  ApiError,
  type ExperimentMetrics,
  type ExperimentSummary,
  type MetricBlock,
  type ScenarioCatalogue,
} from "../lib/api";
import { formatTime, num, pct, useAsync } from "../lib/useAsync";
import {
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  Metric,
  ModeBadge,
} from "../components/ui";

const STRATEGY_COLORS: Record<string, string> = {
  adaptive: "#0284c7",
  baseline: "#94a3b8",
};

/** The headline metric rows, each with its own formatter. Typed so a missing
 *  field in the API contract becomes a compile error rather than a blank cell. */
const METRIC_ROWS: [string, (block: MetricBlock) => string | number][] = [
  ["Runs", (block) => block.runs],
  ["Top-1 accuracy", (block) => pct(block.top1_accuracy, 1)],
  ["Top-3 accuracy", (block) => pct(block.top3_accuracy, 1)],
  [
    "Top-1 incl. accepted confusions",
    (block) => pct(block.accepted_confusion_adjusted_accuracy, 1),
  ],
  ["Mean probes (all runs)", (block) => num(block.mean_probes_all, 2)],
  ["Mean probes (conclusive only)", (block) => num(block.mean_probes_conclusive, 2)],
  ["Diagnosis coverage", (block) => pct(block.coverage, 1)],
  ["Inconclusive rate", (block) => pct(block.inconclusive_rate, 1)],
  ["Mean compute time", (block) => `${num(block.mean_elapsed_ms, 2)} ms`],
  [
    "Fault localization accuracy",
    (block) => `${pct(block.localization_accuracy, 1)} (n=${block.localization_evaluated})`,
  ],
];

export default function ExperimentsPage() {
  const catalogue = useAsync<ScenarioCatalogue>(() => api.scenarioCatalogue(), []);
  const [runsPerScenario, setRunsPerScenario] = useState(3);
  const [seed, setSeed] = useState(20261009);
  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
  const [selectedFaults, setSelectedFaults] = useState<string[]>([]);
  const [includeControl, setIncludeControl] = useState(true);
  const [maxProbes, setMaxProbes] = useState(8);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);
  const [estimate, setEstimate] = useState<{
    scenarios: number;
    strategies: number;
    runs_per_scenario: number;
    total_runs: number;
    exceeds_limit: boolean;
    limit: number;
  } | null>(null);

  useEffect(() => {
    if (!catalogue.data) return;
    setSelectedTemplates((current) =>
      current.length > 0 ? current : Array.from(new Set(catalogue.data!.scenarios.map((s) => s.template_id))),
    );
  }, [catalogue.data]);

  const payload = useMemo(
    () => ({
      runs_per_scenario: runsPerScenario,
      seed,
      template_ids: selectedTemplates.length > 0 ? selectedTemplates : null,
      fault_types: selectedFaults.length > 0 ? selectedFaults : null,
      max_probes: maxProbes,
      strategies: ["adaptive", "baseline"],
      include_control: includeControl,
    }),
    [runsPerScenario, seed, selectedTemplates, selectedFaults, maxProbes, includeControl],
  );

  useEffect(() => {
    if (!catalogue.data) return;
    let cancelled = false;
    api
      .estimateExperiment(payload)
      .then((result) => {
        if (!cancelled) setEstimate(result);
      })
      .catch(() => {
        if (!cancelled) setEstimate(null);
      });
    return () => {
      cancelled = true;
    };
  }, [payload, catalogue.data]);

  async function guard(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "unknown_error", String(cause)));
    } finally {
      setBusy(false);
    }
  }

  const runSuite = () =>
    guard(async () => {
      setSummary(await api.runExperiment(payload));
    });

  const templates = Array.from(new Set(catalogue.data?.scenarios.map((s) => s.template_id) ?? []));
  const faultTypes = catalogue.data?.fault_types ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Experiment Studio</h2>
          <p className="text-xs text-slate-600">
            Executes the ground-truth fault suite against both strategies with fixed seeds, then
            computes the reported metrics from the stored run records. Ground-truth labels are read
            only by the evaluator, after each run.
          </p>
        </div>
        <ModeBadge mode="SIMULATED LAB" />
      </div>

      {error && <ErrorNotice error={error} onRetry={() => setError(null)} />}

      {catalogue.loading ? (
        <Loading label="Loading the scenario catalogue…" />
      ) : catalogue.error ? (
        <ErrorNotice error={catalogue.error} onRetry={catalogue.reload} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Run configuration" subtitle="Validated before anything executes." className="lg:col-span-1">
            <div className="space-y-3 text-sm">
              <label className="block text-xs font-medium text-slate-700">
                Runs per scenario: <strong>{runsPerScenario}</strong>
                <input
                  type="range"
                  min={1}
                  max={20}
                  value={runsPerScenario}
                  onChange={(event) => setRunsPerScenario(Number(event.target.value))}
                  className="mt-1 w-full"
                />
                <span className="text-[11px] text-slate-500">
                  Each run uses <code>seed + run_index</code>, so a larger value samples more
                  independent packet-loss streams rather than repeating one.
                </span>
              </label>

              <label className="block text-xs font-medium text-slate-700">
                Base seed
                <input
                  type="number"
                  value={seed}
                  min={0}
                  onChange={(event) => setSeed(Number(event.target.value))}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
                />
              </label>

              <label className="block text-xs font-medium text-slate-700">
                Maximum probe budget: <strong>{maxProbes}</strong>
                <input
                  type="range"
                  min={1}
                  max={16}
                  value={maxProbes}
                  onChange={(event) => setMaxProbes(Number(event.target.value))}
                  className="mt-1 w-full"
                />
              </label>

              <fieldset>
                <legend className="text-xs font-medium text-slate-700">Templates</legend>
                <div className="mt-1 space-y-1">
                  {templates.map((templateId) => (
                    <label key={templateId} className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={selectedTemplates.includes(templateId)}
                        onChange={(event) =>
                          setSelectedTemplates((current) =>
                            event.target.checked
                              ? [...current, templateId]
                              : current.filter((item) => item !== templateId),
                          )
                        }
                      />
                      <code>{templateId}</code>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset>
                <legend className="text-xs font-medium text-slate-700">
                  Fault classes (none selected = all)
                </legend>
                <div className="mt-1 max-h-40 space-y-1 overflow-y-auto pr-1">
                  {faultTypes.map((faultType) => (
                    <label key={faultType} className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={selectedFaults.includes(faultType)}
                        onChange={(event) =>
                          setSelectedFaults((current) =>
                            event.target.checked
                              ? [...current, faultType]
                              : current.filter((item) => item !== faultType),
                          )
                        }
                      />
                      <code>{faultType}</code>
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={includeControl}
                  onChange={(event) => setIncludeControl(event.target.checked)}
                />
                include healthy control scenarios
              </label>

              {estimate && (
                <div
                  className={`rounded border px-2 py-1.5 text-xs ${
                    estimate.exceeds_limit
                      ? "border-red-300 bg-red-50 text-red-900"
                      : "border-slate-200 bg-slate-50 text-slate-700"
                  }`}
                >
                  Would execute{" "}
                  <strong>{estimate.total_runs}</strong> runs ({estimate.scenarios} scenarios ×{" "}
                  {estimate.strategies} strategies × {estimate.runs_per_scenario} runs)
                  {estimate.exceeds_limit && (
                    <> — exceeds the server limit of {estimate.limit}. Reduce the run count.</>
                  )}
                </div>
              )}

              <button
                type="button"
                disabled={busy || (estimate?.exceeds_limit ?? false)}
                onClick={() => void runSuite()}
                className="inline-flex w-full items-center justify-center gap-1 rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                <Play size={14} aria-hidden="true" />
                {busy ? "Running the suite…" : "Run experiment suite"}
              </button>
              <p className="text-[11px] text-slate-500">
                Runs execute synchronously against the in-process simulator. A run of 24 scenarios ×
                2 strategies × 3 runs is a few seconds; keeping it small keeps the request
                responsive.
              </p>
            </div>
          </Card>

          <div className="space-y-4 lg:col-span-2">
            {busy && <Loading label="Executing scenarios and computing metrics…" />}
            {!busy && !summary && (
              <Card title="No results yet">
                <EmptyState
                  title="Run the suite to generate real metrics"
                  description="This page deliberately has no sample data. Configure the run above and execute it: charts and tables will be rendered from the run records the backend stores."
                />
              </Card>
            )}
            {summary && <ResultsPanel summary={summary} />}
          </div>
        </div>
      )}

      {catalogue.data && (
        <Card
          title="Scenario catalogue and baseline sequence"
          subtitle="The exact cases executed, and the fixed order the baseline uses."
        >
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
                Fixed-order baseline sequence
              </h3>
              <ol className="mt-1 list-inside list-decimal space-y-0.5 text-xs text-slate-700">
                {catalogue.data.baseline_sequence.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ol>
              <p className="mt-2 text-[11px] text-slate-500">
                The adaptive strategy draws from the same candidate set with the same
                implementations, priors, likelihood model, stopping rule and budget; only the
                selection order differs.
              </p>
            </div>
            <div className="lg:col-span-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
                Scenarios ({catalogue.data.scenarios.length})
              </h3>
              <div className="mt-1 max-h-72 overflow-auto">
                <table className="w-full border-collapse text-[11px]">
                  <caption className="sr-only">Ground-truth scenario catalogue</caption>
                  <thead>
                    <tr className="border-b border-slate-300 text-left uppercase tracking-wide text-slate-600">
                      <th scope="col" className="py-1 pr-2">Scenario</th>
                      <th scope="col" className="py-1 pr-2">Fault</th>
                      <th scope="col" className="py-1 pr-2">Target</th>
                      <th scope="col" className="py-1">Expected cause</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalogue.data.scenarios.map((scenario) => (
                      <tr key={scenario.id} className="border-b border-slate-100">
                        <th scope="row" className="py-1 pr-2 text-left font-normal">
                          <code>{scenario.id}</code>
                          <div className="text-slate-500">{scenario.description}</div>
                        </th>
                        <td className="py-1 pr-2">
                          <code>{scenario.fault_type ?? "none"}</code>
                        </td>
                        <td className="py-1 pr-2">
                          <code>{scenario.target_id || "—"}</code>
                          {Object.keys(scenario.parameters).length > 0 && (
                            <div className="text-slate-500">
                              {JSON.stringify(scenario.parameters)}
                            </div>
                          )}
                        </td>
                        <td className="py-1">
                          <code>{scenario.expected_hypothesis}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function ResultsPanel({ summary }: { summary: ExperimentSummary }) {
  const metrics: ExperimentMetrics = summary.metrics;
  const strategies = Object.keys(metrics.by_strategy);

  const accuracyData = strategies.map((strategy) => ({
    strategy,
    "Top-1": (metrics.by_strategy[strategy].top1_accuracy ?? 0) * 100,
    "Top-3": (metrics.by_strategy[strategy].top3_accuracy ?? 0) * 100,
    "Top-1 incl. accepted confusions":
      (metrics.by_strategy[strategy].accepted_confusion_adjusted_accuracy ?? 0) * 100,
  }));

  const probeData = strategies.map((strategy) => ({
    strategy,
    "Mean probes (all runs)": metrics.by_strategy[strategy].mean_probes_all ?? 0,
    "Mean probes (conclusive)": metrics.by_strategy[strategy].mean_probes_conclusive ?? 0,
  }));

  const rateData = strategies.map((strategy) => ({
    strategy,
    Coverage: (metrics.by_strategy[strategy].coverage ?? 0) * 100,
    Inconclusive: (metrics.by_strategy[strategy].inconclusive_rate ?? 0) * 100,
  }));

  const faultClasses = Object.keys(metrics.by_fault_class).sort();
  const perFaultData = faultClasses.map((label) => ({
    fault: label,
    ...Object.fromEntries(
      strategies.map((strategy) => [
        strategy,
        (metrics.by_fault_class[label].by_strategy[strategy]?.top1_accuracy ?? 0) * 100,
      ]),
    ),
  }));

  const labels = metrics.confusion_matrix.labels;

  return (
    <>
      <Card
        title="Run summary"
        subtitle={`${summary.experiment_id} · generated ${formatTime(summary.generated_at)}`}
        actions={
          <>
            <a
              href={api.experimentExportUrl(summary.experiment_id, "json")}
              className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
            >
              <Download size={12} aria-hidden="true" /> JSON
            </a>
            <a
              href={api.experimentExportUrl(summary.experiment_id, "csv")}
              className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
            >
              <Download size={12} aria-hidden="true" /> CSV
            </a>
            <a
              href={api.experimentExportUrl(summary.experiment_id, "markdown")}
              className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
            >
              <Download size={12} aria-hidden="true" /> Markdown report
            </a>
          </>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Completed runs"
            value={summary.completed_runs}
            hint={`of ${summary.plan?.total_runs ?? "?"} planned · stored records: ${metrics.run_count}`}
          />
          <Metric
            label="Experiment compute time"
            value={`${num(summary.wall_clock_ms / 1000, 2)} s`}
            hint="measured wall clock for the whole suite"
          />
          <Metric label="Scenarios" value={metrics.per_scenario.length} hint="with control cases" />
          <Metric
            label="Seed / budget"
            value={`${summary.config.seed}`}
            hint={`max ${summary.config.max_probes} probes per run`}
          />
        </div>
        <p className="mt-2 text-[11px] text-slate-600">
          Elapsed time above is the measured duration of the diagnosis computation in the
          simulator. It is <strong>not</strong> network latency: the modelled RTTs and timeouts are
          recorded per observation inside each run and reported separately.
        </p>
      </Card>

      <Card title="Headline metrics by strategy">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <caption className="sr-only">Metric comparison between strategies</caption>
            <thead>
              <tr className="border-b border-slate-300 text-left text-xs uppercase tracking-wide text-slate-600">
                <th scope="col" className="py-1.5 pr-3">Metric</th>
                {strategies.map((strategy) => (
                  <th scope="col" key={strategy} className="py-1.5 pr-3">
                    {strategy}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {METRIC_ROWS.map(([label, extract]) => (
                <tr key={label} className="border-b border-slate-100">
                  <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                    {label}
                  </th>
                  {strategies.map((strategy) => (
                    <td key={strategy} className="py-1.5 pr-3 tabular-nums">
                      {extract(metrics.by_strategy[strategy])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Top-1 vs Top-3 accuracy" subtitle="Percent of runs, from stored records.">
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={accuracyData} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="strategy" fontSize={11} />
              <YAxis domain={[0, 100]} fontSize={11} unit="%" />
              <Tooltip formatter={(value: number) => `${value.toFixed(1)}%`} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="Top-1" fill="#0284c7" />
              <Bar dataKey="Top-3" fill="#7dd3fc" />
              <Bar dataKey="Top-1 incl. accepted confusions" fill="#f59e0b" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Mean probes to decision" subtitle="Lower is more efficient, not more accurate.">
          <div style={{ width: "100%", height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={probeData} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="strategy" fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip formatter={(value: number) => value.toFixed(2)} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Mean probes (all runs)" fill="#0284c7" />
                <Bar dataKey="Mean probes (conclusive)" fill="#94a3b8" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Coverage vs inconclusive rate" subtitle="How often the rule could decide.">
          <div style={{ width: "100%", height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={rateData} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="strategy" fontSize={11} />
                <YAxis domain={[0, 100]} fontSize={11} unit="%" />
                <Tooltip formatter={(value: number) => `${value.toFixed(1)}%`} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Coverage" fill="#16a34a" />
                <Bar dataKey="Inconclusive" fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <Card
        title="Top-1 accuracy per fault class"
        subtitle="Per-class breakdown, because an aggregate can hide a systematically missed cause."
      >
        <div style={{ width: "100%", height: 340 }}>
          <ResponsiveContainer>
            <BarChart data={perFaultData} margin={{ top: 8, right: 8, bottom: 90, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="fault" fontSize={10} angle={-40} textAnchor="end" interval={0} height={90} />
              <YAxis domain={[0, 100]} fontSize={11} unit="%" />
              <Tooltip formatter={(value: number) => `${value.toFixed(1)}%`} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {strategies.map((strategy) => (
                <Bar key={strategy} dataKey={strategy} fill={STRATEGY_COLORS[strategy] ?? "#475569"} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card title="Per-fault-class results" subtitle="Sample counts are shown so a rate is never read without its n.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <caption className="sr-only">Per fault class metrics by strategy</caption>
            <thead>
              <tr className="border-b border-slate-300 text-left text-xs uppercase tracking-wide text-slate-600">
                <th scope="col" className="py-1.5 pr-3">Expected cause</th>
                <th scope="col" className="py-1.5 pr-3">Strategy</th>
                <th scope="col" className="py-1.5 pr-3">Runs</th>
                <th scope="col" className="py-1.5 pr-3">Top-1</th>
                <th scope="col" className="py-1.5 pr-3">Top-3</th>
                <th scope="col" className="py-1.5 pr-3">Mean probes</th>
                <th scope="col" className="py-1.5">Inconclusive</th>
              </tr>
            </thead>
            <tbody>
              {faultClasses.map((label) =>
                Object.entries(metrics.by_fault_class[label].by_strategy).map(
                  ([strategy, block]) => (
                    <tr key={`${label}-${strategy}`} className="border-b border-slate-100">
                      <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                        <code>{label}</code>
                      </th>
                      <td className="py-1.5 pr-3">{strategy}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{block.runs}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{pct(block.top1_accuracy, 1)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{pct(block.top3_accuracy, 1)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{num(block.mean_probes_all, 2)}</td>
                      <td className="py-1.5 tabular-nums">{pct(block.inconclusive_rate, 1)}</td>
                    </tr>
                  ),
                ),
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Confusion matrix"
        subtitle="Rows: injected (ground truth). Columns: predicted leading hypothesis. Counts of stored runs."
      >
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <caption className="sr-only">Confusion matrix of actual versus predicted causes</caption>
            <thead>
              <tr className="border-b border-slate-300 text-left">
                <th scope="col" className="py-1 pr-2">actual ↓ / predicted →</th>
                {labels.map((label) => (
                  <th scope="col" key={label} className="py-1 pr-2 font-mono text-[10px]">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {labels.map((actual) => (
                <tr key={actual} className="border-b border-slate-100">
                  <th scope="row" className="py-1 pr-2 text-left font-mono text-[10px]">
                    {actual}
                  </th>
                  {labels.map((predicted) => {
                    const value = metrics.confusion_matrix.matrix[actual]?.[predicted] ?? 0;
                    const diagonal = actual === predicted;
                    return (
                      <td
                        key={predicted}
                        className={`py-1 pr-2 tabular-nums ${
                          diagonal && value > 0
                            ? "bg-green-100 font-semibold"
                            : value > 0
                              ? "bg-amber-50"
                              : "text-slate-400"
                        }`}
                      >
                        {value}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-slate-600">
          Diagonal cells are correct classifications. Off-diagonal cells are reported as errors
          unless they are one of the documented, protocol-justified confusions (shown in the
          table above and explained in the methodology notes).
        </p>
      </Card>

      <Card title="Per-scenario detail" subtitle="Every scenario with its seed and per-strategy outcome.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-xs">
            <caption className="sr-only">Per scenario results by strategy</caption>
            <thead>
              <tr className="border-b border-slate-300 text-left uppercase tracking-wide text-slate-600">
                <th scope="col" className="py-1.5 pr-3">Scenario</th>
                <th scope="col" className="py-1.5 pr-3">Fault</th>
                <th scope="col" className="py-1.5 pr-3">Strategy</th>
                <th scope="col" className="py-1.5 pr-3">Runs</th>
                <th scope="col" className="py-1.5 pr-3">Top-1</th>
                <th scope="col" className="py-1.5">Mean probes</th>
              </tr>
            </thead>
            <tbody>
              {metrics.per_scenario.map((scenario) =>
                Object.entries(scenario.by_strategy).map(([strategy, block]) => (
                  <tr key={`${scenario.scenario_id}-${strategy}`} className="border-b border-slate-100">
                    <th scope="row" className="py-1 pr-3 text-left font-normal">
                      <code>{scenario.scenario_id}</code>
                    </th>
                    <td className="py-1 pr-3">
                      <code>{scenario.actual_fault_type ?? "none"}</code>
                    </td>
                    <td className="py-1 pr-3">{strategy}</td>
                    <td className="py-1 pr-3 tabular-nums">{block.runs}</td>
                    <td className="py-1 pr-3 tabular-nums">{pct(block.top1_accuracy, 1)}</td>
                    <td className="py-1 tabular-nums">{num(block.mean_probes_all, 2)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Methodology notes" subtitle="Generated alongside the metrics.">
        <ul className="list-inside list-disc space-y-1 text-xs text-slate-700">
          {metrics.notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-slate-600">
          Configuration used: <code>{JSON.stringify(summary.config)}</code>
        </p>
      </Card>
    </>
  );
}

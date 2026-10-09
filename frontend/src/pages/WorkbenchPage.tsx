/**
 * Page 3 — Diagnostic Workbench.
 *
 * Run one adaptively selected probe at a time, or run to conclusion. The panel
 * always shows the *planner's* choice and its expected information gain, plus the
 * alternatives it was compared against, so the adaptive behaviour is visible rather
 * than asserted.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Play, RotateCcw, Scale, StepForward } from "lucide-react";

import { api, ApiError, type LabSession } from "../lib/api";
import { num, pct, useAsync } from "../lib/useAsync";
import { useSessionContext } from "../App";
import { TopologyView } from "../components/TopologyView";
import {
  Card,
  EmptyState,
  ErrorNotice,
  KindTag,
  Loading,
  ModeBadge,
  ProbabilityBar,
  StatusBadge,
  StrengthTag,
} from "../components/ui";

import { isTerminalStatus } from "../lib/status";

export default function WorkbenchPage() {
  const navigate = useNavigate();
  const { session, sessionId, diagnosis, setDiagnosis, setDiagnosisId, modelVersion } =
    useSessionContext();

  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [service, setService] = useState("");
  const [maxProbes, setMaxProbes] = useState(8);
  const [strategy, setStrategy] = useState<"adaptive" | "baseline">("adaptive");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [baseline, setBaseline] = useState<Awaited<
    ReturnType<typeof api.runBaseline>
  > | null>(null);

  const catalogue = useAsync(() => api.catalogue(), []);

  // Re-default every selection when the SESSION changes, keyed on the id rather than
  // the object identity. The previous version kept any existing value
  // (`current || hosts[0]?.id`), so after switching sessions the page held ids that do
  // not exist in the new topology: the <select> matched no option and the POST was
  // rejected with a 422 for an unknown source/destination node.
  useEffect(() => {
    if (!session) return;
    const hosts = session.topology.nodes.filter((node) => node.type === "host");
    const servers = session.topology.nodes.filter((node) => node.services.length > 0);
    setSource(hosts[0]?.id ?? "");
    setDestination(servers[0]?.id ?? "");
    setService(servers[0]?.services[0]?.name ?? "");
    // A different session invalidates the previous comparison snapshot too.
    setBaseline(null);
  }, [session, session?.id]);

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

  const start = () =>
    guard(async () => {
      if (!sessionId || !source || !destination) return;
      const created = await api.createDiagnosis({
        session_id: sessionId,
        source_node_id: source,
        destination_node_id: destination,
        destination_service: service || null,
        max_probes: maxProbes,
        strategy,
      });
      setDiagnosis(created);
      setDiagnosisId(created.id);
      setBaseline(null);
    });

  const startAndRun = () =>
    guard(async () => {
      if (!sessionId || !source || !destination) return;
      const created = await api.createDiagnosis({
        session_id: sessionId,
        source_node_id: source,
        destination_node_id: destination,
        destination_service: service || null,
        max_probes: maxProbes,
        strategy,
      });
      setDiagnosis(created);
      setDiagnosisId(created.id);
      setBaseline(null);
      const finished = await api.runDiagnosis(created.id);
      setDiagnosis(finished);
    });

  const step = () =>
    guard(async () => {
      if (!diagnosis) return;
      const updated = await api.stepDiagnosis(diagnosis.id);
      setDiagnosis(updated);
      // The comparison snapshot described the earlier probe sequence; keeping it
      // would show a stale adaptive run beside the live panel.
      setBaseline(null);
    });

  const runToConclusion = () =>
    guard(async () => {
      if (!diagnosis) return;
      const updated = await api.runDiagnosis(diagnosis.id);
      setDiagnosis(updated);
      setBaseline(null);
    });

  const compareBaseline = () =>
    guard(async () => {
      if (!diagnosis) return;
      const result = await api.runBaseline(diagnosis.id, diagnosis.max_probes);
      setBaseline(result);
    });

  const hypothesisTitles = new Map(
    (catalogue.data?.hypotheses ?? []).map((item) => [item.code, item.title]),
  );
  const isTerminal = diagnosis !== null && isTerminalStatus(diagnosis.status);
  const destinationServices =
    session?.topology.nodes.find((node) => node.id === destination)?.services ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Diagnostic Workbench</h2>
          <p className="text-xs text-slate-600">
            The adaptive planner chooses each next probe by expected information gain. Every
            conclusion below is computed from observations the probes actually produced.
          </p>
        </div>
        <ModeBadge mode="SIMULATED LAB" />
      </div>

      {!session && (
        <Card title="No lab session loaded">
          <EmptyState
            title="Load a lab session first"
            description="The workbench runs against a lab session, so you need a topology and (optionally) an injected fault before starting a diagnosis."
            action={
              <Link
                to="/lab"
                className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
              >
                Open Network Lab
              </Link>
            }
          />
        </Card>
      )}

      {error && <ErrorNotice error={error} onRetry={() => setError(null)} />}

      {session && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card
            title="Diagnosis setup"
            subtitle={`Active faults in this session: ${
              session.active_faults.filter((fault) => fault.is_active).length
            }`}
            className="lg:col-span-1"
          >
            <div className="space-y-3">
              <label className="block text-xs font-medium text-slate-700">
                Source host
                <select
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  disabled={busy || (diagnosis !== null && !isTerminal)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm disabled:bg-slate-100"
                >
                  {session.topology.nodes
                    .filter((node) => node.type === "host")
                    .map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.name} ({node.ip_address})
                      </option>
                    ))}
                </select>
              </label>

              <label className="block text-xs font-medium text-slate-700">
                Destination
                <select
                  value={destination}
                  onChange={(event) => {
                    setDestination(event.target.value);
                    const target = session.topology.nodes.find(
                      (node) => node.id === event.target.value,
                    );
                    setService(target?.services[0]?.name ?? "");
                  }}
                  disabled={busy || (diagnosis !== null && !isTerminal)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm disabled:bg-slate-100"
                >
                  {session.topology.nodes.map((node) => (
                    <option key={node.id} value={node.id} disabled={node.id === source}>
                      {node.name} ({node.ip_address})
                    </option>
                  ))}
                </select>
              </label>

              <label className="block text-xs font-medium text-slate-700">
                Target service / port
                <select
                  value={service}
                  onChange={(event) => setService(event.target.value)}
                  disabled={busy || (diagnosis !== null && !isTerminal)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm disabled:bg-slate-100"
                >
                  <option value="">(no service — IP-only diagnosis)</option>
                  {destinationServices.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name} (port {item.port})
                    </option>
                  ))}
                </select>
              </label>

              <label className="block text-xs font-medium text-slate-700">
                Maximum probe budget: <strong>{maxProbes}</strong>
                <input
                  type="range"
                  min={1}
                  max={16}
                  value={maxProbes}
                  disabled={busy || (diagnosis !== null && !isTerminal)}
                  onChange={(event) => setMaxProbes(Number(event.target.value))}
                  className="mt-1 w-full"
                />
              </label>

              <fieldset disabled={busy || (diagnosis !== null && !isTerminal)}>
                <legend className="text-xs font-medium text-slate-700">Strategy</legend>
                <div className="mt-1 space-y-1 text-sm">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="strategy"
                      checked={strategy === "adaptive"}
                      onChange={() => setStrategy("adaptive")}
                    />
                    adaptive (expected information gain)
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="strategy"
                      checked={strategy === "baseline"}
                      onChange={() => setStrategy("baseline")}
                    />
                    fixed-order baseline
                  </label>
                </div>
              </fieldset>

              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  disabled={busy || !source || !destination}
                  onClick={() => void start()}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
                >
                  Start diagnosis
                </button>
                <button
                  type="button"
                  disabled={busy || !source || !destination}
                  onClick={() => void startAndRun()}
                  className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  Start &amp; run to conclusion
                </button>
              </div>
              <p className="text-xs text-slate-600">
                Model revision: <code>{modelVersion}</code>. Priors are uniform and documented;
                the engine never reads the injected fault label.
              </p>
            </div>
          </Card>

          <div className="space-y-4 lg:col-span-2">
            {!diagnosis ? (
              <Card title="No active diagnosis">
                <EmptyState
                  title="Start a diagnosis to see evidence"
                  description="Once started, this panel shows the current hypothesis ranking, the next probe the planner selected with its expected information gain, and the full evidence from every executed probe."
                />
              </Card>
            ) : (
              <>
                <Card
                  title={`Diagnosis ${diagnosis.id}`}
                  subtitle={`${diagnosis.strategy_label} · strategy: ${diagnosis.strategy} · seed ${diagnosis.random_seed} · model ${diagnosis.model_version}`}
                  actions={
                    <>
                      <StatusBadge status={diagnosis.status} />
                      <button
                        type="button"
                        onClick={() => void step()}
                        disabled={busy || isTerminal}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
                      >
                        <StepForward size={14} aria-hidden="true" />
                        Run One Step
                      </button>
                      <button
                        type="button"
                        onClick={() => void runToConclusion()}
                        disabled={busy || isTerminal}
                        className="inline-flex items-center gap-1 rounded bg-slate-900 px-2.5 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                      >
                        <Play size={14} aria-hidden="true" />
                        Run to Conclusion
                      </button>
                      <button
                        type="button"
                        onClick={() => void compareBaseline()}
                        disabled={busy}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
                      >
                        <Scale size={14} aria-hidden="true" />
                        Compare with Baseline
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setDiagnosis(null);
                          setDiagnosisId(null);
                          setBaseline(null);
                        }}
                        disabled={busy}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
                      >
                        <RotateCcw size={14} aria-hidden="true" />
                        Reset Diagnosis
                      </button>
                    </>
                  }
                >
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
                      <p className="text-xs uppercase tracking-wide text-slate-500">Probes</p>
                      <p className="font-mono text-lg tabular-nums">
                        {diagnosis.probes_used}/{diagnosis.max_probes}
                      </p>
                    </div>
                    <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
                      <p className="text-xs uppercase tracking-wide text-slate-500">
                        Belief entropy
                      </p>
                      <p className="font-mono text-lg tabular-nums">
                        {num(diagnosis.beliefs.entropy_bits, 3)} bits
                      </p>
                    </div>
                    <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
                      <p className="text-xs uppercase tracking-wide text-slate-500">
                        Lead over runner-up
                      </p>
                      <p className="font-mono text-lg tabular-nums">
                        {pct(diagnosis.beliefs.lead_over_runner_up, 1)}
                      </p>
                    </div>
                  </div>

                  {isTerminal && (
                    <div className="mt-3 rounded border border-slate-300 bg-slate-50 px-3 py-2 text-sm">
                      <p className="font-semibold">
                        {diagnosis.report?.headline ??
                          `${diagnosis.status}: ${diagnosis.beliefs.leader.code}`}
                      </p>
                      <p className="mt-1 text-xs text-slate-700">
                        {diagnosis.stopping_reason}
                      </p>
                      <Link
                        to="/report"
                        className="mt-2 inline-block text-xs font-medium text-sky-700 hover:underline"
                      >
                        Open the full evidence report →
                      </Link>
                    </div>
                  )}
                </Card>

                <Card
                  title="Hypothesis ranking"
                  subtitle="Posterior probability per modelled explanation, with the prior for comparison."
                >
                  <ul className="space-y-2">
                    {diagnosis.beliefs.ranked.map((hypothesis, index) => (
                      <li key={hypothesis.code} className="space-y-1">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <span className="text-sm">
                            <span className="mr-1.5 font-mono text-xs text-slate-500">
                              #{index + 1}
                            </span>
                            <code className="font-semibold">{hypothesis.code}</code>{" "}
                            <span className="text-xs text-slate-600">
                              — {hypothesisTitles.get(hypothesis.code) ?? hypothesis.title}
                            </span>
                          </span>
                          <span className="text-xs text-slate-500">
                            prior {pct(hypothesis.prior, 1)} · layers {hypothesis.layers.join(", ")}
                          </span>
                        </div>
                        <ProbabilityBar
                          value={hypothesis.probability}
                          label={hypothesis.code}
                          emphasis={index === 0}
                        />
                        {index === 0 && (
                          <p className="text-xs text-slate-600">{hypothesis.summary}</p>
                        )}
                      </li>
                    ))}
                  </ul>
                </Card>

                <Card
                  title="Next probe selected by the planner"
                  subtitle="Chosen for its expected reduction in uncertainty per unit cost."
                >
                  {diagnosis.next_probe ? (
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="rounded border border-sky-300 bg-sky-50 px-2 py-0.5 text-sm">
                          {diagnosis.next_probe.probe_key}
                        </code>
                        <span className="text-xs text-slate-600">
                          {diagnosis.next_probe.label}
                        </span>
                      </div>
                      <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                        <div>
                          <dt className="text-slate-500">Expected information gain</dt>
                          <dd className="font-mono tabular-nums">
                            {num(diagnosis.next_probe.expected_information_gain_bits, 4)} bits
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Relative cost</dt>
                          <dd className="font-mono tabular-nums">{diagnosis.next_probe.cost}</dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">EIG / cost score</dt>
                          <dd className="font-mono tabular-nums">
                            {num(diagnosis.next_probe.score, 4)}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Current entropy</dt>
                          <dd className="font-mono tabular-nums">
                            {num(diagnosis.next_probe.entropy_before_bits, 3)} bits
                          </dd>
                        </div>
                      </dl>
                      <p className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-700">
                        <strong>Why this probe: </strong>
                        {diagnosis.next_probe.reason}
                      </p>
                      {diagnosis.next_probe.considered_alternatives.length > 1 && (
                        <details className="text-xs">
                          <summary className="cursor-pointer font-medium text-slate-700">
                            Alternatives considered (
                            {diagnosis.next_probe.considered_alternatives.length})
                          </summary>
                          <table className="mt-1 w-full border-collapse text-[11px]">
                            <caption className="sr-only">Probe candidates ranked by EIG per cost</caption>
                            <thead>
                              <tr className="text-left text-slate-600">
                                <th scope="col" className="py-0.5 pr-2">Probe</th>
                                <th scope="col" className="py-0.5 pr-2">EIG (bits)</th>
                                <th scope="col" className="py-0.5 pr-2">Cost</th>
                                <th scope="col" className="py-0.5">Score</th>
                              </tr>
                            </thead>
                            <tbody>
                              {diagnosis.next_probe.considered_alternatives.map((item) => (
                                <tr key={item.probe_key} className="border-t border-slate-100">
                                  <td className="py-0.5 pr-2">
                                    <code>{item.probe_key}</code>
                                  </td>
                                  <td className="py-0.5 pr-2 tabular-nums">
                                    {num(item.expected_information_gain_bits, 4)}
                                  </td>
                                  <td className="py-0.5 pr-2 tabular-nums">{item.cost}</td>
                                  <td className="py-0.5 tabular-nums">{num(item.score, 4)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </details>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-slate-700">
                      The diagnosis has reached a terminal state, so no further probe is planned.
                      {diagnosis.stopping_reason}
                    </p>
                  )}
                </Card>

                {diagnosis.rejected_candidates.length > 0 && (
                  <Card
                    title="Probes not available for this target"
                    subtitle="Removed from the candidate set with an explicit reason, not silently skipped."
                  >
                    <ul className="space-y-1 text-xs">
                      {diagnosis.rejected_candidates.map((item) => (
                        <li key={item.probe_key} className="flex flex-wrap gap-2">
                          <code className="text-slate-700">{item.probe_key}</code>
                          <span className="text-slate-600">{item.reason}</span>
                        </li>
                      ))}
                    </ul>
                  </Card>
                )}

                {baseline && (
                  <Card
                    title="Adaptive versus fixed-order baseline"
                    subtitle={String(baseline.same_conditions.difference ?? "")}
                  >
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[560px] border-collapse text-sm">
                        <caption className="sr-only">
                          Adaptive and baseline strategy comparison
                        </caption>
                        <thead>
                          <tr className="border-b border-slate-300 text-left text-xs uppercase tracking-wide text-slate-600">
                            <th scope="col" className="py-1.5 pr-3">Metric</th>
                            <th scope="col" className="py-1.5 pr-3">Adaptive</th>
                            <th scope="col" className="py-1.5">Fixed order</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr className="border-b border-slate-100">
                            <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                              Probes used
                            </th>
                            <td className="py-1.5 pr-3 tabular-nums">
                              {baseline.adaptive.probes_used}
                            </td>
                            <td className="py-1.5 tabular-nums">{baseline.baseline.probes_used}</td>
                          </tr>
                          <tr className="border-b border-slate-100">
                            <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                              Top hypothesis
                            </th>
                            <td className="py-1.5 pr-3">
                              <code>{baseline.adaptive.top_hypothesis}</code> (
                              {pct(baseline.adaptive.top_probability, 1)})
                            </td>
                            <td className="py-1.5">
                              <code>{baseline.baseline.top_hypothesis}</code> (
                              {pct(baseline.baseline.top_probability, 1)})
                            </td>
                          </tr>
                          <tr className="border-b border-slate-100">
                            <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                              Terminal status
                            </th>
                            <td className="py-1.5 pr-3">{baseline.adaptive.status}</td>
                            <td className="py-1.5">{baseline.baseline.status}</td>
                          </tr>
                          <tr>
                            <th scope="row" className="py-1.5 pr-3 align-top text-left font-medium">
                              Probe order
                            </th>
                            <td className="py-1.5 pr-3">
                              <ol className="list-inside list-decimal font-mono text-[11px]">
                                {baseline.adaptive.probe_sequence.map((key) => (
                                  <li key={key}>{key}</li>
                                ))}
                              </ol>
                            </td>
                            <td className="py-1.5">
                              <ol className="list-inside list-decimal font-mono text-[11px]">
                                {baseline.baseline.probe_sequence.map((key) => (
                                  <li key={key}>{key}</li>
                                ))}
                              </ol>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 text-xs text-slate-600">
                      Both runs used the same topology, active faults, priors, likelihood model,
                      stopping rule and probe budget. Only the selection order differed. For a
                      statistical comparison over many scenarios with fixed seeds, use the
                      Experiment Studio.
                    </p>
                  </Card>
                )}

                <Card
                  title="Probe timeline"
                  subtitle="Each probe with its outcome, its modelled duration and the information it added."
                >
                  {diagnosis.steps.length === 0 ? (
                    <p className="text-sm text-slate-600">
                      No probe has been executed yet. Use <em>Run One Step</em> to watch the
                      planner work, or <em>Run to Conclusion</em>.
                    </p>
                  ) : (
                    <ol className="space-y-3">
                      {diagnosis.steps.map((stepItem) => (
                        <li
                          key={stepItem.sequence_number}
                          className="rounded border border-slate-200 bg-white p-3"
                        >
                          <div className="flex flex-wrap items-baseline justify-between gap-2">
                            <span className="text-sm font-medium">
                              {stepItem.sequence_number}. {stepItem.probe_label}{" "}
                              <code className="text-xs text-slate-600">
                                {stepItem.probe_key}
                              </code>
                            </span>
                            <span className="flex items-center gap-2 text-xs text-slate-600">
                              <ModeBadge mode={stepItem.mode} />
                              <code className="rounded bg-slate-100 px-1.5 py-0.5">
                                {stepItem.outcome}
                              </code>
                            </span>
                          </div>
                          <p className="mt-1 text-xs text-slate-700">{stepItem.summary}</p>
                          <dl className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-slate-600 sm:grid-cols-4">
                            <div>
                              <dt className="text-slate-500">Planned EIG</dt>
                              <dd className="font-mono tabular-nums">
                                {num(stepItem.planned_information_gain_bits, 4)} bits
                              </dd>
                            </div>
                            <div>
                              <dt className="text-slate-500">Entropy</dt>
                              <dd className="font-mono tabular-nums">
                                {num(stepItem.entropy_before_bits, 2)} →{" "}
                                {num(stepItem.entropy_after_bits, 2)}
                              </dd>
                            </div>
                            <div>
                              <dt className="text-slate-500">Modelled time (sim)</dt>
                              <dd className="font-mono tabular-nums">
                                {num(stepItem.modelled_elapsed_ms, 1)} ms
                              </dd>
                            </div>
                            <div>
                              <dt className="text-slate-500">Measured compute time</dt>
                              <dd className="font-mono tabular-nums">
                                {num(stepItem.measured_wall_clock_ms, 2)} ms
                              </dd>
                            </div>
                          </dl>
                          <p className="mt-2 text-[11px] text-slate-500">
                            <strong>Selected because: </strong>
                            {stepItem.selected_reason}
                          </p>
                          {stepItem.evidence.length > 0 && (
                            <ul className="mt-2 space-y-1">
                              {stepItem.evidence.map((statement, index) => (
                                <li
                                  key={index}
                                  className="flex flex-wrap items-start gap-1.5 text-xs text-slate-800"
                                >
                                  <KindTag kind={statement.kind} />
                                  <StrengthTag strength={statement.strength} />
                                  <span>{statement.statement}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                </Card>
              </>
            )}
          </div>
        </div>
      )}

      {session && diagnosis && (
        <Card
          title="Topology with the suspected component"
          subtitle={
            diagnosis.suspected_component.component_id
              ? `Localized to ${diagnosis.suspected_component.component_kind} ${diagnosis.suspected_component.component_id} (location confidence: ${diagnosis.suspected_component.confidence})`
              : "No single component could be identified from the executed probes."
          }
        >
          <TopologyView
            topology={session.topology}
            suspectedComponentId={diagnosis.suspected_component.component_id}
            suspects={{
              links: new Set(
                diagnosis.suspected_component.component_kind === "link" &&
                  diagnosis.suspected_component.component_id
                  ? [diagnosis.suspected_component.component_id]
                  : [],
              ),
              nodes: new Set(
                diagnosis.suspected_component.component_kind === "node" &&
                  diagnosis.suspected_component.component_id
                  ? [diagnosis.suspected_component.component_id]
                  : [],
              ),
              services: new Set(
                diagnosis.suspected_component.component_kind === "service" &&
                  diagnosis.suspected_component.component_id
                  ? [diagnosis.suspected_component.component_id]
                  : [],
              ),
            }}
          />
          {diagnosis.suspected_component.evidence.length > 0 && (
            <ul className="mt-2 list-inside list-disc text-xs text-slate-700">
              {diagnosis.suspected_component.evidence.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {catalogue.error && (
        <ErrorNotice error={catalogue.error} onRetry={catalogue.reload} />
      )}
      {catalogue.loading && <Loading label="Loading hypotheses…" />}

      {session && (
        <Card title="Continue to the report" subtitle="Evidence, alternatives and export.">
          <button
            type="button"
            onClick={() => navigate("/report")}
            disabled={!diagnosis}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            Open Evidence &amp; Diagnosis Report
          </button>
          {!diagnosis && (
            <span className="ml-2 text-xs text-slate-600">
              (start a diagnosis first)
            </span>
          )}
        </Card>
      )}

      {session && <LabSessionFacts session={session} />}
    </div>
  );
}

function LabSessionFacts({ session }: { session: LabSession }) {
  const active = session.active_faults.filter((fault) => fault.is_active);
  return (
    <Card
      title="Current lab fault state"
      subtitle="What the probes are seeing right now. The diagnostic engine does not read these labels; it only observes their effects."
    >
      {active.length === 0 ? (
        <p className="text-sm text-slate-700">
          No fault is active. A diagnosis should conclude{" "}
          <code>NO_FAULT_DETECTED</code> once enough distinct probe classes have run.
        </p>
      ) : (
        <ul className="space-y-1 text-xs">
          {active.map((fault) => (
            <li key={fault.id} className="rounded border border-red-200 bg-red-50 px-2 py-1">
              <strong>{fault.fault_type}</strong> on <code>{fault.target_id}</code>
              {fault.parameter_summary !== "no parameters" && (
                <span className="text-slate-600"> · {fault.parameter_summary}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/**
 * Page 4 — Evidence and Diagnosis Report.
 *
 * Shows the full evidence timeline, the conclusion with its qualification, what the
 * leading hypothesis explains, what it does not, and the recommended next step.
 * Exports are produced by the backend so the downloaded file is the same data the
 * page renders.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Download, FileJson, FileText } from "lucide-react";

import { api, ApiError } from "../lib/api";
import { num, pct, useAsync } from "../lib/useAsync";
import { useSessionContext } from "../App";
import { isTerminalStatus } from "../lib/status";
import { TopologyView } from "../components/TopologyView";
import {
  Card,
  EmptyState,
  ErrorNotice,
  KindTag,
  Loading,
  Metric,
  ModeBadge,
  ProbabilityBar,
  StatusBadge,
  StrengthTag,
} from "../components/ui";

export default function ReportPage() {
  const { session, diagnosis, setDiagnosis, diagnosisId } = useSessionContext();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const catalogue = useAsync(() => api.catalogue(), []);
  const titles = new Map((catalogue.data?.hypotheses ?? []).map((item) => [item.code, item.title]));

  // Reload from the backend when arriving here directly with an id but no object
  // in context (for example after a page refresh).
  useEffect(() => {
    if (diagnosis || !diagnosisId) return;
    let cancelled = false;
    api
      .getDiagnosis(diagnosisId)
      .then((result) => {
        if (!cancelled) setDiagnosis(result);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause)));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [diagnosis, diagnosisId, setDiagnosis]);

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

  const runToConclusion = () =>
    guard(async () => {
      if (!diagnosis) return;
      setDiagnosis(await api.runDiagnosis(diagnosis.id));
    });

  const loadPreview = () =>
    guard(async () => {
      if (!diagnosis) return;
      setPreview(await api.reportPreview(diagnosis.id));
    });

  if (!diagnosis) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Evidence &amp; Diagnosis Report</h2>
        {error && <ErrorNotice error={error} onRetry={() => setError(null)} />}
        {diagnosisId && !error ? (
          <Loading label="Loading the stored diagnosis…" />
        ) : (
          <Card title="No diagnosis loaded">
            <EmptyState
              title="Nothing to report yet"
              description="Run a diagnosis in the Diagnostic Workbench, then return here for the full evidence report, the alternatives considered and the recommended verification step."
              action={
                <Link
                  to="/diagnose"
                  className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
                >
                  Open the Diagnostic Workbench
                </Link>
              }
            />
          </Card>
        )}
      </div>
    );
  }

  const report = diagnosis.report;
  const isTerminal = isTerminalStatus(diagnosis.status);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">
            Evidence &amp; Diagnosis Report — <code className="text-base">{diagnosis.id}</code>
          </h2>
          <p className="text-xs text-slate-600">
            {diagnosis.source_node_id} → {diagnosis.destination_node_id}
            {diagnosis.destination_service ? ` / ${diagnosis.destination_service}` : ""}
            {diagnosis.port ? ` (port ${diagnosis.port})` : ""} ·{" "}
            {diagnosis.probes_used}/{diagnosis.max_probes} probes
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ModeBadge mode={diagnosis.mode} />
          <StatusBadge status={diagnosis.status} />
          <a
            href={api.reportMarkdownUrl(diagnosis.id)}
            className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50"
          >
            <FileText size={14} aria-hidden="true" />
            Export Markdown
          </a>
          <a
            href={api.reportJsonUrl(diagnosis.id)}
            className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50"
          >
            <FileJson size={14} aria-hidden="true" />
            Export JSON
          </a>
          {!isTerminal && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void runToConclusion()}
              className="rounded bg-slate-900 px-2.5 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              Finish the diagnosis
            </button>
          )}
        </div>
      </div>

      {error && <ErrorNotice error={error} onRetry={() => setError(null)} />}

      {!isTerminal && (
        <div
          role="status"
          className="rounded border border-sky-300 bg-sky-50 px-3 py-2 text-sm text-sky-900"
        >
          This diagnosis is still <strong>{diagnosis.status}</strong>. The evidence below is
          partial; the conclusion is not final until the backend reports a terminal state.
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Status" value={diagnosis.status} hint={diagnosis.strategy} />
        <Metric
          label="Leading hypothesis"
          value={diagnosis.beliefs.leader.code}
          hint={pct(diagnosis.beliefs.leader.probability, 1)}
        />
        <Metric
          label="Belief entropy"
          value={`${num(diagnosis.beliefs.entropy_bits, 3)} bits`}
          hint={`lead ${pct(diagnosis.beliefs.lead_over_runner_up, 1)}`}
        />
        <Metric
          label="Suspected component"
          value={
            diagnosis.suspected_component.component_id
              ? diagnosis.suspected_component.component_id
              : "none identified"
          }
          hint={`location confidence: ${diagnosis.suspected_component.confidence}`}
        />
      </div>

      {report && (
        <Card title="Conclusion" subtitle={`Model ${report.model_version} · priors ${report.prior_config_version}`}>
          <p className="text-sm font-semibold text-slate-900">{report.headline}</p>
          <p className="mt-1 text-sm text-slate-700">{report.summary}</p>
          <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
            <strong>Uncertainty: </strong>
            {report.uncertainty_note}
          </p>
          <p className="mt-2 text-xs text-slate-700">
            <strong>Stopping rule: </strong>
            {diagnosis.stopping_reason}
          </p>

          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-green-800">
                Evidence supporting this hypothesis
              </h3>
              {report.supporting_evidence.length === 0 ? (
                <p className="mt-1 text-xs text-slate-600">
                  No probe produced evidence that specifically raises the leading hypothesis.
                </p>
              ) : (
                <ul className="mt-1 list-inside list-disc space-y-1 text-xs text-slate-800">
                  {report.supporting_evidence.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-700">
                Evidence weakening it
              </h3>
              {report.weakening_evidence.length === 0 ? (
                <p className="mt-1 text-xs text-slate-600">
                  No executed probe weakened the leading hypothesis.
                </p>
              ) : (
                <ul className="mt-1 list-inside list-disc space-y-1 text-xs text-slate-800">
                  {report.weakening_evidence.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-800">
                Not explained by it
              </h3>
              {report.unexplained.length === 0 ? (
                <p className="mt-1 text-xs text-slate-600">
                  The executed evidence is consistent with the leading hypothesis.
                </p>
              ) : (
                <ul className="mt-1 list-inside list-disc space-y-1 text-xs text-slate-800">
                  {report.unexplained.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="mt-3 rounded border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">
            <p>
              <strong>Recommended next step: </strong>
              {report.recommended_next_step}
            </p>
            {report.remediation.length > 0 && (
              <div className="mt-1">
                <strong>Documented remediation for this hypothesis: </strong>
                <ul className="ml-4 list-inside list-disc">
                  {report.remediation.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="mt-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
              Caveats and provenance
            </h3>
            <ul className="mt-1 list-inside list-disc space-y-0.5 text-[11px] text-slate-600">
              {report.caveats.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        </Card>
      )}

      <Card
        title="Hypothesis ranking with likelihood impact"
        subtitle="How each hypothesis moved, and by what factor the evidence changed it."
      >
        <ul className="space-y-2">
          {diagnosis.beliefs.ranked.map((hypothesis, index) => (
            <li key={hypothesis.code}>
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                <span>
                  <span className="mr-1.5 font-mono text-xs text-slate-500">#{index + 1}</span>
                  <code className="font-semibold">{hypothesis.code}</code>{" "}
                  <span className="text-xs text-slate-600">
                    {titles.get(hypothesis.code) ?? hypothesis.title}
                  </span>
                </span>
                <span className="text-xs text-slate-500">
                  prior {pct(hypothesis.prior, 1)} → posterior {pct(hypothesis.probability, 1)}
                </span>
              </div>
              <ProbabilityBar value={hypothesis.probability} label={hypothesis.code} emphasis={index === 0} />
            </li>
          ))}
        </ul>

        {report && report.contributions.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[620px] border-collapse text-xs">
              <caption className="sr-only">
                Likelihood impact of each observation on each hypothesis
              </caption>
              <thead>
                <tr className="border-b border-slate-300 text-left uppercase tracking-wide text-slate-600">
                  <th scope="col" className="py-1 pr-2">#</th>
                  <th scope="col" className="py-1 pr-2">Probe</th>
                  <th scope="col" className="py-1 pr-2">Outcome</th>
                  <th scope="col" className="py-1 pr-2">Info gained</th>
                  <th scope="col" className="py-1">Effect on the leading hypothesis</th>
                </tr>
              </thead>
              <tbody>
                {report.contributions.map((contribution) => {
                  const ratio =
                    contribution.likelihood_ratios[diagnosis.beliefs.leader.code] ?? 1;
                  return (
                    <tr key={contribution.sequence_number} className="border-b border-slate-100">
                      <td className="py-1 pr-2 tabular-nums">{contribution.sequence_number}</td>
                      <td className="py-1 pr-2">
                        <code>{contribution.probe_key}</code>
                      </td>
                      <td className="py-1 pr-2">
                        <code>{contribution.outcome}</code>
                      </td>
                      <td className="py-1 pr-2 tabular-nums">
                        {num(contribution.information_gained_bits, 3)} bits
                      </td>
                      <td className="py-1">
                        {ratio > 1 ? "raised" : ratio < 1 ? "lowered" : "unchanged"} ×
                        {num(ratio, 2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Evidence timeline"
        subtitle="Probe-by-probe record, in execution order, with structured details."
      >
        {diagnosis.steps.length === 0 ? (
          <p className="text-sm text-slate-600">No probe has been executed yet.</p>
        ) : (
          <ol className="space-y-3">
            {diagnosis.steps.map((step) => (
              <li key={step.sequence_number} className="rounded border border-slate-200 p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-sm font-medium">
                    {step.sequence_number}. {step.probe_label}
                  </span>
                  <span className="flex items-center gap-2 text-xs text-slate-600">
                    <ModeBadge mode={step.mode} />
                    <code className="rounded bg-slate-100 px-1.5 py-0.5">{step.outcome}</code>
                  </span>
                </div>
                <p className="mt-1 text-sm text-slate-700">{step.summary}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  {new Date(step.created_at).toLocaleString()} · modelled{" "}
                  {num(step.modelled_elapsed_ms, 1)} ms (simulated protocol time) · measured{" "}
                  {num(step.measured_wall_clock_ms, 2)} ms (probe computation) · entropy{" "}
                  {num(step.entropy_before_bits, 3)} → {num(step.entropy_after_bits, 3)} bits
                </p>

                {step.evidence.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {step.evidence.map((statement, index) => (
                      <li
                        key={index}
                        className="flex flex-wrap items-start gap-1.5 text-xs text-slate-800"
                      >
                        <KindTag kind={statement.kind} />
                        <StrengthTag strength={statement.strength} />
                        <span>
                          {statement.statement}
                          {statement.supports.length > 0 && (
                            <span className="ml-1 text-green-800">
                              → supports: {statement.supports.join(", ")}
                            </span>
                          )}
                          {statement.weakens.length > 0 && (
                            <span className="ml-1 text-red-800">
                              → weakens: {statement.weakens.join(", ")}
                            </span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] font-medium text-slate-600">
                    Structured details
                  </summary>
                  <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-[10px] leading-tight text-slate-700">
                    {JSON.stringify(step.details, null, 2)}
                  </pre>
                </details>
                <p className="mt-1 text-[11px] text-slate-500">
                  <strong>Selected because: </strong>
                  {step.selected_reason}
                </p>
              </li>
            ))}
          </ol>
        )}
      </Card>

      {session && diagnosis.suspected_component.component_id && (
        <Card
          title="Suspected component on the topology"
          subtitle={`${diagnosis.suspected_component.component_kind} ${diagnosis.suspected_component.component_id} · location confidence ${diagnosis.suspected_component.confidence}`}
        >
          <TopologyView
            topology={session.topology}
            suspectedComponentId={diagnosis.suspected_component.component_id}
            suspects={{
              links: new Set(
                diagnosis.suspected_component.component_kind === "link"
                  ? [diagnosis.suspected_component.component_id]
                  : [],
              ),
              nodes: new Set(
                diagnosis.suspected_component.component_kind === "node"
                  ? [diagnosis.suspected_component.component_id]
                  : [],
              ),
              services: new Set(
                diagnosis.suspected_component.component_kind === "service"
                  ? [diagnosis.suspected_component.component_id]
                  : [],
              ),
            }}
          />
          <ul className="mt-2 list-inside list-disc text-xs text-slate-700">
            {diagnosis.suspected_component.evidence.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Card>
      )}

      <Card
        title="Exported report"
        subtitle="Generated by the backend from the same stored records this page renders."
      >
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void loadPreview()}
            className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            <Download size={14} aria-hidden="true" />
            Preview the Markdown report
          </button>
          <a
            href={api.reportMarkdownUrl(diagnosis.id)}
            className="rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50"
          >
            Download .md
          </a>
          <a
            href={api.reportJsonUrl(diagnosis.id)}
            className="rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50"
          >
            Download .json
          </a>
        </div>
        {preview && (
          <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
            {preview}
          </pre>
        )}
      </Card>
    </div>
  );
}

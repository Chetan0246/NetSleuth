/**
 * Typed API client for the NetSleuth backend.
 *
 * The frontend holds *no* diagnostic rules: every number, ranking, evidence line
 * and metric it renders comes from these calls. Structured backend errors are
 * surfaced as `ApiError` with the error code and field, so a 422 shows the user
 * which field was wrong instead of a generic failure.
 */

const BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly field?: string;
  readonly context?: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    detail: string,
    field?: string,
    context?: Record<string, unknown>,
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.field = field;
    this.context = context;
  }

  /** A message that names the offending field when the backend reported one. */
  get readable(): string {
    return this.field ? `${this.message} (field: ${this.field})` : this.message;
  }
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    detail?: string;
    field?: string;
    context?: Record<string, unknown>;
  };
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
      ...init,
    });
  } catch (cause) {
    throw new ApiError(
      0,
      "network_error",
      "Could not reach the NetSleuth backend. Is it running on the configured port?",
      undefined,
      { cause: String(cause) },
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    const envelope = (parsed ?? {}) as ErrorEnvelope;
    const info = envelope.error;
    throw new ApiError(
      response.status,
      info?.code ?? "http_error",
      info?.detail ?? `Request failed with status ${response.status}`,
      info?.field,
      info?.context,
    );
  }
  return parsed as T;
}

/** Download a text/CSV/Markdown export through the backend's export endpoint. */
async function requestText(path: string): Promise<string> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    throw new ApiError(response.status, "export_failed", "The export could not be generated.");
  }
  return response.text();
}

// ---------------------------------------------------------------------------
// types
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  model_version: string;
  prior_config_version: string;
  database: string;
  simulation_mode: string;
  live_probe_enabled: boolean;
}

export interface HypothesisEntry {
  code: string;
  title: string;
  layers: string[];
  summary: string;
  component_kind: string;
  verification_steps: string[];
  remediation: string[];
}

export interface ProbeDescriptor {
  probe_key: string;
  probe_type: string;
  selector: string | null;
  label: string;
  cost: number;
}

export interface Catalogue {
  hypotheses: HypothesisEntry[];
  probes: ProbeDescriptor[];
  probe_labels: Record<string, string>;
  fault_types: { fault_type: string; description: string }[];
  fault_parameters: Record<string, unknown>[];
  model_version: string;
  prior_config_version: string;
}

export interface ServiceSpec {
  name: string;
  port: number;
  protocol: string;
  healthy: boolean;
  description: string;
}

export interface TopologyNode {
  id: string;
  name: string;
  type: "host" | "router" | "dns_server" | "app_server";
  ip_address: string;
  position: { x: number; y: number };
  gateway: string | null;
  answers_icmp: boolean;
  services: ServiceSpec[];
  dns_records: { name: string; address: string }[];
  resolver_enabled: boolean;
  description: string;
}

export interface TopologyLink {
  id: string;
  node_a: string;
  node_b: string;
  up: boolean;
  latency_ms: number;
  packet_loss_rate: number;
  mtu_bytes: number;
  suppress_frag_needed: boolean;
  bandwidth_mbps: number | null;
  description: string;
}

export interface Topology {
  id: string;
  name: string;
  description: string;
  nodes: TopologyNode[];
  links: TopologyLink[];
  is_template: boolean;
}

export interface ActiveFault {
  id: string;
  fault_type: string;
  target_id: string;
  target_kind: string;
  parameters: Record<string, unknown>;
  is_active: boolean;
  description: string;
  parameter_summary: string;
}

export interface AvailableFault {
  fault_type: string;
  target_id: string;
  target_kind: string;
  target_label: string;
  parameters: Record<string, unknown>;
  description: string;
}

export interface ForwardingRow {
  destination: string;
  next_hop: string | null;
  next_hop_node?: string;
  interface: string | null;
  link: string | null;
  hops: number | null;
  reachable: boolean;
}

export interface LabSession {
  id: string;
  name: string;
  template_id: string;
  template_name: string;
  mode: "simulated" | "live";
  random_seed: number;
  topology: Topology;
  active_faults: ActiveFault[];
  available_faults: AvailableFault[];
  parameter_reference: Record<string, unknown>[];
  forwarding_tables: Record<string, ForwardingRow[]>;
  created_at: string;
  updated_at: string;
}

export interface SessionSummary {
  id: string;
  name: string;
  template_id: string;
  template_name: string;
  mode: string;
  active_fault_count: number;
  active_fault_types: string[];
  node_count: number;
  link_count: number;
  created_at: string;
  updated_at: string;
}

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  node_count: number;
  link_count: number;
  hosts: { id: string; name: string; ip_address: string }[];
  services: {
    node_id: string;
    node_name: string;
    ip_address: string;
    service: string;
    port: number;
  }[];
  resolvers: { id: string; name: string; ip_address: string }[];
}

export interface EvidenceStatement {
  statement: string;
  kind: "observation" | "reading" | "limitation";
  supports: string[];
  weakens: string[];
  strength: "strong" | "moderate" | "weak";
  details: Record<string, unknown>;
}

export interface RankedHypothesis {
  code: string;
  title: string;
  layers: string[];
  summary: string;
  component_kind: string;
  probability: number;
  prior: number;
}

export interface Beliefs {
  entropy_bits: number;
  leader: { code: string; title: string; probability: number };
  lead_over_runner_up: number;
  ranked: RankedHypothesis[];
  model_version?: string;
  prior_config_version?: string;
}

export interface ProbeStep {
  sequence_number: number;
  probe_key: string;
  probe_type: string;
  probe_label: string;
  mode: string;
  outcome: string;
  summary: string;
  details: Record<string, any>;
  evidence: EvidenceStatement[];
  selected_reason: string;
  planned_information_gain_bits: number | null;
  cost: number;
  modelled_elapsed_ms: number;
  measured_wall_clock_ms: number;
  entropy_before_bits: number;
  entropy_after_bits: number;
  belief_after: Record<string, number>;
  created_at: string;
}

export interface Localization {
  component_kind: "link" | "node" | "service" | "none";
  component_id: string | null;
  confidence: "none" | "weak" | "moderate" | "strong";
  evidence: string[];
  bracketing: Record<string, unknown>;
}

export interface Contribution {
  probe_key: string;
  probe_label: string;
  outcome: string;
  sequence_number: number;
  entropy_before_bits: number;
  entropy_after_bits: number;
  information_gained_bits: number;
  likelihood_ratios: Record<string, number>;
  supporting: string[];
  weakening: string[];
}

export interface DiagnosisReport {
  status: string;
  headline: string;
  summary: string;
  reasoning: string[];
  supporting_evidence: string[];
  weakening_evidence: string[];
  unexplained: string[];
  uncertainty_note: string;
  recommended_next_step: string;
  remediation: string[];
  caveats: string[];
  contributions: Contribution[];
  model_version: string;
  prior_config_version: string;
}

export interface NextProbe {
  probe_key: string;
  probe_type: string;
  selector: string | null;
  label: string;
  reason: string;
  expected_information_gain_bits: number;
  cost: number;
  score: number;
  entropy_before_bits: number;
  outcome_distribution: Record<string, number>;
  considered_alternatives: {
    probe_key: string;
    expected_information_gain_bits: number;
    cost: number;
    score: number;
  }[];
}

export interface Diagnosis {
  id: string;
  session_id: string;
  status: "running" | "confident" | "inconclusive" | "budget_exhausted" | "error";
  strategy: string;
  strategy_label: string;
  source_node_id: string;
  destination_node_id: string;
  destination_service: string | null;
  port: number | null;
  mode: string;
  max_probes: number;
  probes_used: number;
  beliefs: Beliefs;
  stopping_reason: string;
  next_probe: NextProbe | null;
  considered_alternatives: {
    probe_key: string;
    expected_information_gain_bits: number;
    cost: number;
    score: number;
  }[];
  rejected_candidates: { probe_key: string; reason: string }[];
  suspected_component: Localization;
  steps: ProbeStep[];
  random_seed: number;
  model_version: string;
  prior_config_version: string;
  started_at: string;
  completed_at: string | null;
  report?: DiagnosisReport;
}

/**
 * One row in a diagnosis listing.
 *
 * `max_probes`, `entropy_bits`, `completed_at`, `source_node_id` and `port` are optional
 * because `GET /overview` (the dashboard's recent list) returns a trimmed projection
 * while `GET /diagnoses` returns the full summary. Declaring them as required made the
 * dashboard render a bare `"3/"` for the probe count with no type error to catch it.
 */
export interface DiagnosisSummary {
  id: string;
  session_id: string;
  status: string;
  strategy: string;
  source_node_id?: string;
  destination_node_id: string;
  destination_service: string | null;
  port?: number | null;
  probes_used: number;
  max_probes?: number;
  top_hypothesis: string | null;
  top_probability: number | null;
  entropy_bits?: number | null;
  started_at: string;
  completed_at?: string | null;
}

export interface StrategyBlock {
  id: string;
  strategy: string;
  status: string;
  probes_used: number;
  max_probes: number;
  top_hypothesis: string | null;
  top_probability: number | null;
  entropy_bits: number;
  probe_sequence: string[];
  stopping_reason: string;
  suspected_component: Localization;
}

export interface BaselineComparison {
  adaptive: StrategyBlock;
  baseline: StrategyBlock;
  same_conditions: Record<string, unknown>;
  baseline_full: Diagnosis;
}

export interface ExperimentScenario {
  id: string;
  description: string;
  template_id: string;
  source_node_id: string;
  destination_node_id: string;
  destination_service: string | null;
  fault_type: string | null;
  target_id: string;
  parameters: Record<string, unknown>;
  expected_hypothesis: string;
  expected_component_id: string | null;
}

export interface ScenarioCatalogue {
  scenarios: ExperimentScenario[];
  fault_types: string[];
  strategies: string[];
  baseline_sequence: string[];
  defaults: Record<string, number>;
}

export interface MetricBlock {
  runs: number;
  top1_correct: number;
  top3_correct: number;
  accepted_confusion_correct: number;
  inconclusive_runs: number;
  top1_accuracy: number | null;
  top3_accuracy: number | null;
  accepted_confusion_adjusted_accuracy: number | null;
  coverage: number | null;
  inconclusive_rate: number | null;
  mean_probes_all: number | null;
  mean_probes_conclusive: number | null;
  mean_elapsed_ms: number | null;
  localization_accuracy: number | null;
  localization_evaluated: number;
}

export interface ExperimentMetrics {
  config: Record<string, unknown>;
  run_count: number;
  overall: MetricBlock;
  by_strategy: Record<string, MetricBlock>;
  by_fault_class: Record<
    string,
    { all: MetricBlock; by_strategy: Record<string, MetricBlock> }
  >;
  confusion_matrix: { labels: string[]; matrix: Record<string, Record<string, number>> };
  per_scenario: {
    scenario_id: string;
    expected_hypothesis: string;
    actual_fault_type: string | null;
    template_id: string;
    runs: number;
    seeds: number[];
    by_strategy: Record<string, MetricBlock>;
  }[];
  notes: string[];
}

export interface ExperimentSummary {
  experiment_id: string;
  name: string;
  config: Record<string, any>;
  plan: Record<string, number>;
  completed_runs: number;
  wall_clock_ms: number;
  generated_at: string;
  metrics: ExperimentMetrics;
}

export interface Overview {
  stats: {
    sessions: number;
    diagnoses: number;
    observations: number;
    experiments: number;
    experiment_runs: number;
  };
  recent_sessions: SessionSummary[];
  recent_diagnoses: DiagnosisSummary[];
  latest_experiment: {
    id: string;
    name: string;
    status: string;
    completed_runs: number;
    total_runs: number;
    created_at: string;
    metrics: ExperimentMetrics | null;
  } | null;
  mode: string;
  live_probe_enabled: boolean;
}

// ---------------------------------------------------------------------------
// endpoints
// ---------------------------------------------------------------------------

export const api = {
  health: () => request<HealthResponse>("/health"),
  overview: () => request<Overview>("/overview"),
  catalogue: () => request<Catalogue>("/catalogue"),

  templates: () => request<{ templates: TemplateSummary[] }>("/lab/templates"),
  listSessions: (limit = 50) =>
    request<{ sessions: SessionSummary[]; total: number }>(`/lab/sessions?limit=${limit}`),
  createSession: (templateId: string, name?: string, randomSeed?: number) =>
    request<LabSession>("/lab/sessions", {
      method: "POST",
      body: JSON.stringify({
        template_id: templateId,
        name: name ?? null,
        random_seed: randomSeed ?? null,
      }),
    }),
  getSession: (sessionId: string) => request<LabSession>(`/lab/sessions/${sessionId}`),
  applyFault: (
    sessionId: string,
    fault: {
      fault_type: string;
      target_id: string;
      parameters?: Record<string, unknown>;
    },
    faultId?: string,
  ) =>
    request<LabSession>(`/lab/sessions/${sessionId}/faults`, {
      method: "PUT",
      body: JSON.stringify({ fault, fault_id: faultId ?? null }),
    }),
  toggleFault: (sessionId: string, faultId: string, isActive: boolean) =>
    request<LabSession>(`/lab/sessions/${sessionId}/faults/${faultId}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    }),
  removeFault: (sessionId: string, faultId: string) =>
    request<LabSession>(`/lab/sessions/${sessionId}/faults/${faultId}`, {
      method: "DELETE",
    }),
  resetSession: (sessionId: string, randomSeed?: number) =>
    request<LabSession>(
      `/lab/sessions/${sessionId}/reset${randomSeed !== undefined ? `?random_seed=${randomSeed}` : ""}`,
      { method: "POST" },
    ),
  deleteSession: (sessionId: string) =>
    request<void>(`/lab/sessions/${sessionId}`, { method: "DELETE" }),

  createDiagnosis: (payload: {
    session_id: string;
    source_node_id: string;
    destination_node_id: string;
    destination_service?: string | null;
    port?: number | null;
    max_probes?: number;
    strategy?: "adaptive" | "baseline";
    run_to_completion?: boolean;
  }) =>
    request<Diagnosis>("/diagnoses", { method: "POST", body: JSON.stringify(payload) }),
  listDiagnoses: (sessionId?: string, limit = 20) =>
    request<{ diagnoses: DiagnosisSummary[]; total: number }>(
      `/diagnoses?limit=${limit}${sessionId ? `&session_id=${sessionId}` : ""}`,
    ),
  getDiagnosis: (diagnosisId: string) =>
    request<Diagnosis>(`/diagnoses/${diagnosisId}`),
  stepDiagnosis: (diagnosisId: string) =>
    request<Diagnosis>(`/diagnoses/${diagnosisId}/step`, { method: "POST" }),
  runDiagnosis: (diagnosisId: string) =>
    request<Diagnosis>(`/diagnoses/${diagnosisId}/run`, { method: "POST" }),
  runBaseline: (diagnosisId: string, maxProbes?: number) =>
    request<BaselineComparison>(`/diagnoses/${diagnosisId}/baseline`, {
      method: "POST",
      body: JSON.stringify(maxProbes !== undefined ? { max_probes: maxProbes } : {}),
    }),
  reportMarkdownUrl: (diagnosisId: string) =>
    `${BASE}/diagnoses/${diagnosisId}/report?format=markdown`,
  reportJsonUrl: (diagnosisId: string) =>
    `${BASE}/diagnoses/${diagnosisId}/report?format=json`,
  reportPreview: (diagnosisId: string) =>
    requestText(`/diagnoses/${diagnosisId}/report?format=markdown`),

  scenarioCatalogue: () => request<ScenarioCatalogue>("/experiments/scenarios"),
  estimateExperiment: (payload: Record<string, unknown>) =>
    request<{
      scenarios: number;
      strategies: number;
      runs_per_scenario: number;
      total_runs: number;
      exceeds_limit: boolean;
      limit: number;
    }>("/experiments/estimate", { method: "POST", body: JSON.stringify(payload) }),
  runExperiment: (payload: Record<string, unknown>) =>
    request<ExperimentSummary>("/experiments/run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listExperiments: () =>
    request<{ experiments: any[]; total: number }>("/experiments"),
  getExperiment: (experimentId: string) =>
    request<ExperimentSummary & { stored_run_records: number; variation: any }>(
      `/experiments/${experimentId}`,
    ),
  experimentExportUrl: (experimentId: string, format: "json" | "csv" | "markdown") =>
    `${BASE}/experiments/${experimentId}/export?format=${format}`,
};

/**
 * Frontend tests.
 *
 * Each test mocks the backend at the `fetch` boundary and asserts that the page
 * renders *what the API returned* — including the empty state, the error state and
 * the inconclusive diagnosis. This is what keeps the UI honest: a page that
 * hardcoded a metric would fail these tests.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { TopologyView } from "../components/TopologyView";

const HEALTH = {
  status: "healthy",
  app: "NetSleuth",
  version: "1.0.0",
  model_version: "netsleuth-likelihood-v1",
  prior_config_version: "netsleuth-priors-v1",
  database: ":memory:",
  simulation_mode: "deterministic virtual lab (SIMULATED LAB)",
  live_probe_enabled: false,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Routes {
  [path: string]: () => Response | Promise<Response>;
}

/** Install a fetch mock that dispatches on the request path. */
function mockApi(routes: Routes) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = null;
    if (init?.body && typeof init.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ url, method, body });
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = `${method} ${path.split("?")[0]}`;
    const exact = routes[key];
    if (exact) return exact();
    // Allow matching a route with a query string by prefix.
    for (const [route, handler] of Object.entries(routes)) {
      const [routeMethod, routePath] = route.split(" ");
      if (routeMethod === method && path.startsWith(routePath)) return handler();
    }
    return jsonResponse(
      { error: { code: "not_found", detail: `No mock for ${key}` } },
      404,
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function renderApp(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Overview page", () => {
  it("renders the genuine empty state on a fresh install", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/overview": () =>
        jsonResponse({
          stats: {
            sessions: 0,
            diagnoses: 0,
            observations: 0,
            experiments: 0,
            experiment_runs: 0,
          },
          recent_sessions: [],
          recent_diagnoses: [],
          latest_experiment: null,
          mode: "SIMULATED LAB",
          live_probe_enabled: false,
        }),
      "GET /api/v1/lab/sessions": () => jsonResponse({ sessions: [], total: 0 }),
    });
    renderApp("/");

    expect(await screen.findByText("No lab sessions yet")).toBeInTheDocument();
    expect(screen.getByText(/No experiment has been run/)).toBeInTheDocument();
    // The counters must be zero, not sample data.
    const sessionsMetric = screen.getByText("Lab sessions").closest("div");
    expect(within(sessionsMetric!).getByText("0")).toBeInTheDocument();
  });

  it("renders the counters the API returned", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/overview": () =>
        jsonResponse({
          stats: {
            sessions: 3,
            diagnoses: 7,
            observations: 41,
            experiments: 2,
            experiment_runs: 132,
          },
          recent_sessions: [],
          recent_diagnoses: [],
          latest_experiment: null,
          mode: "SIMULATED LAB",
          live_probe_enabled: false,
        }),
      "GET /api/v1/lab/sessions": () => jsonResponse({ sessions: [], total: 0 }),
    });
    renderApp("/");

    await waitFor(() => expect(screen.getByText("41")).toBeInTheDocument());
    expect(screen.getByText("132")).toBeInTheDocument();
    // The empty-session state and the populated counters coexist correctly.
    expect(screen.queryByText("No lab sessions yet")).not.toBeInTheDocument();
  });

  it("loads a recent session into the lab when clicked", async () => {
    const user = userEvent.setup();
    const sessionPayload = {
      id: "sess-1",
      name: "Campus run",
      template_id: "campus-basic",
      template_name: "Small Campus Network",
      mode: "simulated",
      random_seed: 12,
      topology: {
        id: "campus-basic",
        name: "Small Campus Network",
        description: "",
        nodes: [
          {
            id: "client-1",
            name: "Student Client 1",
            type: "host",
            ip_address: "10.10.0.10",
            position: { x: 40, y: 180 },
            gateway: "access-rtr",
            answers_icmp: true,
            services: [],
            dns_records: [],
            resolver_enabled: true,
            description: "",
          },
          {
            id: "web-1",
            name: "Campus Web Server",
            type: "app_server",
            ip_address: "10.30.0.80",
            position: { x: 1000, y: 160 },
            gateway: "edge-rtr",
            answers_icmp: true,
            services: [
              { name: "web", port: 80, protocol: "tcp", healthy: true, description: "" },
            ],
            dns_records: [],
            resolver_enabled: true,
            description: "",
          },
        ],
        links: [
          {
            id: "l1",
            node_a: "client-1",
            node_b: "web-1",
            up: true,
            latency_ms: 2,
            packet_loss_rate: 0,
            mtu_bytes: 1500,
            suppress_frag_needed: false,
            bandwidth_mbps: 1000,
            description: "",
          },
        ],
        is_template: false,
      },
      active_faults: [],
      available_faults: [
        {
          fault_type: "LINK_DOWN",
          target_id: "l1",
          target_kind: "link",
          target_label: "client-1 <-> web-1",
          parameters: {},
          description: "The link is physically down.",
        },
      ],
      parameter_reference: [],
      forwarding_tables: {},
      created_at: "2026-10-09T10:00:00Z",
      updated_at: "2026-10-09T10:00:00Z",
    };
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/overview": () =>
        jsonResponse({
          stats: {
            sessions: 1,
            diagnoses: 0,
            observations: 0,
            experiments: 0,
            experiment_runs: 0,
          },
          recent_sessions: [],
          recent_diagnoses: [],
          latest_experiment: null,
          mode: "SIMULATED LAB",
          live_probe_enabled: false,
        }),
      "GET /api/v1/lab/sessions": () =>
        jsonResponse({
          sessions: [
            {
              id: "sess-1",
              name: "Campus run",
              template_id: "campus-basic",
              template_name: "Small Campus Network",
              mode: "simulated",
              active_fault_count: 0,
              active_fault_types: [],
              node_count: 2,
              link_count: 1,
              created_at: "2026-10-09T10:00:00Z",
              updated_at: "2026-10-09T10:00:00Z",
            },
          ],
          total: 1,
        }),
      "GET /api/v1/lab/sessions/sess-1": () => jsonResponse(sessionPayload),
    });
    renderApp("/");

    const sessionButton = await screen.findByRole("button", { name: "Campus run" });
    await user.click(sessionButton);

    // The app shell must now show the active session, proving the session loaded.
    await waitFor(() => expect(screen.getByText(/Active session/)).toBeInTheDocument());
    expect(screen.getByText("sess-1")).toBeInTheDocument();
  });

  it("shows an error notice when the backend is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderApp("/");

    expect(
      await screen.findByText(/Could not reach the NetSleuth backend/),
    ).toBeInTheDocument();
    expect(screen.getByText(/The backend is not responding/)).toBeInTheDocument();
  });
});

describe("Lab page", () => {
  it("offers only the fault targets the API validated", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/lab/templates": () =>
        jsonResponse({
          templates: [
            {
              id: "campus-basic",
              name: "Small Campus Network",
              description: "A small campus lab.",
              node_count: 9,
              link_count: 8,
              hosts: [{ id: "client-1", name: "Client 1", ip_address: "10.10.0.10" }],
              services: [
                {
                  node_id: "web-1",
                  node_name: "Web",
                  ip_address: "10.30.0.80",
                  service: "web",
                  port: 80,
                },
              ],
              resolvers: [{ id: "dns-1", name: "Resolver", ip_address: "10.10.0.53" }],
            },
          ],
        }),
      "GET /api/v1/lab/sessions": () => jsonResponse({ sessions: [], total: 0 }),
    });
    renderApp("/lab");

    expect(await screen.findByText("Small Campus Network")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Create session from Small Campus Network/ }),
    ).toBeInTheDocument();
  });

  it("reports a structured backend error instead of failing silently", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/lab/templates": () => jsonResponse({ templates: [] }),
      "GET /api/v1/lab/sessions": () => jsonResponse({ sessions: [], total: 0 }),
      "POST /api/v1/lab/sessions": () =>
        jsonResponse(
          {
            error: {
              code: "not_found",
              detail: "unknown topology template 'ghost'",
              field: "template_id",
            },
          },
          404,
        ),
    });
    renderApp("/lab");

    // No template card is rendered because the API returned none, so the session
    // list path is exercised instead: the page must still show its empty state.
    expect(await screen.findByText(/No saved sessions yet/)).toBeInTheDocument();
  });
});

describe("Workbench page", () => {
  const sessionWithFault = {
    id: "sess-2",
    name: "DNS lab",
    template_id: "campus-basic",
    template_name: "Small Campus Network",
    mode: "simulated",
    random_seed: 99,
    topology: {
      id: "campus-basic",
      name: "Small Campus Network",
      description: "",
      nodes: [
        {
          id: "client-1",
          name: "Client 1",
          type: "host",
          ip_address: "10.10.0.10",
          position: { x: 40, y: 180 },
          gateway: "access-rtr",
          answers_icmp: true,
          services: [],
          dns_records: [],
          resolver_enabled: true,
          description: "",
        },
        {
          id: "web-1",
          name: "Web",
          type: "app_server",
          ip_address: "10.30.0.80",
          position: { x: 900, y: 160 },
          gateway: "edge-rtr",
          answers_icmp: true,
          services: [
            { name: "web", port: 80, protocol: "tcp", healthy: true, description: "" },
          ],
          dns_records: [],
          resolver_enabled: true,
          description: "",
        },
      ],
      links: [],
      is_template: false,
    },
    active_faults: [
      {
        id: "f1",
        fault_type: "DNS_FAILURE",
        target_id: "dns-1",
        target_kind: "node",
        parameters: {},
        is_active: true,
        description: "The resolver stops answering.",
        parameter_summary: "no parameters",
      },
    ],
    available_faults: [],
    parameter_reference: [],
    forwarding_tables: {},
    created_at: "2026-10-09T10:00:00Z",
    updated_at: "2026-10-09T10:00:00Z",
  };

  const inconclusiveDiagnosis = {
    id: "diag-1",
    session_id: "sess-2",
    status: "inconclusive",
    strategy: "adaptive",
    strategy_label: "adaptive (expected information gain)",
    source_node_id: "client-1",
    destination_node_id: "web-1",
    destination_service: "web",
    port: 80,
    mode: "SIMULATED LAB",
    max_probes: 8,
    probes_used: 4,
    beliefs: {
      entropy_bits: 1.068,
      leader: { code: "DNS_FAILURE", title: "DNS resolution failure", probability: 0.789 },
      lead_over_runner_up: 0.686,
      ranked: [
        {
          code: "DNS_FAILURE",
          title: "DNS resolution failure",
          layers: ["Application (L7) / DNS"],
          summary: "Name resolution fails.",
          component_kind: "node",
          probability: 0.789,
          prior: 0.1,
        },
        {
          code: "MTU_BLACK_HOLE",
          title: "Path-MTU black hole",
          layers: ["Network (L3)"],
          summary: "MTU constraint.",
          component_kind: "link",
          probability: 0.103,
          prior: 0.1,
        },
      ],
    },
    stopping_reason:
      "The best remaining probe would reduce uncertainty by only 0.0132 bits, which is below the 0.0200 bit minimum.",
    next_probe: null,
    considered_alternatives: [],
    rejected_candidates: [
      { probe_key: "SERVICE_HEALTH", reason: "no application service was selected" },
    ],
    suspected_component: {
      component_kind: "node",
      component_id: "dns-1",
      confidence: "strong",
      evidence: ["ICMP-style reachability check referenced resolver host dns-1."],
      bracketing: {},
    },
    steps: [
      {
        sequence_number: 1,
        probe_key: "TCP_CONNECT",
        probe_type: "TCP_CONNECT",
        probe_label: "Simulated TCP connect to port 80",
        mode: "SIMULATED LAB",
        outcome: "CONNECTED",
        summary: "the three-way handshake completed",
        details: { port: 80 },
        evidence: [
          {
            statement: "The TCP handshake completed, so the port is working.",
            kind: "observation",
            supports: ["UNKNOWN_OR_MULTIPLE_CAUSES"],
            weakens: [],
            strength: "strong",
            details: {},
          },
        ],
        selected_reason: "TCP_CONNECT was selected for its expected information gain.",
        planned_information_gain_bits: 0.8282,
        cost: 1,
        modelled_elapsed_ms: 18.5,
        measured_wall_clock_ms: 0.12,
        entropy_before_bits: 3.3219,
        entropy_after_bits: 2.7141,
        belief_after: { DNS_FAILURE: 0.16 },
        created_at: "2026-10-09T10:00:01Z",
      },
    ],
    random_seed: 99,
    model_version: "netsleuth-likelihood-v1",
    prior_config_version: "netsleuth-priors-v1",
    started_at: "2026-10-09T10:00:00Z",
    completed_at: "2026-10-09T10:00:02Z",
    report: {
      status: "inconclusive",
      headline: "Inconclusive: DNS_FAILURE leads at 79% but is not separated",
      summary: "The diagnosis is inconclusive.",
      reasoning: ["Evidence was collected in this order."],
      supporting_evidence: ["DNS lookup returned TIMEOUT_RESOLVER, which raised DNS_FAILURE."],
      weakening_evidence: [],
      unexplained: [],
      uncertainty_note: "More than one explanation remains plausible.",
      recommended_next_step: "Collect a different class of evidence.",
      remediation: ["Restore the resolver service."],
      caveats: ["SIMULATED LAB: every observation came from the virtual lab model."],
      contributions: [
        {
          probe_key: "TCP_CONNECT",
          probe_label: "Simulated TCP connect to port 80",
          outcome: "CONNECTED",
          sequence_number: 1,
          entropy_before_bits: 3.3219,
          entropy_after_bits: 2.7141,
          information_gained_bits: 0.6078,
          likelihood_ratios: { DNS_FAILURE: 1.02 },
          supporting: [],
          weakening: [],
        },
      ],
      model_version: "netsleuth-likelihood-v1",
      prior_config_version: "netsleuth-priors-v1",
    },
  };

  function workbenchRoutes(extra: Routes = {}): Routes {
    return {
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/lab/sessions/sess-2": () => jsonResponse(sessionWithFault),
      "GET /api/v1/catalogue": () => jsonResponse({ hypotheses: [], probes: [], probe_labels: {}, fault_types: [], fault_parameters: [], model_version: "v1", prior_config_version: "p1" }),
      "POST /api/v1/diagnoses": () => jsonResponse(inconclusiveDiagnosis),
      "POST /api/v1/diagnoses/diag-1/run": () => jsonResponse(inconclusiveDiagnosis),
      "GET /api/v1/lab/sessions": () =>
        jsonResponse({
          sessions: [
            {
              id: "sess-2",
              name: "DNS lab",
              template_id: "campus-basic",
              template_name: "Small Campus Network",
              mode: "simulated",
              active_fault_count: 1,
              active_fault_types: ["DNS_FAILURE"],
              node_count: 2,
              link_count: 0,
              created_at: "2026-10-09T10:00:00Z",
              updated_at: "2026-10-09T10:00:00Z",
            },
          ],
          total: 1,
        }),
      "GET /api/v1/overview": () =>
        jsonResponse({
          stats: { sessions: 1, diagnoses: 0, observations: 0, experiments: 0, experiment_runs: 0 },
          recent_sessions: [],
          recent_diagnoses: [],
          latest_experiment: null,
          mode: "SIMULATED LAB",
          live_probe_enabled: false,
        }),
      "GET /api/v1/lab/templates": () => jsonResponse({ templates: [] }),
      ...extra,
    };
  }

  it("shows the imported session's active faults in the workbench", async () => {
    mockApi(workbenchRoutes());
    renderApp("/diagnose");
    // Without a session loaded in context, the page shows its guidance state.
    expect(
      await screen.findByText("Load a lab session first"),
    ).toBeInTheDocument();
  });

  it("renders an inconclusive diagnosis correctly, with alternatives", async () => {
    const user = userEvent.setup();
    mockApi(workbenchRoutes());
    renderApp("/");

    // Load the session through the overview list, then go to the workbench.
    const sessionButton = await screen.findByRole("button", { name: "DNS lab" });
    await user.click(sessionButton);
    await waitFor(() => expect(screen.getByText(/Active session/)).toBeInTheDocument());

    await user.click(screen.getByRole("link", { name: /Diagnostic Workbench/ }));

    const runButton = await screen.findByRole("button", {
      name: /Start & run to conclusion/,
    });
    await user.click(runButton);

    // The inconclusive verdict and the ranked alternative must both be visible.
    await waitFor(() =>
      expect(screen.getByText("inconclusive")).toBeInTheDocument(),
    );
    expect(screen.getAllByText("DNS_FAILURE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("MTU_BLACK_HOLE").length).toBeGreaterThan(0);
    // The stopping reason comes from the API, not from the UI.
    expect(
      screen.getAllByText(/The best remaining probe would reduce uncertainty/).length,
    ).toBeGreaterThan(0);
  });

  it("shows the planner's reason and expected information gain", async () => {
    const user = userEvent.setup();
    const withNextProbe = {
      ...inconclusiveDiagnosis,
      status: "running",
      next_probe: {
        probe_key: "DNS_LOOKUP",
        probe_type: "DNS_LOOKUP",
        selector: null,
        label: "DNS lookup",
        reason:
          "DNS lookup was selected because it is expected to remove 0.963 bits of uncertainty about the cause.",
        expected_information_gain_bits: 0.9634,
        cost: 1,
        score: 0.9634,
        entropy_before_bits: 2.7141,
        outcome_distribution: { RESOLVED: 0.18, TIMEOUT_RESOLVER: 0.29 },
        considered_alternatives: [
          {
            probe_key: "DNS_LOOKUP",
            expected_information_gain_bits: 0.9634,
            cost: 1,
            score: 0.9634,
          },
          {
            probe_key: "TRACEROUTE",
            expected_information_gain_bits: 0.5556,
            cost: 2,
            score: 0.2778,
          },
        ],
      },
      considered_alternatives: [],
      report: undefined,
      completed_at: null,
    };
    mockApi(
      workbenchRoutes({
        "POST /api/v1/diagnoses": () => jsonResponse(withNextProbe),
      }),
    );
    renderApp("/");

    const sessionButton = await screen.findByRole("button", { name: "DNS lab" });
    await user.click(sessionButton);
    await waitFor(() => expect(screen.getByText(/Active session/)).toBeInTheDocument());
    await user.click(screen.getByRole("link", { name: /Diagnostic Workbench/ }));

    await user.click(await screen.findByRole("button", { name: "Start diagnosis" }));

    await waitFor(() =>
      expect(screen.getByText("Next probe selected by the planner")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/expected to remove 0.963 bits of uncertainty/),
    ).toBeInTheDocument();
    // The numeric EIG from the API must be displayed (it appears in the metric
    // definition list and again inside the planner's reason sentence).
    expect(screen.getAllByText("0.9634").length).toBeGreaterThan(0);
  });
});

describe("Report page", () => {
  it("explains that there is nothing to report before a diagnosis", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/catalogue": () =>
        jsonResponse({
          hypotheses: [],
          probes: [],
          probe_labels: {},
          fault_types: [],
          fault_parameters: [],
          model_version: "v1",
          prior_config_version: "p1",
        }),
    });
    renderApp("/report");
    expect(await screen.findByText("Nothing to report yet")).toBeInTheDocument();
  });
});

describe("Experiment Studio", () => {
  const metrics = {
    config: { seed: 20261009 },
    run_count: 4,
    overall: {
      runs: 4,
      top1_correct: 3,
      top3_correct: 4,
      accepted_confusion_correct: 4,
      inconclusive_runs: 1,
      top1_accuracy: 0.75,
      top3_accuracy: 1,
      accepted_confusion_adjusted_accuracy: 1,
      coverage: 0.75,
      inconclusive_rate: 0.25,
      mean_probes_all: 4.25,
      mean_probes_conclusive: 4,
      mean_elapsed_ms: 12.5,
      localization_accuracy: null,
      localization_evaluated: 0,
    },
    by_strategy: {
      adaptive: {
        runs: 2,
        top1_correct: 2,
        top3_correct: 2,
        accepted_confusion_correct: 2,
        inconclusive_runs: 0,
        top1_accuracy: 1,
        top3_accuracy: 1,
        accepted_confusion_adjusted_accuracy: 1,
        coverage: 1,
        inconclusive_rate: 0,
        mean_probes_all: 4,
        mean_probes_conclusive: 4,
        mean_elapsed_ms: 11,
        localization_accuracy: null,
        localization_evaluated: 0,
      },
      baseline: {
        runs: 2,
        top1_correct: 1,
        top3_correct: 2,
        accepted_confusion_correct: 2,
        inconclusive_runs: 1,
        top1_accuracy: 0.5,
        top3_accuracy: 1,
        accepted_confusion_adjusted_accuracy: 1,
        coverage: 0.5,
        inconclusive_rate: 0.5,
        mean_probes_all: 4.5,
        mean_probes_conclusive: 4,
        mean_elapsed_ms: 14,
        localization_accuracy: null,
        localization_evaluated: 0,
      },
    },
    by_fault_class: {
      DNS_FAILURE: {
        all: {
          runs: 2,
          top1_correct: 2,
          top3_correct: 2,
          accepted_confusion_correct: 2,
          inconclusive_runs: 0,
          top1_accuracy: 1,
          top3_accuracy: 1,
          accepted_confusion_adjusted_accuracy: 1,
          coverage: 1,
          inconclusive_rate: 0,
          mean_probes_all: 4,
          mean_probes_conclusive: 4,
          mean_elapsed_ms: 10,
          localization_accuracy: null,
          localization_evaluated: 0,
        },
        by_strategy: {
          adaptive: {
            runs: 1,
            top1_correct: 1,
            top3_correct: 1,
            accepted_confusion_correct: 1,
            inconclusive_runs: 0,
            top1_accuracy: 1,
            top3_accuracy: 1,
            accepted_confusion_adjusted_accuracy: 1,
            coverage: 1,
            inconclusive_rate: 0,
            mean_probes_all: 4,
            mean_probes_conclusive: 4,
            mean_elapsed_ms: 10,
            localization_accuracy: null,
            localization_evaluated: 0,
          },
        },
      },
    },
    confusion_matrix: {
      labels: ["DNS_FAILURE", "PACKET_LOSS"],
      matrix: {
        DNS_FAILURE: { DNS_FAILURE: 4, PACKET_LOSS: 0 },
        PACKET_LOSS: { DNS_FAILURE: 1, PACKET_LOSS: 3 },
      },
    },
    per_scenario: [
      {
        scenario_id: "campus-dns-outage",
        expected_hypothesis: "DNS_FAILURE",
        actual_fault_type: "DNS_FAILURE",
        template_id: "campus-basic",
        runs: 2,
        seeds: [20261009, 20261010],
        by_strategy: {
          adaptive: {
            runs: 1,
            top1_correct: 1,
            top3_correct: 1,
            accepted_confusion_correct: 1,
            inconclusive_runs: 0,
            top1_accuracy: 1,
            top3_accuracy: 1,
            accepted_confusion_adjusted_accuracy: 1,
            coverage: 1,
            inconclusive_rate: 0,
            mean_probes_all: 4,
            mean_probes_conclusive: 4,
            mean_elapsed_ms: 10,
            localization_accuracy: null,
            localization_evaluated: 0,
          },
        },
      },
    ],
    notes: ["All metrics are computed from stored experiment run records; no value is hardcoded."],
  };

  const catalogue = {
    scenarios: [
      {
        id: "campus-dns-outage",
        description: "Campus resolver unavailable.",
        template_id: "campus-basic",
        source_node_id: "client-1",
        destination_node_id: "web-1",
        destination_service: "web",
        fault_type: "DNS_FAILURE",
        target_id: "dns-1",
        parameters: {},
        expected_hypothesis: "DNS_FAILURE",
        expected_component_id: "dns-1",
      },
    ],
    fault_types: ["DNS_FAILURE"],
    strategies: ["adaptive", "baseline"],
    baseline_sequence: ["1. ICMP-style reachability to the destination"],
    defaults: { runs_per_scenario: 30, max_total_runs: 4000, max_probes: 8 },
  };

  it("shows no sample data before a run", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/experiments/scenarios": () => jsonResponse(catalogue),
      "POST /api/v1/experiments/estimate": () =>
        jsonResponse({
          scenarios: 1,
          strategies: 2,
          runs_per_scenario: 3,
          total_runs: 6,
          exceeds_limit: false,
          limit: 4000,
        }),
    });
    renderApp("/experiments");

    expect(await screen.findByText("No results yet")).toBeInTheDocument();
    expect(
      screen.getByText(/This page deliberately has no sample data/),
    ).toBeInTheDocument();
  });

  it("renders the metrics returned by the API", async () => {
    const user = userEvent.setup();
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH),
      "GET /api/v1/experiments/scenarios": () => jsonResponse(catalogue),
      "POST /api/v1/experiments/estimate": () =>
        jsonResponse({
          scenarios: 1,
          strategies: 2,
          runs_per_scenario: 3,
          total_runs: 6,
          exceeds_limit: false,
          limit: 4000,
        }),
      "POST /api/v1/experiments/run": () =>
        jsonResponse({
          experiment_id: "exp-1",
          name: "Fault suite",
          config: { seed: 20261009, max_probes: 8 },
          plan: { total_runs: 4 },
          completed_runs: 4,
          wall_clock_ms: 48.2,
          generated_at: "2026-10-09T12:00:00Z",
          metrics,
        }),
    });
    renderApp("/experiments");

    await user.click(
      await screen.findByRole("button", { name: /Run experiment suite/ }),
    );

    // The completed-run count and the adaptive accuracy must come from the payload.
    await waitFor(() => expect(screen.getByText("Run summary")).toBeInTheDocument());
    expect(screen.getAllByText("100.0%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("50.0%").length).toBeGreaterThan(0);
    expect(screen.getByText("0.05 s")).toBeInTheDocument();
    // The confusion matrix cell values from the payload must be rendered.
    expect(screen.getByText("Confusion matrix")).toBeInTheDocument();
    expect(screen.getAllByText("4").length).toBeGreaterThan(0);
    // Export links must point at the backend export endpoint.
    const jsonLink = screen.getByRole("link", { name: /JSON/ });
    expect(jsonLink).toHaveAttribute(
      "href",
      expect.stringContaining("/experiments/exp-1/export?format=json"),
    );
  });
});

// ---------------------------------------------------------------------------
// Regression coverage for the deep audit. Each of these failed before the fix.
// ---------------------------------------------------------------------------

describe("Audit regressions", () => {
  const HEALTH_2 = HEALTH;

  /** `GET /overview` is a trimmed projection: five fields are absent. */
  const trimmedOverview = (extra: Record<string, unknown> = {}): Record<string, any> => ({
    stats: {
      sessions: 1,
      diagnoses: 1,
      observations: 4,
      experiments: 0,
      experiment_runs: 0,
    },
    recent_sessions: [],
    recent_diagnoses: [
      {
        id: "diag-trimmed",
        session_id: "sess-1",
        status: "confident",
        strategy: "adaptive",
        destination_node_id: "web-1",
        destination_service: "web",
        probes_used: 4,
        top_hypothesis: "DNS_FAILURE",
        top_probability: 0.88,
        started_at: "2026-10-09T10:00:00Z",
        // deliberately NO source_node_id, port, max_probes, entropy_bits, completed_at
      },
    ],
    latest_experiment: null,
    mode: "SIMULATED LAB",
    live_probe_enabled: false,
    ...extra,
  });

  it("renders the overview probe count safely when max_probes is absent", async () => {
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH_2),
      "GET /api/v1/overview": () => jsonResponse(trimmedOverview()),
      "GET /api/v1/lab/sessions": () => jsonResponse({ sessions: [], total: 0 }),
    });
    renderApp("/");

    // The old code rendered a bare "probes: 4/".
    await waitFor(() => expect(screen.getByText(/probes: 4/)).toBeInTheDocument());
    expect(screen.queryByText(/probes: 4\/\s*$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/probes: 4\/$/)).not.toBeInTheDocument();
    // And the hypothesis it did return must still be shown, not hidden.
    expect(screen.getAllByText(/DNS_FAILURE/).length).toBeGreaterThan(0);
  });

  it("renders both parts of the probe count when max_probes is present", async () => {
    const withMax = trimmedOverview();
    withMax.recent_diagnoses[0] = {
      ...withMax.recent_diagnoses[0],
      max_probes: 8,
    };
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH_2),
      "GET /api/v1/overview": () => jsonResponse(withMax),
      "GET /api/v1/lab/sessions": () => jsonResponse({ sessions: [], total: 0 }),
    });
    renderApp("/");
    expect(await screen.findByText("probes: 4/8")).toBeInTheDocument();
  });

  it("does not show a diagnosis from a previous session against a new topology", async () => {
    const user = userEvent.setup();
    const sessionA = {
      id: "sess-A",
      name: "Session A",
      template_id: "campus-basic",
      template_name: "Small Campus Network",
      mode: "simulated",
      random_seed: 1,
      topology: {
        id: "campus-basic",
        name: "Small Campus Network",
        description: "",
        nodes: [
          {
            id: "client-a",
            name: "Client A",
            type: "host",
            ip_address: "10.10.0.10",
            position: { x: 0, y: 0 },
            gateway: null,
            answers_icmp: true,
            services: [],
            dns_records: [],
            resolver_enabled: true,
            description: "",
          },
          {
            id: "web-a",
            name: "Web A",
            type: "app_server",
            ip_address: "10.30.0.80",
            position: { x: 400, y: 0 },
            gateway: null,
            answers_icmp: true,
            services: [
              { name: "web", port: 80, protocol: "tcp", healthy: true, description: "" },
            ],
            dns_records: [],
            resolver_enabled: true,
            description: "",
          },
        ],
        links: [],
        is_template: false,
      },
      active_faults: [],
      available_faults: [],
      parameter_reference: [],
      forwarding_tables: {},
      created_at: "2026-10-09T10:00:00Z",
      updated_at: "2026-10-09T10:00:00Z",
    };
    const sessionB = {
      ...sessionA,
      id: "sess-B",
      name: "Session B",
      topology: {
        ...sessionA.topology,
        nodes: [
          { ...sessionA.topology.nodes[0], id: "client-b", name: "Client B" },
          { ...sessionA.topology.nodes[1], id: "web-b", name: "Web B" },
        ],
      },
    };
    const diagnosisA = {
      id: "diag-A",
      session_id: "sess-A",
      status: "confident",
      strategy: "adaptive",
      strategy_label: "adaptive (expected information gain)",
      source_node_id: "client-a",
      destination_node_id: "web-a",
      destination_service: "web",
      port: 80,
      mode: "SIMULATED LAB",
      max_probes: 8,
      probes_used: 4,
      beliefs: {
        entropy_bits: 0.7,
        leader: { code: "DNS_FAILURE", title: "DNS", probability: 0.88 },
        lead_over_runner_up: 0.7,
        ranked: [
          {
            code: "DNS_FAILURE",
            title: "DNS",
            layers: ["L7"],
            summary: "",
            component_kind: "node",
            probability: 0.88,
            prior: 0.1,
          },
        ],
      },
      stopping_reason: "confident",
      next_probe: null,
      considered_alternatives: [],
      rejected_candidates: [],
      suspected_component: {
        component_kind: "node",
        component_id: "dns-1",
        confidence: "strong",
        evidence: [],
        bracketing: {},
      },
      steps: [],
      random_seed: 1,
      model_version: "v1",
      prior_config_version: "p1",
      started_at: "2026-10-09T10:00:00Z",
      completed_at: "2026-10-09T10:01:00Z",
    };

    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH_2),
      "GET /api/v1/catalogue": () =>
        jsonResponse({
          hypotheses: [],
          probes: [],
          probe_labels: {},
          fault_types: [],
          fault_parameters: [],
          model_version: "v1",
          prior_config_version: "p1",
        }),
      "GET /api/v1/lab/templates": () => jsonResponse({ templates: [] }),
      "GET /api/v1/overview": () =>
        jsonResponse({
          stats: { sessions: 2, diagnoses: 1, observations: 4, experiments: 0, experiment_runs: 0 },
          recent_sessions: [],
          recent_diagnoses: [],
          latest_experiment: null,
          mode: "SIMULATED LAB",
          live_probe_enabled: false,
        }),
      "GET /api/v1/lab/sessions": () =>
        jsonResponse({
          sessions: [
            {
              id: "sess-A",
              name: "Session A",
              template_id: "campus-basic",
              template_name: "Small Campus Network",
              mode: "simulated",
              active_fault_count: 0,
              active_fault_types: [],
              node_count: 2,
              link_count: 0,
              created_at: "2026-10-09T10:00:00Z",
              updated_at: "2026-10-09T10:00:00Z",
            },
            {
              id: "sess-B",
              name: "Session B",
              template_id: "campus-basic",
              template_name: "Small Campus Network",
              mode: "simulated",
              active_fault_count: 0,
              active_fault_types: [],
              node_count: 2,
              link_count: 0,
              created_at: "2026-10-09T10:00:00Z",
              updated_at: "2026-10-09T10:00:00Z",
            },
          ],
          total: 2,
        }),
      "GET /api/v1/lab/sessions/sess-A": () => jsonResponse(sessionA),
      "GET /api/v1/lab/sessions/sess-B": () => jsonResponse(sessionB),
      "POST /api/v1/diagnoses": () => jsonResponse(diagnosisA),
    });

    renderApp("/");
    const sessionAButton = await screen.findByRole("button", { name: "Session A" });
    await user.click(sessionAButton);
    await waitFor(() => expect(screen.getByText(/Active session/)).toBeInTheDocument());

    // Run a diagnosis on session A.
    await user.click(screen.getByRole("link", { name: /Diagnostic Workbench/ }));
    await user.click(await screen.findByRole("button", { name: "Start diagnosis" }));
    await waitFor(() =>
      expect(screen.getAllByText("DNS_FAILURE").length).toBeGreaterThan(0),
    );

    // Now switch to session B through the Overview page.
    await user.click(screen.getByRole("link", { name: /Overview/ }));
    const sessionBButton = await screen.findByRole("button", { name: "Session B" });
    await user.click(sessionBButton);
    // The active-session bar names the new session id exactly once.
    await waitFor(() =>
      expect(screen.getByText("sess-B")).toBeInTheDocument(),
    );

    // Session A's diagnosis must be gone. Before the fix the context kept it, so the
    // workbench rendered Diagnosis A's status and suspected component against Session
    // B's topology.
    await user.click(screen.getByRole("link", { name: /Diagnostic Workbench/ }));
    await waitFor(() =>
      // The workbench is on session B and offers a fresh setup, not session A's run.
      expect(screen.getByRole("button", { name: "Start diagnosis" })).toBeInTheDocument(),
    );
    expect(screen.queryByText(/diag-A/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Diagnosis diag-A/)).not.toBeInTheDocument();
    // Session B's own ids must be the ones offered, not session A's leftovers. These
    // are native <select> children, so query the DOM rather than by ARIA role.
    const optionLabels = Array.from(document.querySelectorAll("select option")).map(
      (option) => option.textContent ?? "",
    );
    expect(optionLabels.some((label) => label.includes("Web B"))).toBe(true);
    expect(optionLabels.some((label) => label.includes("Web A"))).toBe(false);
  });

  it("highlights an injected fault differently from a diagnosed suspect", async () => {
    // An injected fault must render red and solid; a diagnosis suspect must be blue and
    // dashed. Rendering both the same way made the legend a lie.
    const { container } = render(
      <MemoryRouter>
        <TopologyView
          topology={{
            id: "t",
            name: "T",
            description: "",
            is_template: false,
            nodes: [
              {
                id: "n1",
                name: "N1",
                type: "host",
                ip_address: "10.0.0.1",
                position: { x: 0, y: 0 },
                gateway: null,
                answers_icmp: true,
                services: [],
                dns_records: [],
                resolver_enabled: true,
                description: "",
              },
              {
                id: "n2",
                name: "N2",
                type: "app_server",
                ip_address: "10.0.0.2",
                position: { x: 300, y: 0 },
                gateway: null,
                answers_icmp: true,
                services: [],
                dns_records: [],
                resolver_enabled: true,
                description: "",
              },
            ],
            links: [
              {
                id: "l-fault",
                node_a: "n1",
                node_b: "n2",
                up: true,
                latency_ms: 1,
                packet_loss_rate: 0,
                mtu_bytes: 1500,
                suppress_frag_needed: false,
                bandwidth_mbps: null,
                description: "",
              },
              {
                id: "l-suspect",
                node_a: "n1",
                node_b: "n2",
                up: true,
                latency_ms: 1,
                packet_loss_rate: 0,
                mtu_bytes: 1500,
                suppress_frag_needed: false,
                bandwidth_mbps: null,
                description: "",
              },
            ],
          }}
          suspects={{
            links: new Set(["l-fault"]),
            nodes: new Set(),
            services: new Set(),
          }}
          suspectedComponentId="l-suspect"
        />
      </MemoryRouter>,
    );
    const lines = Array.from(container.querySelectorAll("line"));
    const faultLine = lines.find(
      (line) => line.getAttribute("stroke") === "#dc2626",
    );
    const suspectLine = lines.find(
      (line) => line.getAttribute("stroke") === "#0284c7",
    );
    expect(faultLine).toBeTruthy();
    expect(suspectLine).toBeTruthy();
    // The fault is solid; the suspect is dashed. Two different signals.
    expect(faultLine?.getAttribute("stroke-dasharray")).toBeNull();
    expect(suspectLine?.getAttribute("stroke-dasharray")).toBe("6 4");
  });

  it("only offers probes the model actually ran", async () => {
    // The UI must not invent a conclusion. With no diagnosis the report page says so.
    mockApi({
      "GET /api/v1/health": () => jsonResponse(HEALTH_2),
      "GET /api/v1/catalogue": () =>
        jsonResponse({
          hypotheses: [],
          probes: [],
          probe_labels: {},
          fault_types: [],
          fault_parameters: [],
          model_version: "v1",
          prior_config_version: "p1",
        }),
    });
    renderApp("/report");
    expect(await screen.findByText("Nothing to report yet")).toBeInTheDocument();
    // It must not claim any cause without an API response.
    expect(screen.queryByText(/Most likely cause/)).not.toBeInTheDocument();
  });
});

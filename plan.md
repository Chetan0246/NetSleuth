# NetSleuth — Implementation Plan for an AI Coding Agent

> **Project title:** NetSleuth: Evidence-Guided, Multi-Layer Network Fault Localization Using Active Probing  
> **Course:** Computer Networks  
> **Project type:** Full-stack networking lab, diagnostic engine, visualization, and experimental evaluation  
> **Primary goal:** Build a working, reproducible system that investigates connectivity failures, ranks likely root causes from diagnostic evidence, chooses useful follow-up probes, and explains its conclusions.

---

## 0. Instructions to the Coding Agent

You are the lead engineer implementing NetSleuth as a complete, runnable academic project. Follow this document as the source of truth.

### Non-negotiable requirements

1. **Build a functioning application, not just a UI mock-up.** The UI must call a real backend, the backend must execute the diagnostic engine, and the results must come from the configured lab state.
2. **Make the demo reproducible.** The core project must run on a normal developer laptop without a physical router, special hardware, root access, or internet access. Use a deterministic virtual network lab for the required core.
3. **Keep networking concepts visible.** Model hosts, routers, links, IP addresses, routes, DNS, TTL/hops, ICMP-style reachability, TCP connection outcomes, packet loss, delay, and MTU-related failures. Explain which behaviours are simulated.
4. **Implement a real diagnostic-selection algorithm.** Do not label the app “AI-powered” unless it actually uses a defined method. Implement a documented, explainable Bayesian/likelihood-based hypothesis updater and an information-gain probe selector. No trained machine-learning model is required for the core project.
5. **Keep the system honest.** Never fabricate diagnosis confidence, benchmark numbers, probe results, or charts. Compute all displayed measurements from actual application runs. If evidence is inconclusive, say so.
6. **Separate simulated and real diagnostics.** Every simulated result must be visibly labeled `SIMULATED LAB`. Real host probes must be labeled `LIVE PROBE` and must execute only when explicitly requested.
7. **No unauthorized network scanning.** Live mode is for a user-specified, authorized host and a small number of explicitly selected diagnostics. Do not implement port scanning, subnet sweeps, exploitation, stealth, or denial-of-service traffic.
8. **Deliver documentation and tests.** Provide setup instructions, architecture and algorithm documentation, test scenarios, evaluation methodology, a sample results report generated from actual runs, and a short demo script.
9. **Work incrementally.** First create the backend engine and tests, then API, then frontend, then evaluation. Keep the repository runnable at every stage.
10. **Do not stop after generating files.** Install dependencies where available, run tests, start/build the application, fix errors, and report exactly what was and was not verified.

### Agent workflow

- Inspect the existing repository before changing it; preserve useful work and conventions.
- Create a concise task checklist and implement each phase in order.
- Prefer simple, typed, testable modules over a monolithic file.
- Avoid placeholder buttons, mocked success messages, hardcoded metrics, and TODO-only core features.
- If an optional environment-dependent feature cannot be run, keep it isolated and document the limitation rather than blocking the core project.

---

## 1. Problem Statement

When a user cannot reach a service, the underlying cause might be a failed link, a routing black hole, packet loss, excessive delay, DNS failure, an MTU/path-MTU issue, a blocked TCP port, or an unavailable application service. Several of these failures can produce similar symptoms.

Basic monitoring tools typically report that a destination is unreachable or that latency is high. NetSleuth goes one step further: it gathers diagnostic observations, maintains a ranked set of possible causes, selects the next test that is expected to reduce uncertainty, and shows the evidence behind its conclusion.

### Proposed solution

NetSleuth is an interactive diagnostic workbench with four connected parts:

1. **Virtual network lab:** A small topology containing clients, routers, DNS servers, and application servers.
2. **Fault-injection engine:** Controlled, repeatable faults such as a down link, incorrect route, DNS outage, packet loss, high latency, MTU black hole, blocked TCP connection, or stopped service.
3. **Evidence-guided diagnostic engine:** A probe planner and fault-hypothesis ranker that chooses tests based on expected information gain and explains its reasoning.
4. **Dashboard and evaluation suite:** A topology view, evidence timeline, diagnosis report, and experiment runner comparing adaptive probing with a fixed-order baseline.

### What this project is not

- It is not an intrusion detection system or DDoS mitigation tool.
- It is not a dynamic routing or load-balancing optimizer.
- It is not simply a packet sniffer with graphs.
- It is not a replacement for production-grade network-management products.
- It does not claim that the simulator is a full TCP/IP implementation. Simulation rules must be documented accurately.

---

## 2. Core Contribution and Differentiation

The central contribution is **evidence-guided fault localization through adaptive diagnostic test selection**.

The application must demonstrate all of the following:

- Multiple possible causes can explain the same initial symptom.
- Each diagnostic probe provides an observation that changes the relative likelihood of those causes.
- The engine can select the next probe based on expected information gain rather than always executing the same fixed sequence.
- The final result includes evidence for the leading cause and observations that weaken alternatives.
- The adaptive approach is compared experimentally with a fixed-sequence baseline using known injected faults.

This makes the project distinct in emphasis from traffic anomaly monitoring, intrusion detection, adaptive routing, and load balancing.

### Avoid unsupported claims

Do not claim “first of its kind,” “100% accurate,” or “AI-powered” without evidence. Call the method an **explainable probabilistic diagnostic engine**. In the report, distinguish a project-specific design contribution from established networking and diagnosis concepts.

---

## 3. Users and Main Use Cases

### Primary user

A student or network administrator in a lab who needs to understand why a client cannot reach a host or service.

### Main use cases

1. Start a predefined network topology.
2. Select a source client and destination service.
3. Inject a known fault or choose a scenario preset.
4. Run adaptive diagnosis step-by-step or automatically.
5. View the evidence from each probe and the updated hypothesis ranking.
6. Compare adaptive probing against a fixed-order diagnostic strategy.
7. Export a diagnostic report and experiment results.
8. Optionally run a small set of live diagnostics against an explicitly authorized host.

---

## 4. Scope: Required Core vs Optional Enhancements

### Required core (must be complete)

- Predefined editable topology or a topology selector with at least two templates.
- Deterministic virtual-lab simulation with path traversal.
- At least eight distinct injected fault scenarios.
- At least five diagnostic probe types.
- Evidence records for each probe.
- Fault hypotheses with confidence/probability updates.
- Information-gain-based adaptive probe selection.
- A fixed-order baseline for comparison.
- Interactive web dashboard with topology, probe timeline, diagnosis, and evaluation.
- Persistent scenario/experiment history in SQLite or structured local files.
- Automated unit, integration, and API tests.
- Documentation, report export, and a working demo script.

### Optional enhancements (only after core passes)

- Live ping, traceroute, DNS lookup, and single-port TCP connect probes on the local machine.
- Offline PCAP upload and parsing with Scapy.
- Mininet or Linux network-namespace adapter for experiments on an actual virtualized topology.
- Additional topologies, IPv6, or user-defined fault combinations.
- PDF report export if it can be implemented reliably; Markdown/HTML/CSV exports are sufficient for the core.

Do not sacrifice the required engine, tests, or evaluation to add optional features.

---

## 5. Technical Stack

Use a maintainable, two-part application.

### Backend

- Python 3.11 or newer, subject to dependency compatibility.
- FastAPI for HTTP APIs and interactive API documentation.
- Pydantic models for request/response validation.
- NetworkX for topology graph operations and path calculation.
- SQLite for session, diagnosis, and experiment metadata. SQLAlchemy is acceptable if it keeps persistence clean; avoid unnecessary ORM complexity.
- NumPy is optional. Entropy and probability calculations can be implemented with the Python standard library.
- pytest and HTTPX for automated backend/API tests.

### Frontend

- React + TypeScript using Vite.
- Tailwind CSS or an existing consistent styling system.
- React Flow (`@xyflow/react`) for interactive nodes and links, or a simpler SVG graph if React Flow creates installation risk.
- Recharts for actual experiment charts.
- Lucide icons for interface icons.
- Vitest and React Testing Library for key frontend tests.

### Optional packet tools

- Scapy for offline PCAP parsing and, if safely supported, explicitly requested local lab capture.
- `ping`, `traceroute`/`tracepath`, `dig`/`nslookup`, and ordinary TCP socket connection for live probes where installed.

### Developer workflow

- Git repository with a clear README.
- `.env.example` only for non-secret settings; no secrets are required by the core.
- `requirements.txt` or `pyproject.toml` for backend dependencies, and `package.json` for frontend dependencies.
- A single documented local development workflow.

---

## 6. High-Level Architecture

```text
┌───────────────────────────────────────────────────────────────┐
│                    React + TypeScript UI                      │
│ Dashboard | Topology Lab | Diagnose | Evidence | Experiments   │
└──────────────────────────────┬────────────────────────────────┘
                               │ REST / JSON
┌──────────────────────────────▼────────────────────────────────┐
│                         FastAPI API                            │
│ Validation | Session API | Diagnosis API | Experiment API      │
└────────────┬────────────────────┬──────────────────┬───────────┘
             │                    │                  │
┌────────────▼────────┐ ┌─────────▼──────────┐ ┌─────▼───────────┐
│ Virtual Lab Engine  │ │ Diagnostic Engine  │ │ Persistence     │
│ Graph, routes, links│ │ Hypotheses, Bayes  │ │ SQLite          │
│ Fault injection     │ │ Information gain   │ │ Runs/evidence   │
│ Probe observations  │ │ Stop/uncertainty   │ │ Export metadata │
└────────────┬────────┘ └─────────┬──────────┘ └─────────────────┘
             │                    │
             └──────────┬─────────┘
                        │
              ┌─────────▼─────────┐
              │ Probe Interface   │
              │ Simulated probes  │
              │ Optional live     │
              │ probe adapter     │
              └───────────────────┘
```

### Layer responsibilities

**UI:** Collects the user’s selections, shows topology and results, and renders the real backend responses. It must not implement diagnosis rules itself.

**API:** Validates requests, creates and retrieves sessions, coordinates diagnostic runs, and returns typed response models.

**Virtual lab:** Owns the topology, active fault state, routes, path traversal, simulated delay/loss, and probe outcome generation.

**Diagnostic engine:** Owns hypotheses, likelihoods, belief updates, probe selection, stopping rules, and explanation generation.

**Persistence:** Stores scenario configuration, fault ground truth for lab evaluation, diagnostic observations, posterior rankings, experiment metrics, and creation timestamps.

**Live probe adapter:** Is an optional boundary for real commands/sockets. It must not silently replace simulation or mix simulated evidence with live evidence.

---

## 7. Virtual Network Lab Design

### 7.1 Network objects

Represent each network as a graph. Use a directed graph if direction-specific behaviour is supported; otherwise clearly document that the core lab uses symmetric links.

#### Node types

- `host`: client endpoint.
- `router`: forwards packets and decrements TTL/hop limit in simulated traces.
- `dns_server`: resolves configured hostnames.
- `app_server`: exposes simulated TCP services and optional HTTP health state.

Every node should have an ID, display name, type, IP address, and relevant settings. IP addresses must be unique within a topology.

#### Link attributes

Each link must include:

- Stable link ID and endpoint IDs.
- `up` boolean.
- `latency_ms` non-negative numeric value.
- `packet_loss_rate` between 0 and 1.
- `mtu_bytes` integer within a validated range.
- Optional bandwidth metadata for display only unless bandwidth behaviour is truly modelled.

Never claim actual throughput simulation if the engine only stores bandwidth metadata.

#### Routing behaviour

- Each router has a routing table or uses a documented shortest-path approach for the core topology.
- Path computation should be deterministic for a fixed topology.
- Hop count and TTL-expiry behaviour should be generated from the path.
- A `route_blackhole` or missing route fault must make the relevant destination unreachable through the simulated routing layer.
- Explain that the simulator models selected routing behaviour and is not a full router OS.

### 7.2 Required topology templates

Create at least two topology templates.

**Template A — Small Campus Network**

`Student Client → Access Router → Campus Router → DNS Server / Web Server`

Include at least one alternative path or a distinct service route if it helps test localization.

**Template B — Multi-Hop Network**

`Client → Edge Router → Core Router → Branch Router → Application Server`

Place the DNS server on a reachable segment. Multiple hops allow traceroute-style observations and link-localization experiments.

Use readable IP ranges such as `10.10.0.0/24`, `10.20.0.0/24`, and `10.30.0.0/24` in the *simulation model*. If full subnet/routing validation is implemented, use Python's `ipaddress` module.

### 7.3 Fault injection model

A fault must be a structured object with a unique ID, type, target node/link/service, parameters, active status, and a human-readable description. Fault activation/deactivation must be reversible and reflected in all subsequent probes.

Implement at least these eight scenarios:

1. **Link Down:** A link between two nodes is unavailable.
2. **Routing Black Hole:** A destination route is missing or points to an unusable next hop.
3. **DNS Failure:** The DNS resolver is unavailable or a requested record cannot be resolved.
4. **Packet Loss:** A selected link drops packets at a configurable probability. Use seeded randomness for reproducible experiments.
5. **High Latency:** A selected link adds controlled delay.
6. **MTU / Path-MTU Black Hole:** Small probes succeed but packets above a path constraint fail according to documented simulated DF/fragmentation rules.
7. **TCP Port Blocked:** A configured destination port is silently dropped or explicitly rejected; model timeout and refusal as different outcomes.
8. **Application Service Down:** The route, IP reachability, and TCP path may work, but the target service reports unavailable.

Include **Gateway Unreachable** as a useful additional scenario if time permits. Fault injection should support at least one active fault per scenario; multiple-fault combinations are an enhancement after single-fault behaviour is correct.

### 7.4 Determinism

All random packet-loss decisions must accept a seed. A scenario record must persist its seed. Re-running a scenario with the same topology, fault settings, and seed should reproduce the same probe outcomes.

---

## 8. Diagnostic Probe Types

Implement a common probe interface so probes can be added without changing the diagnostic algorithm.

Every observation must record:

- Probe ID and probe type.
- Source and destination.
- Start/end timestamps or elapsed duration.
- Outcome category.
- Structured details (not only a sentence).
- Evidence statements.
- Whether the result is `SIMULATED LAB` or `LIVE PROBE`.
- Probe cost or weight used in selection, if applicable.

### Required probes

1. **ICMP-style Reachability Probe**
   - Simulates echo success, timeout, unreachable response, packet loss, or delay.
   - Record latency and loss when measurable.
   - Do not equate a blocked ICMP response with proof that a host is down.

2. **DNS Lookup Probe**
   - Queries the simulated DNS mapping.
   - Differentiate resolver unreachable, name not found, timeout, and successful resolution.
   - A DNS failure should not prevent the system from testing direct IP reachability where the destination IP is known.

3. **Traceroute / TTL Probe**
   - Walks the simulated path hop by hop.
   - Returns hop records and the last responding node.
   - Distinguish “probe timed out at a hop” from conclusive path failure; intermediate routers can suppress responses.

4. **TCP Connect Probe**
   - Tests a single configured server port in the simulation.
   - Outcomes: connected, connection refused, timeout/drop, destination unreachable, or service unavailable when the simulated model can distinguish it.
   - Do not implement port scanning.

5. **MTU / Packet-Size Probe**
   - Tries a documented set of packet sizes against the simulated path.
   - Shows the maximum successful size or identifies an MTU-related symptom.
   - Keep simulated path-MTU behaviour explicit and consistent.

6. **Service Health Probe**
   - Checks the configured application service after network and transport reachability are considered.
   - Outcomes: healthy, unhealthy, refused, or unavailable depending on the model.

### Optional probes

- DNS-over-real-network or resolver configuration checks through OS commands.
- Single TCP connection to a manually specified, authorized host and port.
- Real `ping` and traceroute commands with strict timeouts.
- Offline PCAP analysis for TCP SYN/SYN-ACK/RST patterns and relevant DNS/ICMP packets.

### Probe execution controls

- Set per-probe timeout and a maximum number of probes per diagnosis.
- Do not block the main event loop with long-running subprocesses; use async subprocesses or background execution where appropriate.
- Validate user input and call subprocesses with an argument list, never `shell=True`.
- For live mode, show a confirmation and a clear target/port preview before running any probe.

---

## 9. Fault Hypotheses and Explainable Diagnosis

### 9.1 Hypothesis catalogue

At minimum, use these hypotheses:

- `LINK_FAILURE`
- `ROUTING_FAILURE`
- `DNS_FAILURE`
- `PACKET_LOSS`
- `HIGH_LATENCY`
- `MTU_BLACK_HOLE`
- `TCP_FILTER_OR_PORT_FAILURE`
- `APPLICATION_SERVICE_FAILURE`
- `UNKNOWN_OR_MULTIPLE_CAUSES`

The `UNKNOWN_OR_MULTIPLE_CAUSES` option is important: the engine must be allowed to report insufficient evidence or that the observed problem does not match a known single-fault scenario.

Each hypothesis should contain:

- Stable code and readable title.
- OSI/TCP-IP layer or layers involved.
- Brief explanation.
- Recommended verification/remediation steps.
- Probe likelihood model metadata.

### 9.2 Probability model

Implement a transparent, testable Bayesian-style belief updater. Begin with documented prior probabilities. For each observed outcome, update the hypothesis probabilities using a likelihood table:

`posterior(H) ∝ likelihood(observation | H) × prior(H)`

Normalize all non-zero posterior values so the sum equals 1.0. Add a small probability floor or carefully handle zero likelihoods to avoid accidental numerical collapse. The likelihood table must be explicit in code or a configuration file, not hidden in UI code.

Requirements:

- Keep all probabilities finite and between 0 and 1.
- Normalized beliefs should sum to approximately 1.0.
- An observation that supports a hypothesis should increase its relative ranking when other factors are equal.
- Store the prior, observation, likelihood contribution, and posterior for every update so the system can explain its result.
- Write tests for normal cases, conflicting evidence, unknown outcomes, and numerical stability.

Do not tune probabilities using the same benchmark cases and then present those cases as an independent test. If calibration is later performed, split calibration and evaluation scenarios.

### 9.3 Expected information gain

The adaptive planner selects the next eligible probe that is expected to reduce uncertainty about the current hypothesis distribution.

Use Shannon entropy:

`H(B) = -Σ p(h) log2 p(h)`

For each candidate probe `q`, estimate expected posterior entropy over its possible outcomes:

`EIG(q) = H(B) - Σ_o P(o | q, B) × H(B after observing o)`

Choose a probe with high expected information gain. If probes have different costs, use a documented score such as `EIG / estimated_cost`. The cost can be a simple relative value (for example, cheap, medium, expensive) in the simulator; do not claim it is measured wall-clock cost unless measured.

Implementation details:

- Define possible outcomes and `P(outcome | hypothesis, probe)` in one transparent catalogue.
- Derive `P(outcome | probe, current belief)` from the current belief and likelihood table.
- For every candidate outcome, calculate the hypothetical posterior and entropy.
- Exclude already-run probes if they cannot produce new evidence, unless the planner explicitly supports repeat probes for loss/latency estimates.
- Apply a deterministic tie-breaker so tests and demos are reproducible.
- If the best probe adds negligible expected information, stop or ask the user to run a new class of test.
- Clearly show why the chosen probe was selected (expected information gain, cost, and the ambiguity it should resolve).

**Important:** The selection logic must actually affect the next probe shown and executed. Do not calculate EIG only for display while continuing to run a fixed sequence.

### 9.4 Stopping rule and uncertainty

Stop when one of the following holds:

- A leading hypothesis passes a configurable confidence threshold and is sufficiently separated from the next candidate.
- No available probe is expected to reduce uncertainty meaningfully.
- The maximum probe budget is reached.
- An unrecoverable simulation or input error occurs.

Use configurable thresholds; begin with top posterior ≥ 0.80 and a configurable lead over the second-ranked hypothesis, then calibrate with experiments. If the threshold is not met, report **inconclusive** or **more than one plausible cause**, not a forced confident answer.

The report should include:

- Top hypothesis and posterior score.
- Other plausible hypotheses.
- Evidence supporting the top hypothesis.
- Evidence against or not explained by it.
- Probes executed and probes considered next.
- Recommended follow-up test or remediation.
- A warning that simulated diagnoses only apply to the configured model.

### 9.5 Explanation quality

Generate explanations from structured evidence and rules. Do not use a free-form LLM as the source of truth. Example:

> “DNS resolution failed while the simulated client could reach the server IP. This makes a general route failure less likely and increases the DNS-failure hypothesis. The next selected test is a resolver reachability check because it helps distinguish an unavailable resolver from a missing DNS record.”

Only generate a statement if the evidence in the current run supports it. Avoid phrases like “proved” when the observation is merely suggestive.

---

## 10. Adaptive Strategy vs Fixed Baseline

A core academic experiment must compare the adaptive strategy with a baseline.

### Baseline strategy

Use a documented fixed order, such as:

1. ICMP-style reachability.
2. DNS lookup.
3. Traceroute.
4. TCP connect.
5. MTU/packet-size test.
6. Service health.

Both strategies must use the same topology, fault state, prior/likelihood catalogue, stopping threshold, and probe budget. Only the selection order differs. If repeat tests are needed to estimate packet loss, apply the same repeated-probe policy to both methods.

### Evaluation dataset

Create an automated scenario set using known ground-truth injected faults. Include each required fault type, vary target location and configuration, and use fixed seeds. Prefer at least 30 generated runs per fault class if runtime allows; otherwise use the largest reproducible set feasible and state the actual sample size.

The experiment runner must create run records from actual executions and must not contain pre-filled success rates.

### Metrics

Calculate and display:

- **Top-1 fault accuracy:** correct leading hypothesis / evaluated runs.
- **Top-3 fault accuracy:** ground truth appears among the three highest-ranked hypotheses / evaluated runs.
- **Mean probes to decision:** average probes executed before stopping.
- **Diagnosis coverage:** proportion of runs that reach a non-inconclusive decision under the stopping rule.
- **Inconclusive rate:** proportion of runs that do not meet the stopping rule.
- **Mean elapsed diagnostic time:** only when measured; report simulator timing separately from real probe duration.
- **Fault localization accuracy:** for link/node-specific faults, whether the predicted component matches the injected component.
- **Confusion matrix:** actual fault class vs predicted fault class.

Show adaptive and fixed-baseline values side by side. Add per-fault breakdowns, not only an aggregate score. Include sample counts and the exact experiment configuration. A run can legitimately show that adaptive probing does not outperform the baseline; report results truthfully and discuss why.

### Avoid misleading evaluation

- Do not compare strategies with different probe budgets or stopping conditions without explaining the difference.
- Do not use injected ground truth as an input to the diagnostic engine. The engine may know the topology and probe model, but it must not read the selected fault label to choose a diagnosis.
- Ground truth is used only by the evaluator after the run.
- Clearly label simulated wall-clock duration versus modelled link latency.
- Persist experiment configuration and random seeds to make results reproducible.

---

## 11. User Interface Specification

Create a polished and accessible dashboard. Use a consistent design system; prioritize legibility over visual effects. Dark mode is fine, but all status information must also use text/icons and not colour alone.

### Page 1 — Overview Dashboard

Show:

- Number of saved lab sessions.
- Recent diagnostic runs.
- Latest diagnosis and confidence, if available.
- Active fault scenario.
- A compact topology preview.
- A clear `Open Network Lab` and `Run Diagnosis` action.
- A persistent label identifying simulation mode vs live mode.

All statistics must be derived from API data. Display a genuine empty state for a fresh install.

### Page 2 — Network Lab

- Render nodes and links using React Flow or SVG.
- Show node type, IP address, and link attributes in a side panel.
- Provide at least two topology templates.
- Let the user select a source and destination.
- Provide a scenario selector for each supported fault.
- Show the target component and parameters before applying the fault.
- Clearly indicate active faults; allow disabling/resetting faults.
- Include a `Reset Lab` action with confirmation if it discards the current scenario.

### Page 3 — Diagnostic Workbench

- Show source, destination, target service/port, and maximum probe budget.
- Offer `Run One Step`, `Run to Conclusion`, `Reset Diagnosis`, and `Compare with Baseline` actions as appropriate.
- Render the current hypothesis ranking in descending order.
- Display the next probe selected by the adaptive planner and its expected information-gain score.
- Show the reason for the probe selection in plain language.
- Show loading, success, timeout, error, and inconclusive states accurately.
- Do not mark a diagnosis as complete until the backend returns a terminal state.

### Page 4 — Evidence and Diagnosis Report

- Timeline of probes with timestamps, outcome, elapsed time, and evidence details.
- Top diagnosis, confidence, alternatives, and certainty label.
- Evidence supporting and weakening each leading hypothesis.
- Highlight the suspected fault's node or link on the topology when location-specific evidence is available.
- Recommended next step/remediation with a caveat appropriate to the confidence.
- Export the report to Markdown or JSON.

### Page 5 — Experiment Studio

- Run the reproducible fault suite.
- Configure run count/seed within validated limits.
- Compare adaptive vs fixed-order baseline.
- Charts for top-1 accuracy, top-3 accuracy, average probes, and inconclusive rate.
- Per-fault result table and confusion matrix.
- Export results as CSV and JSON.
- Display the number of actual completed runs and generation time.

### Optional Page 6 — Live Diagnostics / PCAP

Only after all required pages work.

- Strong visible `LIVE MODE — USE ONLY ON AUTHORIZED TARGETS` notice.
- Require the user to explicitly enter/confirm a hostname or IP and, for TCP, one port.
- Use tight timeouts, low probe counts, and no scanning of port ranges or entire subnets.
- Display raw outcome metadata, distinguish permission/tool-not-installed errors, and do not silently simulate a result if a live command fails.
- For PCAP, support offline files only by default, validate extension/size, and display that the capture may contain sensitive data.

### UX requirements

- Responsive desktop and laptop layout.
- Empty, loading, error, and success states.
- Confirmation for destructive actions.
- Keyboard-accessible controls and labels.
- No buttons that look active but do nothing.
- Avoid arbitrary hardcoded dashboard data.

---

## 12. API Design

Use a versioned REST API prefix such as `/api/v1`. Follow consistent HTTP status codes and Pydantic schemas. Exact response shapes can evolve during implementation, but document and test them.

### Required endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness check and version |
| `GET` | `/api/v1/lab/templates` | List built-in topologies |
| `POST` | `/api/v1/lab/sessions` | Create a new lab session from a template |
| `GET` | `/api/v1/lab/sessions/{session_id}` | Retrieve topology, active faults, source/destination and mode |
| `PUT` | `/api/v1/lab/sessions/{session_id}/faults` | Activate/deactivate a validated fault |
| `POST` | `/api/v1/lab/sessions/{session_id}/reset` | Reset the session to a known state |
| `POST` | `/api/v1/diagnoses` | Start a diagnosis for a session/source/destination |
| `GET` | `/api/v1/diagnoses/{diagnosis_id}` | Retrieve current belief, evidence and status |
| `POST` | `/api/v1/diagnoses/{diagnosis_id}/step` | Execute one adaptively selected probe |
| `POST` | `/api/v1/diagnoses/{diagnosis_id}/run` | Continue until a terminal status or probe budget |
| `POST` | `/api/v1/diagnoses/{diagnosis_id}/baseline` | Run or link a fixed-order comparison with same settings |
| `GET` | `/api/v1/diagnoses/{diagnosis_id}/report` | Return a report object or downloadable Markdown/JSON |
| `POST` | `/api/v1/experiments/run` | Execute the ground-truth scenario suite |
| `GET` | `/api/v1/experiments/{experiment_id}` | Get progress/results for an experiment |
| `GET` | `/api/v1/experiments/{experiment_id}/export` | Export CSV/JSON results |

Optional live endpoints must be separate from simulated endpoints and must require explicit target confirmation. Do not put live probe calls behind a generic diagnosis request without a mode field.

### API engineering requirements

- Validate all identifiers and enums.
- Return structured errors with an error code, human-readable detail, and optional field information.
- Apply request limits to experiment run count and probe budget.
- Add tests for invalid IDs, malformed faults, invalid IP/port, and repeated operations.
- Use predictable response models; do not return arbitrary internal Python objects.

---

## 13. Data Models

Define typed backend models and persistence tables (or equivalent structured storage) for the following entities.

### `LabSession`

- `id`, `name`, `template_id`, `mode` (`simulated` or `live`), `topology_json`, `active_faults_json`, `random_seed`, `created_at`, `updated_at`.

### `FaultConfig`

- `id`, `fault_type`, `target_id`, `parameters`, `is_active`, `description`.

### `DiagnosisRun`

- `id`, `session_id`, `source_node_id`, `destination_node_id`, `destination_service`, `strategy` (`adaptive` or `baseline`), `status`, `max_probes`, `prior_config_version`, `started_at`, `completed_at`.

### `ProbeObservation`

- `id`, `diagnosis_id`, `sequence_number`, `probe_type`, `selected_reason`, `information_gain`, `outcome`, `details_json`, `evidence_json`, `elapsed_ms`, `mode`, `created_at`.

### `BeliefSnapshot`

- `id`, `diagnosis_id`, `observation_id`, `ranked_hypotheses_json`, `entropy_before`, `entropy_after`, `created_at`.

### `ExperimentRun`

- `id`, `experiment_id`, `seed`, `scenario_id`, `actual_fault_type`, `actual_target_id`, `strategy`, `predicted_fault_type`, `predicted_target_id`, `is_correct_top1`, `is_correct_top3`, `probes_used`, `is_inconclusive`, `elapsed_ms`, `created_at`.

Keep the injected ground-truth fault in the evaluator/experiment record, not in the diagnostic engine's request context.

---

## 14. Suggested Repository Structure

Adjust only if the repository already has a sensible equivalent structure.

```text
netsleuth/
├── README.md
├── plan.md
├── docs/
│   ├── architecture.md
│   ├── networking-concepts.md
│   ├── diagnostic-algorithm.md
│   ├── experiment-methodology.md
│   ├── test-plan.md
│   ├── demo-script.md
│   ├── limitations.md
│   └── references.md
├── backend/
│   ├── pyproject.toml                 # or requirements.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── routes_health.py
│   │   │   ├── routes_lab.py
│   │   │   ├── routes_diagnosis.py
│   │   │   └── routes_experiments.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   └── errors.py
│   │   ├── models/
│   │   │   ├── schemas.py
│   │   │   └── persistence.py
│   │   ├── lab/
│   │   │   ├── graph.py
│   │   │   ├── templates.py
│   │   │   ├── routing.py
│   │   │   ├── faults.py
│   │   │   └── simulator.py
│   │   ├── probes/
│   │   │   ├── base.py
│   │   │   ├── simulated.py
│   │   │   └── live.py                # optional
│   │   ├── diagnosis/
│   │   │   ├── hypotheses.py
│   │   │   ├── likelihoods.py
│   │   │   ├── bayes.py
│   │   │   ├── information_gain.py
│   │   │   ├── planner.py
│   │   │   ├── explanations.py
│   │   │   └── runner.py
│   │   ├── experiments/
│   │   │   ├── scenarios.py
│   │   │   ├── runner.py
│   │   │   ├── metrics.py
│   │   │   └── exports.py
│   │   └── storage/
│   │       ├── repository.py
│   │       └── database.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── api/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── app/
│       ├── components/
│       ├── pages/
│       ├── features/
│       │   ├── lab/
│       │   ├── diagnosis/
│       │   └── experiments/
│       ├── lib/api.ts
│       ├── types/
│       └── test/
└── scripts/
    ├── run_dev.sh
    ├── run_demo.py
    └── run_evaluation.py
```

Do not create empty files for every path just to match the tree. Create meaningful modules when their phase begins.

---

## 15. Important Algorithmic Implementation Details

### 15.1 One shared probe contract

Create a typed interface or protocol similar to:

```python
class ProbeRunner(Protocol):
    def run(self, request: ProbeRequest, lab_state: LabState) -> ProbeObservation:
        ...
```

The exact signature may be asynchronous if needed, but all probes must return the same structured observation schema. The diagnostic engine should depend on the probe interface, not on implementation-specific details of the simulator.

### 15.2 Keep lab ground truth separate

The simulator must use the active fault configuration to produce observations, but the diagnostic policy must receive only the network configuration and observations it would legitimately have in a diagnostic session. Do not pass the ground-truth `fault_type` or `scenario_id` into the belief updater or planner.

### 15.3 Reproducibility and observability

- Assign a seed to every experimental run.
- Record every selected probe and why it was selected.
- Record belief entropy before and after every observation.
- Record model/config version identifiers in an experiment export.
- Use structured logging; never expose stack traces or internal file paths to the browser in production responses.

### 15.4 Avoid logic duplication

The API, UI, baseline and adaptive runner should all use the same underlying probe engine and likelihood model. Strategy selection decides *which test to run next*, not how results are interpreted.

---

## 16. Optional Live Diagnostics: Guardrails and Behaviour

Live mode is an enhancement, not a blocker for the required simulator.

### Allowed scope

- A single ICMP reachability test to a user-specified target.
- A bounded traceroute to one user-specified target.
- One DNS lookup for a user-specified hostname.
- One TCP connect attempt to one user-specified host and port.
- Optional single HTTP(S) health request with a short timeout.

### Required controls

- Require the user to confirm that they own or are authorized to test the target.
- Do not scan ports, subnets, or arbitrary ranges.
- Use explicit command arguments, strict timeouts, a small fixed attempt limit, and safe subprocess handling.
- Validate hostnames, IPs, and ports before execution.
- Do not permit arbitrary shell commands, user-supplied executable paths, or command options.
- If a tool is missing or permissions are denied, return a clear error. Do not invent a simulated success.
- Label every observation `LIVE PROBE`; never mix live observations into a simulated evidence timeline unless the report clearly separates them.
- If permissions/network policies restrict raw sockets, fall back only to a clearly named TCP/HTTP/DNS check, not to a falsely labelled ping.

No feature may attempt to disrupt a third-party system.

---

## 17. Testing Plan

Tests are a core deliverable, not optional polish.

### 17.1 Unit tests — backend

Test at minimum:

- Graph creation and validation.
- Duplicate IP and missing endpoint rejection.
- Shortest-path/routing behaviour.
- Link-down and route-black-hole outcomes.
- DNS success, record absence, and resolver outage.
- Packet loss reproducibility with a seed.
- Latency calculation and thresholds.
- MTU probe outcomes for small vs large packets.
- TCP timeout/drop vs connection refused distinction.
- Service-down behaviour while network reachability remains available.
- Bayesian normalization and monotonic updates for clear evidence.
- Entropy calculation, including all-zero/invalid input handling.
- Expected information gain for a known small belief distribution.
- Deterministic tie-break behaviour.
- Stopping conditions: confidence, low information gain, and probe budget.
- Explanation output does not claim evidence that was not observed.

### 17.2 Integration tests

- Start each template, inject each fault, and run all relevant probes.
- Perform an adaptive diagnosis from start to terminal state.
- Run baseline diagnosis with identical settings.
- Confirm that fault ground truth is used by evaluator only and not leaked to planner requests.
- Save and retrieve a diagnosis from persistence.
- Reset a lab and confirm that active faults are removed.

### 17.3 API tests

- Health endpoint.
- Valid and invalid lab creation.
- Valid and invalid fault injection.
- Step/run endpoint state transitions.
- Error response format.
- Probe budget maximum enforcement.
- Experiment validation and result export.
- Live-mode safeguards if live mode is implemented.

### 17.4 Frontend tests

- Dashboard empty state and loaded state.
- Topology selection and fault-activation form validation.
- Diagnosis controls call the API and display the returned state.
- Inconclusive result is displayed correctly.
- Experiment charts use API data rather than hardcoded values.
- Error and loading states.

### 17.5 End-to-end smoke test

A scripted happy path must work from a clean local start:

1. Open the app.
2. Create/load the campus topology.
3. Inject a DNS fault.
4. Start adaptive diagnosis.
5. See the evidence timeline and an appropriately qualified DNS diagnosis.
6. Run baseline comparison.
7. Open experiment results.
8. Export a report.

Also include smoke paths for a route failure and a TCP/service failure.

---

## 18. Acceptance Criteria / Definition of Done

The project is not done until the following are met.

### Functionality

- [ ] Backend starts with documented instructions and `/api/v1/health` returns healthy.
- [ ] Frontend starts and talks to the backend without CORS or runtime errors.
- [ ] At least two topology templates render and can be selected.
- [ ] At least eight fault scenarios can be applied and reset.
- [ ] At least six simulated probe types return structured evidence.
- [ ] The belief updater produces valid normalized probabilities.
- [ ] The planner calculates expected information gain for eligible probes.
- [ ] The actual next probe is selected by the adaptive planner.
- [ ] The diagnosis can terminate as confident, inconclusive, or budget exhausted.
- [ ] The UI shows evidence and alternatives instead of a diagnosis label alone.
- [ ] Fixed-order baseline uses the same probe implementations and stopping rule.
- [ ] Experiment metrics are computed from actual runs and exported.
- [ ] No dashboard metric or benchmark result is hardcoded.

### Quality

- [ ] Unit, integration, and API tests pass.
- [ ] Core type/schema validation is in place.
- [ ] No credentials/secrets are committed.
- [ ] Error states are handled and user actions do not silently fail.
- [ ] Logs avoid exposing sensitive values unnecessarily.
- [ ] Setup and troubleshooting instructions are verified on the target development environment.
- [ ] Simulated vs live evidence is clearly labelled.

### Academic deliverables

- [ ] Problem statement, goals, scope, architecture, and networking concepts are documented.
- [ ] The algorithm and information-gain equations are explained with a worked example.
- [ ] At least eight fault scenarios have documented expected observations.
- [ ] Experimental methodology, actual metrics, and limitations are reported.
- [ ] Screenshots come from the working application.
- [ ] A 3–5 minute demo script is provided.
- [ ] References are verified and consistently cited in the final report.

---

## 19. Development Milestones

Implement in this order. At the end of each phase, run and fix the relevant tests.

### Phase 1 — Repository and foundation

- Inspect repository, establish backend/frontend directories, dependency files, lint/format settings, and README.
- Create health endpoint and basic frontend shell.
- Verify backend/frontend start commands and API connection.

**Exit gate:** Both applications start locally; a health check is visible in the UI or demonstrably works through the API.

### Phase 2 — Virtual lab and fault model

- Implement typed node, link, routing, service, and fault models.
- Add the campus and multi-hop templates.
- Implement seeded fault injection and reset.
- Implement deterministic path and hop calculation.
- Unit-test each network behaviour before building the diagnosis UI.

**Exit gate:** A test can create a topology, inject a fault, run a relevant probe, and observe the expected structured outcome.

### Phase 3 — Probe engine

- Implement the common probe contract.
- Implement ICMP-style reachability, DNS, traceroute, TCP connect, MTU, and service-health simulator probes.
- Add a structured evidence format and source/mode tags.
- Write scenario tests for all fault types.

**Exit gate:** At least eight seeded scenarios have repeatable and distinguishable observations.

### Phase 4 — Probabilistic diagnosis and adaptive planner

- Implement hypothesis catalogue and explicit likelihood model.
- Implement probability normalization and evidence updates.
- Implement entropy, expected information gain, probe-cost handling, tie-breaking, and stopping rules.
- Implement a fixed-order baseline using the same probes.
- Add tests that demonstrate the next probe can differ from the baseline and that evidence changes the rankings.

**Exit gate:** A diagnosis can run from the command line/backend test without any frontend and returns evidence-backed beliefs and a terminal status.

### Phase 5 — Full-stack lab and diagnostic UI

- Implement topology visualization, template selection, fault form, and reset.
- Implement one-step diagnosis and run-to-conclusion controls.
- Show probe timeline, belief updates, selected-probe reasoning, final diagnosis, and uncertainty.
- Add accurate loading, failure, and empty states.

**Exit gate:** A reviewer can demonstrate a fault from the browser and observe real backend-generated diagnostic steps.

### Phase 6 — Experiment Studio

- Generate scenario cases and seeds.
- Run adaptive and baseline strategies under equivalent conditions.
- Compute top-1/top-3, probe count, coverage, inconclusive rate, localization accuracy, and confusion matrix.
- Render charts/table from API results.
- Export result records and an experiment summary.

**Exit gate:** Metrics can be regenerated from the stored run records, and repeating an experiment with the same configuration is reproducible.

### Phase 7 — Polish, optional extension, and validation

- Fix accessibility and responsive layout issues.
- Add report export and demo script.
- Add optional live probes and/or offline PCAP only if all required acceptance criteria pass.
- Run complete automated tests and smoke tests.
- Capture genuine screenshots and document known limitations.

**Exit gate:** A clean-clone setup succeeds from the README, all core tests pass, and the entire demo can be performed reliably.

---

## 20. Suggested Demonstration Script (3–5 Minutes)

### Demonstration A — DNS failure

1. Open the Campus Network template.
2. Show the client, routers, DNS server, web server and links.
3. Inject the DNS failure and explain that it is a controlled test fault.
4. Run the adaptive diagnosis one step at a time.
5. Show that the gateway/IP path remains reachable while hostname lookup fails.
6. Show the ranked hypotheses and the next probe selected, with its information-gain explanation.
7. Finish the diagnosis and review the evidence-backed result and recommended check.

### Demonstration B — Link or route failure

1. Reset the lab.
2. Inject a down link or route black hole at a selected point in the multi-hop topology.
3. Run diagnosis and show traceroute/path evidence.
4. Highlight the last responding hop or suspect segment.
5. Explain the distinction between localizing the fault to a component and merely observing that the destination is unreachable.

### Demonstration C — Measured evaluation

1. Open Experiment Studio.
2. Run a small reproducible scenario suite.
3. Compare adaptive probes with fixed-order probes.
4. Explain top-1 accuracy, average probes, and inconclusive rate.
5. State the actual counts and any limitations rather than claiming a result that is not supported by the run.

### Likely viva questions to prepare

- What is fault localization, and how is it different from anomaly detection?
- Why can ping failure not prove a host is down?
- How do DNS, routing, transport, and application failures differ?
- How do TTL and traceroute help find a path problem?
- What is Bayesian updating in this project?
- What is entropy, and why does expected information gain help select a probe?
- How is the fixed-order baseline constructed fairly?
- How is ground truth kept separate from the diagnosis engine?
- What are the limitations of the simulation model?
- How would the simulator be validated against a real Mininet or Linux namespace network?

---

## 21. Academic Report Structure

Generate a report outline that the team can complete with actual screenshots and experimental results:

1. Abstract.
2. Introduction and motivation.
3. Problem statement and objectives.
4. Scope and assumptions.
5. Related concepts and related work.
6. Computer Networks concepts used.
7. System requirements and architecture.
8. Virtual network and fault-injection design.
9. Diagnostic probes and evidence representation.
10. Bayesian hypothesis update and expected-information-gain algorithm.
11. Implementation and user interface.
12. Experimental setup, baseline, metrics, and reproducibility.
13. Results, charts, confusion matrix, and interpretation.
14. Limitations and threats to validity.
15. Conclusion and future work.
16. References.
17. Appendix: setup, API summary, test cases, and demo procedure.

Do not invent a related-work review, citations, evaluation figures, or screenshots. Verify the reference details and use official standards and tool documentation where appropriate. Include at least the relevant protocol concepts (IP, ICMP, DNS, TCP, path-MTU behaviour) and cite reliable standards/documentation in the final report.

---

## 22. Self-Assessment Rubric

Use this as a preparation checklist, not as a prediction of a professor's grade. Follow the official course rubric if one is provided.

| Area | Suggested weight | Evidence to show |
|---|---:|---|
| Problem statement and motivation | 10% | Clear need for root-cause diagnosis rather than generic monitoring |
| Computer Networks fundamentals | 20% | Correct treatment of IP, routing, DNS, ICMP, TCP, MTU and latency/loss |
| Design and differentiation | 15% | Adaptive evidence-guided test selection, uncertainty and explainability |
| Implementation correctness | 20% | Working simulator, probes, API, UI, faults, persistence and error handling |
| Experimental evaluation | 15% | Fair baseline, reproducible runs, metrics and honest interpretation |
| UI and demonstration quality | 10% | Interactive topology, evidence timeline, useful visuals and stable demo |
| Documentation and viva readiness | 10% | Architecture, algorithm, tests, report, citations and limitations |
| **Total** | **100%** | |

High-quality UI alone is not enough. The diagnosis algorithm, networking correctness, reproducible evaluation, and ability to defend design choices are essential.

---

## 23. Networking References to Verify and Cite

Use authoritative sources and verify the version/details before final submission. Candidate standards and documentation include:

- RFC 791 — Internet Protocol (IPv4).
- RFC 792 — Internet Control Message Protocol (ICMP).
- RFC 1035 — Domain Names: Implementation and Specification (DNS).
- RFC 1191 — Path MTU Discovery.
- RFC 9293 — Transmission Control Protocol (TCP).
- Official documentation for Python, FastAPI, NetworkX, React Flow, and Scapy.

Do not treat these references as a substitute for reading the relevant portions. Cite them where protocol behaviour or implementation choices are explained.

---

## 24. Final Instructions Before Declaring Completion

At the end of implementation, the coding agent must provide a concise handoff containing:

1. What was implemented (core vs optional features).
2. Exact setup and run commands for the backend and frontend.
3. The test command(s), how many tests ran, and the actual pass/fail summary.
4. A step-by-step demo scenario, including which fault to inject.
5. The location of generated reports/exports and how to regenerate experiment metrics.
6. Actual limitations and any feature not verified in the current environment.
7. A checklist mapped to Section 18 with any incomplete items marked honestly.

**Never claim full marks are guaranteed.** The goal is to produce a technically strong, testable, well-documented project that gives the student clear evidence to present and defend during evaluation.

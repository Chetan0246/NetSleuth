# Test plan and coverage

Tests are a core deliverable of this project, not polish. This document states what
is tested, at which layer, and which plan requirement each group covers.

> Counts in this document are the actual counts of the last recorded run of the
> suite. Re-run and compare:
> ```bash
> cd backend && python3 -m pytest -q      # backend: 477 tests
> cd frontend && npm run test             # frontend: 17 tests
> python3 scripts/smoke_test.py           # end to end: 38 checks
> ```

---

## How to run

| Command | Scope |
|---|---|
| `cd backend && python3 -m pytest tests/unit/test_audit_regressions.py -q` | Only the audit regressions |
| `cd backend && python3 -m pytest -q` | All backend unit, integration and API tests |
| `cd backend && python3 -m pytest tests/unit/test_lab_simulator.py -q` | One module |
| `cd backend && python3 -m pytest -k mtu -q` | Selection by name |
| `cd frontend && npm run typecheck` | TypeScript strict check |
| `cd frontend && npm run test` | Component tests |
| `cd frontend && npm run build` | Type check + production build |
| `python3 scripts/smoke_test.py` | Boots a real uvicorn server and walks the documented path over HTTP |

---

## Layer 1 — unit tests: the network model

`backend/tests/unit/test_lab_graph.py` (29 tests) — the topology and routing model.

- Graph construction and validation: duplicate node ids, duplicate IP addresses,
  dangling link endpoints, self-loops, parallel links, disconnected topologies, DNS
  records pointing at nonexistent IPs, services on non-app-server nodes, invalid
  MTU/IP/port ranges.
- Routing: deterministic shortest path under repeated evaluation, additive latency,
  minimum-over-links path MTU, down link removing the path, `NO_FIRST_HOP` reported
  separately from a mid-path failure, `last_reachable_node` bracketing a failure,
  the `nominal_path` used for localization, the documented link-id tie-break between
  two equal-hop paths, derived forwarding tables (including after a fault), and
  non-routers reporting no forwarding table.
- Templates: at least two, each valid, each with hosts/app servers/a resolver,
  `get_template` returning an independent deep copy, unique IPs, and an unknown
  template raising `NotFoundError`.

`backend/tests/unit/test_lab_faults.py` (31 tests) — fault injection.

- All ten fault types defined; link faults require a real link; a DNS fault must
  target a resolver; service targets must use `node_id:service_name` and the error
  lists the available services; `ROUTE_BLACKHOLE` requires `destination_node_id` and
  rejects a destination that is an endpoint of the affected link; `loss_rate`,
  `added_latency_ms` and `mtu_bytes` range validation; documented defaults applied.
- **Reversibility** for every mutation: link up/down, MTU, latency, loss, per-port
  policy, per-service health, DNS resolver enablement, black holes. Plus toggling an
  active flag, removing a fault twice, an unknown fault id, a black hole not
  affecting another destination, two simultaneous faults, and deterministic
  application order when faults are added in different orders.
- The same link fault works on both topologies.

`backend/tests/unit/test_lab_simulator.py` (54 tests) — protocol behaviour.

- Forwarding: healthy path resolution, localized link-down block, last-hop vs
  mid-path vs no-first-hop distinction, a black hole reported as a black hole, and a
  black hole not affecting other destinations.
- ICMP: RTT and zero loss on a healthy path, RTT growing with hop count, slow
  detection under added latency, partial loss distinguished from a total timeout, an
  ICMP error (not a silent timeout) for a down link, host-unreachable for a last-hop
  failure, a silent timeout for a black hole, **a suppressed ICMP reply not being
  reported as a dead host**, and the gateway selector targeting the gateway.
- DNS: successful resolution, case-insensitive lookup, NXDOMAIN for an unknown name
  with `resolver_reachable = True`, a resolver outage producing a timeout (not
  NXDOMAIN), an unreachable resolver path producing a timeout, and a DNS failure not
  breaking direct IP reachability.
- Traceroute: reaching the destination, localizing a mid-path failure, `NO_FIRST_HOP`
  behavior, hop count matching path length, four hops on the multi-hop topology, and
  silent hops being recorded explicitly.
- TCP: healthy handshake, blocked port timing out, rejected port refused by policy,
  a dead service resetting, **timeout and refusal being different outcomes**, an
  unreachable network short-circuiting TCP, a port fault not affecting another port,
  and added latency producing a slow handshake.
- MTU: all sizes passing on a healthy path, a black hole limiting success without an
  ICMP error, two different black-hole sizes, only oversized sizes failing,
  **packet loss producing a declared inconclusive outcome rather than an MTU
  verdict**, no path producing `UNREACHABLE`, and monotone successes for a real MTU
  limit.
- Service: healthy, a dead service while the network is healthy, a blocked port
  showing as a timeout, a rejected port showing as policy refusal, unreachability
  reported first, and a declared-unhealthy service stalling.
- Determinism: identical observations for identical seeds across three independent
  constructions, different seeds producing different loss patterns, and distinct tags
  giving independent samples.

## Layer 2 — unit tests: the probe engine

`backend/tests/unit/test_probes.py` (38 tests).

- All six probe types implemented; every probe returns the same contract with a
  non-empty outcome, summary, structured details and evidence; every probe labels
  its mode; every emitted outcome is inside the declared vocabulary; **the declared
  vocabulary matches the likelihood model exactly**; every probe has a documented
  relative cost; every evidence statement has a kind and a strength.
- Selector routing: each of the four ICMP selectors targets the right node.
- Evidence quality per probe: a filtered ICMP reply not claiming a dead host; partial
  loss reporting its counts; an ICMP error naming the suspect link; slow evidence
  naming the RTT; DNS evidence noting that IP paths still need testing after a
  resolver failure; NXDOMAIN distinguished from a timeout; traceroute stating that a
  trace localizes but does not prove link-vs-route; a lossy ladder stating that the
  pattern is not an MTU pattern and weakening the MTU hypothesis.
- **Scenario distinguishability**: the eleven injection profiles (ten faults plus
  healthy) produce at most one collision, and the permitted collision is the
  documented link-vs-route ambiguity. Individual signature checks pin the outcome
  each fault must produce in its own probe. Reproducibility of a profile under a
  fixed seed is asserted.

## Layer 3 — unit tests: the diagnostic engine

`backend/tests/unit/test_diagnosis_bayes.py` (34 tests) — the belief updater.

- Entropy: maximum on a uniform distribution, zero on a certain one, zero on all-zero
  and empty input, rejection of negative and non-finite values, scale invariance, and
  the two-hypothesis one-bit case.
- Normalisation: sums to one, preserves ratios, all-zero falls back to uniform.
- `BeliefState`: uniform documented priors, posterior summing to 1.0 after **every**
  observation in a sequence, every probability finite and in [0, 1] throughout, a
  supporting observation raising the ranking, counter-evidence lowering a hypothesis,
  conflicting evidence leaving a contested belief, the likelihood floor preventing
  collapse, the floor recorded, and zero-likelihood handling.
- Audit completeness: prior/observation/sequence/raw and floored likelihoods/
  posteriors/entropies all recorded, likelihood contribution computed, ranked order
  correct and stable, the deterministic tie-break verified against a uniform prior.
- `evidence_weight` softening (including a zero weight being a no-op), unknown probe
  and unknown outcome raising, custom priors normalised, reset restoring the prior,
  the history being append-only, and a nine-observation sequence remaining
  numerically stable with entropy monotone under consistent evidence.

`backend/tests/unit/test_diagnosis_engine.py` (54 tests) — likelihoods, EIG, planner,
baseline, stopping rule, catalogue.

- Likelihood table: valid, complete over the candidate keys, complete over the emitted
  outcomes, all weights positive and finite, every row covering the full hypothesis
  catalogue, **the default being neutral**, a healthy echo not refuting DNS/MTU/port
  hypotheses but strongly weakening forwarding hypotheses, unknown probe/outcome
  raising, the outcome distribution summing to one, and NXDOMAIN supporting DNS more
  than a timeout does.
- EIG: entropy before correct, zero when no uncertainty remains, zero when a probe
  cannot separate the current belief, never exceeding the current entropy across all
  candidates, **matching a hand-computed expectation for a two-hypothesis belief**,
  cost reducing the score but not the information, zero cost treated as one, ranking
  sorted and invariant to input order, and JSON serialisability.
- Candidates: the full context offering every canonical probe, at least six probe
  types, missing resolver/service removing the right candidate, **exclusions always
  carrying a reason**, executed probes excluded, and the exhausted case returning
  `None`.
- Selection: deterministic across repeated calls, **evidence-dependent** (five
  evidence sets not all choosing the same probe, with spot-checks), **deviating from
  the baseline order at least once over a real stepped sequence**, the reason
  containing the real numbers, and alternatives exposed.
- Baseline: the documented order, restriction to supported candidates with reasons,
  information gain not influencing the choice, `None` when exhausted, sharing the
  candidate universe, and the reason naming the fixed sequence.
- Stopping rule: continuing on an ambiguous belief, stopping `confident` once the
  margin is met, **a large but insufficient margin reported as inconclusive**,
  budget exhaustion as its own status, no probe left as inconclusive, negligible EIG
  stopping as inconclusive, a narrow lead not stopping as confident, configurable
  thresholds, and a serialisable payload.
- Catalogue: the nine required hypotheses present, `UNKNOWN` and `NO_FAULT_DETECTED`
  always available, every hypothesis documenting layers/summary/verification/
  remediation, and every fault type mapping to a documented hypothesis.

`backend/tests/unit/test_diagnosis_runner.py` (41 tests) — the runner, localizer and
explanations.

`backend/tests/unit/test_audit_regressions.py` (67 tests) — one class per defect found
by the deep audit, so deleting a fix cannot be mistaken for removing a redundant test:

- **Diagnosis lab isolation.** A diagnosis freezes a private deep copy of the lab and
  records the fault signature it started with; mutating the session mid-run no longer
  changes what a running diagnosis observes, and the API refuses to continue a run whose
  session faults changed (with a 409 naming both counts) instead of folding two network
  states into one posterior. All ten fault types are checked for exact state
  preservation, so the copy cannot double-apply a fault.
- **Diagnosis listing.** `GET /diagnoses` without a session filter returns each
  diagnosis exactly once, agreeing with `GET /diagnoses?session_id=`, and a run from a
  previous process renders the same shape as a live one.
- **Overview payload.** `/overview` now returns the same complete row as the diagnosis
  list, so the dashboard cannot render `"3/"` for the probe count or hide a stored
  conclusion.
- **TCP listener presence.** A port with no declared service reports
  `REFUSED_NO_LISTENER`, not a completed handshake; a declared-but-unhealthy service is
  still `DEGRADED_STALL`; injected port policies still win.
- **Fault parameter validation.** `inf`/`nan` latency and loss are rejected, and an MTU
  above the largest probed packet size is rejected because such a fault could never
  manifest — with a test asserting an accepted MTU *does* manifest.
- **Validation before mutation.** A rejected reset leaves the cached lab and storage
  consistent.
- **`nominal_path` purity.** Localization no longer flips `link.up` on the live topology.
- **Exact localization scoring.** The experiment runner requires exact component
  identity, and every scenario's expected component is asserted to be in the canonical
  form the localizer emits.
- **No dead code.** `ProbeCandidate` carries no unused members, and the duplicate
  outcome vocabulary is gone with the enums asserted as the single source of truth.

- Localization for a link fault from a partial trace, a DNS fault to the resolver, a
  service fault to `host:port`, an MTU fault to the limiting link, **packet loss
  reporting weak/no location rather than inventing a link**, an unresolved cause
  localizing nothing, no evidence localizing nothing, and a healthy result
  implicating no component.
- Explanations: a confident DNS explanation stating the cause; reasoning listing every
  probe with its information gain; the summary reporting the probe count and stopping
  reason; caveats always labelling the simulation and the ground-truth isolation;
  **no overclaiming words**; the validator rejecting a fabricated claim and proof
  language; the validator accepting every real generated explanation; contributions
  recording likelihood ratios.
- Runner: reaching a terminal state, one step executing exactly one probe, stepping
  after completion returning `None`, **no probe running twice**, the budget respected
  (including 1), a zero budget rejected, the belief normalised after every step,
  entropy never increasing, selection reasons recorded per step, both strategies
  using the same probe implementations with the same modes, the baseline following
  the documented order, the baseline taking at least as many probes in the MTU case,
  the same seed reproducing the probe sequence and outcomes, every step carrying
  structured details and evidence, the payload complete and serialisable,
  **ground truth absent from the engine's input** (fault ids not leaking into the
  payload; `ProbeRequest` having no fault field; the context carrying no `FaultType`),
  a healthy lab concluding `NO_FAULT_DETECTED`, and rejected candidates being
  explained.

## Layer 4 — integration tests

`backend/tests/integration/test_engine_integration.py` (50 tests) — the whole engine
without HTTP, which is the Phase 4 exit gate.

- **Every one of the ten fault types reaching its expected top hypothesis on the
  campus topology**, parameterised so all ten are reported individually, with the
  evidence chain printed on failure.
- **Every fault type also diagnosed on the multi-hop topology** (targets remapped).
- A healthy lab reporting `NO_FAULT_DETECTED` on both topologies; each fault being
  reversible and the lab then diagnosing as healthy; the same seed reproducing the
  diagnosis.
- Strategy comparison: both strategies running on every fault; sharing probe
  implementations with overlapping identical observations; sharing the stopping rule;
  and **the adaptive strategy using no more probes in total** across 5 seeds × 10
  faults.
- Persistence: a session round-tripping through SQLite; faults surviving a service
  restart (exercising the storage path, not the cache); reset removing faults; a
  diagnosis and its observations and belief snapshots persisted with matching counts
  and monotone entropy; **re-persisting not duplicating observations**; the stored
  payload containing no ground truth; unknown session/diagnosis raising
  `NotFoundError`; invalid targets and a self-diagnosis rejected; and reading a
  diagnosis from a previous process reporting an actionable error.
- Experiments: the plan counting runs correctly; metrics computed from real runs with
  matching record counts; **metrics recomputable from stored records**; the same
  configuration reproducing the metrics; different seeds changing the observed
  evidence; ground truth confined to the evaluator columns; the confusion matrix
  summing to the run count; accepted confusions never below strict top-1; the
  per-fault breakdown covering every injected class; exports non-empty with the right
  line counts; and a filter matching nothing raising rather than silently succeeding.
- Report export: the Markdown report containing the evidence and the simulation
  label, and the JSON report round-tripping.

## Layer 5 — API tests

`backend/tests/api/test_api.py` (66 tests) against the real FastAPI app with an
in-memory database.

- Health and catalogue: healthy response with version and model revision; live
  probing reported as disabled; the catalogue listing hypotheses, probes and all ten
  fault types; **the overview being genuinely empty on a fresh install**; the OpenAPI
  schema served.
- Lab: templates listed; session creation returning a full topology, parameter
  reference and forwarding tables; unknown and blank templates rejected; session
  retrieval and listing; structured 404s; fault injection accepted for valid targets
  and rejected for unknown targets, invalid parameters (with the offending field
  named) and unknown fault types; **every advertised fault actually injectable** and
  the offered set equal to the catalogue; toggling and removing; an unknown fault id
  rejected; replacing a fault id with a different type returning 409; reset clearing
  faults; reset honouring a new seed; session deletion; deleting an unknown session
  returning 404.
- Diagnosis: creation returning the planner's decision; one step returning evidence;
  run-to-completion returning a terminal state with a report; the
  `run_to_completion` flag on creation; stepping a finished diagnosis returning 409;
  unknown diagnosis 404; invalid source/destination/service rejected; self-diagnosis
  rejected; **the probe-budget ceiling and zero budget rejected**; the baseline
  comparison running both strategies with the same conditions documented, honouring a
  budget override, and working before the adaptive run; Markdown and JSON report
  export with an attachment header; an invalid report format rejected; diagnoses
  listed per session; the overview reflecting real activity; the hypothesis
  catalogue endpoint; and a multi-fault lab still diagnosable.
- Experiments: the scenario catalogue matching the injectable faults; the estimate
  counting runs **without executing them**; an oversized experiment **refused before
  it runs**; an empty strategy list and zero runs rejected; a filter matching nothing
  rejected; a small suite running with complete metrics; **adaptive at least matching
  the baseline** on a two-topology suite while using no more probes; retrieval with
  stored run counts; unknown experiment 404; listing; JSON/CSV/Markdown export with
  correct row counts; **metrics recomputable from stored records**; fault-type
  filtering; and a single-strategy run being allowed.
- Error contract: unexpected errors not leaking internals (asserted through a client
  with `raise_server_exceptions=False`, checking the body carries only the exception
  *type* and no path or traceback); a shared error shape across failures; the
  `NetSleuthError` payload; and unknown routes returning structured errors.
- CORS: the frontend origin allowed, an arbitrary origin not allowed.

## Layer 6 — frontend tests

`frontend/src/test/app.test.tsx` (17 tests). The backend is mocked at the **`fetch`
boundary**, and each assertion checks that the page renders what the API returned.
This is what makes it impossible for a page to pass by displaying a hardcoded value.

- Overview: the genuine empty state on a fresh install (with zeroed counters); the
  counters rendered from the payload; loading a recent session into the app shell;
  and the backend-unreachable notice.
- Lab: rendering only the templates the API returned; the empty saved-session state.
- Workbench: the guidance state with no session; **an inconclusive diagnosis rendered
  as inconclusive with the ranked alternative and the API's stopping reason**; and the
  planner's selection reason with the API's expected-information-gain value.
- Report: the "nothing to report yet" state before a diagnosis.
- Experiment Studio: **no sample data before a run** (explicitly asserting the page
  says so); and after a run, the metrics rendered from the payload including the
  accuracy percentages, the measured wall-clock time, the confusion-matrix cells and
  the export link pointing at the backend export endpoint.

## Layer 7 — end-to-end smoke test

`scripts/smoke_test.py` boots a real uvicorn server as a subprocess and drives it
over HTTP with no test framework involved. **38 checks**, covering:

1. Health and the simulation-mode labels.
2. Template listing and session creation with the full injectable fault set (10
   classes, 59 concrete targets on the campus topology).
3. A healthy control diagnosed as `NO_FAULT_DETECTED` and `confident`.
4. A DNS fault injected and diagnosed as `DNS_FAILURE` with the resolver localized at
   strong location confidence, all observations labelled, all selection reasons
   recorded and all evidence present.
5. The baseline comparison, asserting the two probe **orders differ**, both reach the
   same conclusion, and the response documents that only the order differed.
6. Markdown and JSON report export.
7. Lab reset clearing faults.
8. A route black hole diagnosed as `ROUTING_FAILURE` and localized to the affected
   link.
9. A dead service diagnosed as `APPLICATION_SERVICE_FAILURE` and localized to
   `host:port`.
10. The experiment suite: estimate, execution of every planned run, adaptive top-1 ≥
    baseline, adaptive mean probes ≤ baseline, the confusion matrix covering every
    run, per-class metrics present, and CSV/Markdown exports consistent.
11. Error handling: an unknown session returning a structured 404 and an unknown
    template rejected.

It exits non-zero on the first failed check.

---

## Coverage mapping to plan.md section 17

| plan.md requirement | Where it is covered |
|---|---|
| 17.1 graph creation and validation, duplicate IP, missing endpoint | `test_lab_graph.py` |
| 17.1 shortest-path/routing behaviour | `test_lab_graph.py::TestRouting` |
| 17.1 link-down and route-black-hole outcomes | `test_lab_simulator.py` (forwarding, ICMP, MTU), `test_frontend` via API |
| 17.1 DNS success, record absence, resolver outage | `test_lab_simulator.py::TestDnsLayer`, `test_probes.py::TestDnsProbe` |
| 17.1 packet-loss reproducibility with a seed | `test_lab_simulator.py::TestDeterminism` |
| 17.1 latency calculation and thresholds | `TestIcmpLayer`, `TestTcpLayer` |
| 17.1 MTU outcomes for small vs large packets | `TestMtuLayer`, `TestMtuProbe` |
| 17.1 TCP timeout/drop vs connection refused | `TestTcpLayer::test_timeout_and_refusal_are_different_outcomes` |
| 17.1 service-down while reachability remains | `TestServiceLayer::test_service_down_while_network_is_healthy` |
| 17.1 Bayesian normalisation and monotonic updates | `test_diagnosis_bayes.py` |
| 17.1 entropy including all-zero/invalid input | `test_diagnosis_bayes.py::TestEntropy` |
| 17.1 EIG for a known small belief | `test_diagnosis_engine.py::test_expected_entropy_after_matches_a_manual_calculation` |
| 17.1 deterministic tie-break | `test_diagnosis_engine.py::test_ranking_is_sorted_and_deterministic` |
| 17.1 stopping conditions (confidence, low EIG, budget) | `test_diagnosis_engine.py::TestStoppingRule` |
| 17.1 explanation does not claim unobserved evidence | `test_diagnosis_runner.py::test_validator_rejects_a_fabricated_claim` |
| 17.2 start each template, inject each fault, run relevant probes | `test_engine_integration.py::TestEveryFaultIsDiagnosed` (both topologies) |
| 17.2 adaptive diagnosis to terminal state | `test_engine_integration.py`, `test_diagnosis_runner.py` |
| 17.2 baseline with identical settings | `test_engine_integration.py::TestStrategyComparison` |
| 17.2 ground truth used by evaluator only | `test_ground_truth_is_absent_from_the_engine_input`, `test_context_does_not_carry_ground_truth`, `test_ground_truth_is_used_only_by_the_evaluator` |
| 17.2 save and retrieve a diagnosis | `TestPersistenceIntegration` |
| 17.2 reset a lab and confirm faults removed | `test_reset_removes_active_faults`, smoke test step 7 |
| 17.3 health endpoint | `TestHealthAndCatalogue` |
| 17.3 valid and invalid lab creation | `TestLabEndpoints` |
| 17.3 valid and invalid fault injection | `TestLabEndpoints` |
| 17.3 step/run state transitions | `TestDiagnosisEndpoints` |
| 17.3 error response format | `TestErrorContract` |
| 17.3 probe budget maximum enforcement | `test_probe_budget_ceiling_is_enforced`, `test_zero_probe_budget_is_rejected` |
| 17.3 experiment validation and export | `TestExperimentEndpoints` |
| 17.4 dashboard empty and loaded state | frontend `Overview page` |
| 17.4 fault form validation | `TestLabEndpoints::test_inject_fault_with_invalid_parameters_is_rejected`, frontend lab test |
| 17.4 diagnosis controls display returned state | frontend `Workbench page` |
| 17.4 inconclusive displayed correctly | frontend `renders an inconclusive diagnosis correctly` |
| 17.4 charts use API data | frontend `renders the metrics returned by the API` |
| 17.4 error and loading states | frontend `shows an error notice when the backend is unreachable` |
| 17.5 scripted happy path | `scripts/smoke_test.py` |
| 17.5 route-failure and TCP/service smoke paths | smoke test steps 8 and 9 |

## What is deliberately *not* tested, and why

- **Live probes.** No live-probe implementation exists in this build, so there is
  nothing to test. The health endpoint asserts `live_probe_enabled: false` to make the
  absence explicit rather than implicit.
- **PCAP parsing.** Not implemented (see `limitations.md`); no test pretends
  otherwise.
- **Chart pixel geometry.** The frontend tests assert the *data* rendered (via the
  metric tables and the payload-driven values) rather than SVG geometry, because
  geometry assertions are brittle and would not catch a wrong number. `ResizeObserver`
  is stubbed for jsdom in `src/test/setup.ts`, and the reason is commented there.
- **Performance and load.** The system is a lab-scale diagnostic tool; there is no
  throughput or concurrency requirement and no benchmark is claimed.

## Baseline (recorded before changes)

`python3 -m pytest` on the initial repository reported **no tests collected**: there
was no `tests/` directory. Every test in this document was added by this
implementation. The recorded baseline therefore contributes no pre-existing failures,
and there are currently no known-failing tests.

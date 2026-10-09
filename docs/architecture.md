# Architecture

## Layer responsibilities

```text
┌────────────────────────────────────────────────────────────────────────┐
│                    React + TypeScript UI (Vite)                        │
│  Overview │ Network Lab │ Diagnostic Workbench │ Report │ Experiments  │
│  - renders backend responses; holds no diagnostic rules                │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ REST/JSON  (typed API client in lib/api.ts)
┌───────────────────────────────▼────────────────────────────────────────┐
│                          FastAPI  /api/v1                              │
│  validation │ session API │ diagnosis API │ experiment API │ errors    │
│  routes_health  routes_lab  routes_diagnosis  routes_experiments       │
└───────┬───────────────────────┬───────────────────────┬────────────────┘
        │                       │                       │
┌───────▼─────────┐   ┌─────────▼──────────┐   ┌────────▼──────────────┐
│ Virtual lab     │   │ Diagnostic engine  │   │ Persistence           │
│ graph/templates │   │ hypotheses         │   │ SQLite (sqlite3)      │
│ routing         │   │ likelihoods        │   │ sessions, diagnoses,  │
│ faults (LabState│   │ bayes / EIG planner│   │ observations, beliefs,│
│ simulator       │   │ baseline / runner  │   │ experiments, runs     │
│ outcomes        │   │ localizer / explain│   │ export helpers        │
└────────┬────────┘   └─────────┬──────────┘   └───────────────────────┘
         │                      │
         └──────────┬───────────┘
                    │
        ┌───────────▼────────────┐
        │ Probe interface        │
        │ probes/base.py         │  one contract, six simulated probes
        │ probes/simulated.py    │  (no live-probe implementation in this build)
        └────────────────────────┘
```

## Module map

### `app/core`

| Module | Responsibility |
|---|---|
| `config.py` | Every threshold in one place: confidence threshold and margin, minimum information gain, **`MIN_PROBE_TYPES_FOR_NO_FAULT`**, probe budgets, MTU/latency/timeout constants, experiment limits, model and prior revision strings. |
| `errors.py` | `NetSleuthError` hierarchy carrying an HTTP status and a structured `{error: {code, detail, field, context}}` body, plus handlers for validation, HTTP and unexpected errors (which never leak a stack trace or an internal path). |

### `app/lab` — the virtual network

| Module | Responsibility |
|---|---|
| `graph.py` | Typed `Node`/`Link`/`Topology` models and `validate_topology` (duplicate ids/IPs, dangling endpoints, self-loops, parallel links, connectivity, DNS records pointing at real IPs, services only on app servers). |
| `templates.py` | The two topologies. `get_template` returns a validated deep copy, so a test or session cannot mutate the template itself. |
| `routing.py` | `RoutingTable`: deterministic lowest-hop Dijkstra with a `(hops, link-id-sequence)` ordering key, path latency/MTU accumulation, `nominal_path` (used for localization), `suspect_link`, `last_reachable_node`, and derived forwarding tables for display. |
| `outcomes.py` | The **shared outcome vocabulary** (`ProbeType`, six outcome enums, `PROBE_COSTS`, `CANONICAL_CANDIDATE_ORDER`). One module, so the likelihood table can be checked against the outcomes the probes can actually emit. |
| `faults.py` | `FaultType` (ten), `FaultConfig`/`FaultSpec`, per-type validation, auto-descriptions, and `LabState` — the mutable simulation state that applies active faults over a pristine snapshot so activation is strictly reversible. |
| `simulator.py` | `LabSimulator`: layer-by-layer observation generation (forwarding → ICMP → DNS → traceroute → TCP → path MTU → service health), seeded RNG per tag, and the structured state objects each probe converts into evidence. |

### `app/probes`

| Module | Responsibility |
|---|---|
| `base.py` | `ProbeRequest` (deliberately contains **no** fault information), `EvidenceStatement`, `ProbeObservation` (mode, structured details, modelled vs measured time), and the `ProbeRunner` protocol. |
| `simulated.py` | The six probes plus `KNOWN_OUTCOMES` (the outcome codes each probe may emit, asserted by tests against the likelihood table). |

### `app/diagnosis` — the engine

| Module | Responsibility |
|---|---|
| `hypotheses.py` | Nine hypotheses plus `NO_FAULT_DETECTED`, with layers, verification steps, remediation and the component kind they are about. Uniform, documented priors. |
| `likelihoods.py` | The explicit likelihood table `P(observation | hypothesis, probe)`, the neutral default, the coherence adjustment for the unfalsifiable "unknown" hypothesis, and `validate_likelihood_table`. |
| `bayes.py` | `BeliefState`: normalised Bayesian updating with a likelihood floor, entropy calculation, full audit records for every update. |
| `information_gain.py` | Pure EIG computation and deterministic candidate ranking. |
| `planner.py` | Candidate construction (including *why* a candidate is excluded), `select_next_probe`, `explain_selection`, and `evaluate_stopping_rule` with the breadth guard. |
| `baseline.py` | The fixed probe order, restricted to supported candidates. Shares the same candidate set. |
| `runner.py` | `DiagnosisRun`: the single sequencing implementation used by both strategies, step/run-to-completion, persistence payload, report assembly. |
| `localizer.py` | Turns structured observation details into a suspected component with a **separate** location confidence. |
| `explanations.py` | Rule-based report text, plus `validate_explanation`, which rejects any claim whose supporting observation is absent and any "proof" language. |

### `app/experiments` — evaluation

| Module | Responsibility |
|---|---|
| `scenarios.py` | The ground-truth catalogue (24 scenarios), `EXPECTED_HYPOTHESIS`, and `ACCEPTED_CONFUSIONS` (documented, protocol-justified confusions, reported separately from strict top-1). |
| `runner.py` | Executes the suite, persists every run, uses `seed + run_index` per run, builds both strategies from the same seed and fault. |
| `metrics.py` | Metrics computed **only** from stored run records, with the definitions stated next to the computation. |
| `exports.py` | Markdown/JSON diagnosis reports and Markdown/JSON/CSV experiment exports. |

### `app/storage`

| Module | Responsibility |
|---|---|
| `database.py` | `sqlite3` schema and access (sessions, diagnoses, observations, belief snapshots, experiments, experiment runs), thread-safe via an explicit lock, with `delete_diagnosis_observations` so re-persisting a run is idempotent. |
| `repository.py` | `SessionService` (create/load/mutate/reset, rebuilds `LabState` from storage, offers only validated injectable faults) and `DiagnosisService` (create/step/run/baseline, persistence of each step). |

## Data flow of one diagnosis

```text
POST /diagnoses
   │  validate session, source, destination, service, budget
   ├─ SessionService.lab_for(session_id)   → LabState (topology + active faults)
   ├─ DiagnosisRun.__post_init__
   │    ├─ LabSimulator(lab, seed=session seed)
   │    ├─ build_probe_registry(simulator)          ← six probes, one contract
   │    ├─ BeliefState(uniform priors)
   │    └─ resolve gateway / resolver / control target from the topology
   └─ _evaluate(select_next_probe(...))

POST /diagnoses/{id}/step
   ├─ planner: build_candidates(context)            ← no fault information
   ├─ for each candidate: EIG and EIG/cost          ← pure computation
   ├─ choose the best (deterministic tie-break)
   ├─ probe.run(request, lab) → ProbeObservation    ← the only place the lab is read
   ├─ BeliefState.observe(probe_key, outcome)       ← full audit record
   ├─ BeliefSnapshot persists ranked + likelihoods + entropy
   └─ evaluate_stopping_rule(...)                   ← confidence / breadth / budget / EIG

GET /diagnoses/{id}
   ├─ beliefs (ranked posteriors, entropy, lead)
   ├─ steps (observation, evidence, reason, entropies, modelled & measured times)
   ├─ suspected_component (kind, id, location confidence, bracketing evidence)
   ├─ rejected_candidates (with reasons)
   └─ report (rule-based explanation, validated against the observations)
```

## Persistence schema

```text
lab_sessions(id, name, template_id, template_name, mode, topology_json,
             active_faults_json, random_seed, created_at, updated_at)

diagnoses(id, session_id→lab_sessions, source_node_id, destination_node_id,
          destination_service, port, strategy, status, max_probes, probes_used,
          prior_config_version, model_version, random_seed, hostname,
          gateway_node_id, resolver_node_id, control_node_id, result_json,
          stopping_reason, started_at, completed_at)

probe_observations(id, diagnosis_id→diagnoses, sequence_number, probe_key,
                   probe_type, probe_label, mode, source_node_id,
                   destination_node_id, outcome, summary, details_json,
                   evidence_json, selected_reason, information_gain,
                   modelled_elapsed_ms, measured_wall_clock_ms, created_at)

belief_snapshots(id, diagnosis_id→diagnoses, observation_id, sequence_number,
                 ranked_hypotheses_json, likelihoods_json, entropy_before,
                 entropy_after, created_at)

experiments(id, name, config_json, status, total_runs, completed_runs,
            metrics_json, summary_json, created_at, completed_at)

experiment_runs(id, experiment_id→experiments, scenario_id, seed, template_id,
                source_node_id, destination_node_id, destination_service,
                strategy, actual_fault_type, actual_target_id,
                predicted_fault_type, predicted_probability, predicted_target_id,
                localized_target_id, localization_confidence, is_correct_top1,
                is_correct_top3, is_localization_correct, is_inconclusive,
                status, probes_used, elapsed_ms, ranked_json, created_at)
```

**Ground truth isolation.** `actual_fault_type` / `actual_target_id` exist only in
`experiment_runs`, written by the evaluator after a run completes. The diagnostic
tables (`diagnoses`, `probe_observations`, `belief_snapshots`) contain no injected
fault label, so a stored diagnosis can never be used to recover the answer.

## Design decisions worth defending

1. **One probe contract, six implementations.** The engine depends on
   `ProbeRunner`, never on simulator internals. Adding a probe means adding a class
   plus a likelihood row, not editing the algorithm.
2. **The outcome vocabulary is centralised.** `PROBE_COSTS`,
   `CANONICAL_CANDIDATE_ORDER` and the six outcome enums live in one module, so
   "does the model cover every outcome the probes can emit?" is a testable
   question — and a test asks it.
   (`tests/unit/test_probes.py::test_declared_vocabulary_matches_the_likelihood_model`)
3. **Both strategies are one code path.** `DiagnosisRun` differs only in the
   function used to pick the next probe. Interpretation, priors, likelihoods,
   stopping rule and budget are shared, which is what makes the experiment a fair
   comparison rather than a comparison of two systems.
4. **Ground truth never enters the engine.** Enforced structurally (the request
   model has no fault field) and by test.
5. **Structured errors over exceptions in the API.** Every failure mode has an
   error code and, where applicable, the offending field, so the UI can point at
   the problem instead of showing a generic message.
6. **Reversible faults by construction.** `LabState` captures a pristine snapshot
   and re-derives all state from the active fault list, so "reset" and "disable"
   cannot leave residue.
7. **Persistence as a projection.** The in-memory `DiagnosisRun` is the source of
   truth while a process lives; the database is rewritten from it on each step.
   That keeps the SQL simple and makes the stored record exactly what the API
   returned.

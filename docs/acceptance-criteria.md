# Acceptance criteria and verification

A line-by-line response to `plan.md` §18 (Acceptance Criteria / Definition of Done),
with the evidence for each item. Anything not met is marked **NOT MET** and explained;
nothing here is asserted without a command or a file behind it.

Recorded on the last verification pass of this implementation.

---

## How these were verified

```bash
# 1. backend tests
cd backend && python3 -m pytest -q
#    → 466 collected, 0 failed, 0 errors
#      (348 unit + 50 integration + 68 API)

# 2. frontend type check, tests and production build
cd frontend && npx tsc --noEmit            # → clean
cd frontend && npx vitest run              # → 17 passed (1 file)
cd frontend && npm run build               # → built in 2.2 s

# 3. end-to-end smoke test against a real uvicorn server
python3 scripts/smoke_test.py              # → 38/38 checks passed

# 4. live endpoint probe
python3 -m uvicorn app.main:app --port 8211
curl /api/v1/health /api/v1/lab/templates /api/v1/experiments/scenarios /api/v1/catalogue
#    → 200 for each; health reports live_probe_enabled: false

# 5. evaluation
python3 scripts/run_evaluation.py --runs-per-scenario 10
#    → 440 runs in 2.33 s
```

---

## Functionality

### ✅ Backend starts with documented instructions and `/api/v1/health` returns healthy

The README gives `python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000` from
`backend/`. Verified live: `GET /api/v1/health` → `200` with
`{"status": "healthy", "version": "1.0.0", "model_version": "netsleuth-likelihood-v1"}`.
`app.main` exposes a module-level ASGI `app` so the documented command works
unmodified. `scripts/run_dev.sh` additionally waits for the health endpoint before
starting the frontend.

### ✅ Frontend starts and talks to the backend without CORS or runtime errors

`npm run dev` serves on 5173 and proxies `/api` to 127.0.0.1:8000
(`vite.config.ts`). CORS is configured for `localhost:5173`, `127.0.0.1:5173`, `:4173`
and `127.0.0.1:4173`. Two API tests assert the frontend origin is allowed and an
arbitrary origin is not. The frontend uses same-origin relative URLs, so no CORS
preflight is involved in normal operation. The app shows an explicit banner when the
backend is unreachable (asserted in the frontend tests).

### ✅ At least two topology templates render and can be selected

`campus-basic` (Small Campus Network, 9 nodes / 8 links) and `multihop-wan`
(Multi-Hop Branch Network, 8 nodes / 7 links). `test_templates_are_valid_and_deep_copied`
and `test_every_template_has_hosts_servers_and_a_resolver` cover both. The Lab page
renders both via SVG with a shape+label legend.

### ✅ At least eight fault scenarios can be applied and reset

**Ten** fault types (`FaultType`): `LINK_DOWN`, `ROUTE_BLACKHOLE`, `DNS_FAILURE`,
`PACKET_LOSS`, `HIGH_LATENCY`, `MTU_BLACK_HOLE`, `TCP_PORT_BLOCKED`,
`TCP_PORT_REJECTED`, `SERVICE_DOWN`, `GATEWAY_UNREACHABLE`.

The campus topology offers **59 concrete validated targets** across those ten classes.
`test_every_advertised_fault_can_actually_be_injected` injects every advertised fault
and asserts the offered set equals the catalogue, so a listed button cannot 422.
Reversibility is covered by 20+ tests; `test_reset_removes_active_faults` and the
smoke test step 7 cover reset.

### ✅ At least six simulated probe types return structured evidence

Six: `ICMP_REACHABILITY` (with four selectors), `DNS_LOOKUP`, `TRACEROUTE`,
`TCP_CONNECT`, `MTU_PROBE`, `SERVICE_HEALTH`.
`test_all_six_probe_types_are_implemented` and
`test_every_probe_returns_the_same_contract` assert the count and the uniform contract
(outcome, summary, structured details, evidence list, mode, modelled and measured
timing).

### ✅ The belief updater produces valid normalized probabilities

`test_posterior_always_sums_to_one` checks the sum after every observation in a
sequence; `test_long_observation_sequence_stays_numerically_stable` checks nine
observations; every probability is asserted finite and within [0, 1]. The likelihood
floor prevents zero-collapse (`test_zero_likelihoods_do_not_collapse_the_belief`).

### ✅ The planner calculates expected information gain for eligible probes

`app/diagnosis/information_gain.py` computes EIG per candidate, and
`test_expected_entropy_after_matches_a_manual_calculation` verifies the value against a
hand-computed two-hypothesis case. A worked example with real numbers is in
`diagnostic-algorithm.md` §5.4–5.5.

### ✅ The actual next probe is selected by the adaptive planner

`DiagnosisRun._choose_next()` calls `select_next_probe` for the adaptive strategy and
`select_baseline_probe` for the baseline. The choice is not cosmetic:
`test_selection_actually_depends_on_the_belief` runs five evidence sets through the
planner and asserts they do not all choose the same probe;
`test_adaptive_order_can_differ_from_the_baseline_order` steps the planner and asserts
it deviates from the fixed order. The smoke test asserts the two strategies' probe
sequences differ on a live run.

### ✅ The diagnosis can terminate as confident, inconclusive, or budget exhausted

Four terminal statuses exist (`confident`, `inconclusive`, `budget_exhausted`,
`error`). All three specified ones are covered explicitly:
`test_stops_confidently_once_the_margin_is_met`,
`test_a_large_but_insufficient_margin_is_not_confident`,
`test_no_probe_left_is_inconclusive`, `test_budget_exhaustion_is_its_own_status`.

### ✅ The UI shows evidence and alternatives instead of a diagnosis label alone

Every page shows the ranked hypotheses with posteriors and priors, the per-step
evidence with kind/strength tags, the planner's selection reason with its EIG value,
the alternatives considered, the excluded candidates with reasons, the suspected
component with a separate location confidence, and the report's three separate lists
(supporting / weakening / not-explained). The Workbench test asserts an inconclusive
diagnosis renders as inconclusive alongside the ranked alternative and the API's
stopping reason.

### ✅ Fixed-order baseline uses the same probe implementations and stopping rule

`DiagnosisRun` is one implementation; only `_choose_next` differs. Asserted by
`test_strategies_share_the_same_probe_implementations` (overlapping identical
observations with `SIMULATED LAB` mode) and `test_strategies_share_the_stopping_rule`
(the same confidence and margin conditions hold for both).

### ✅ Experiment metrics are computed from actual runs and exported

`app/experiments/metrics.py` consumes only stored run records.
`test_metrics_are_recomputable_from_stored_records` and the corresponding API test
recompute the metrics from the database and compare. Exports in JSON, CSV and
Markdown; `test_export_as_json_csv_and_markdown` asserts the CSV has exactly
`runs + 1` lines.

### ✅ No dashboard metric or benchmark result is hardcoded

The Overview page renders `overview.stats` from the API and shows a genuine empty state
on a fresh install (`test_overview_is_empty_on_a_fresh_install`, and the frontend test
`renders the genuine empty state on a fresh install` asserts the zeroed counters).
Experiment Studio explicitly renders *"This page deliberately has no sample data"*
before a run, asserted by a frontend test. Metric strings such as `100.0%` and the
measured wall-clock time are asserted to come from the payload.

---

## Quality

### ✅ Unit, integration, and API tests pass

466 tests, 0 failures, 0 errors. Plus 17 frontend tests and 38 smoke checks.

### ✅ Core type/schema validation is in place

Pydantic models for every API request/response (`app/models/schemas.py`), every stored
entity (`app/lab/graph.py`, `faults.py`, `probes/base.py`), with field constraints and
validators. Topology integrity is validated by `validate_topology`; fault targets by
`validate_fault`; the likelihood table by `validate_likelihood_table`. TypeScript runs
in strict mode with `noUnusedLocals`/`noUnusedParameters` and passes cleanly.

### ✅ No credentials/secrets are committed

No credentials, tokens or keys anywhere. The only environment variable is
`NETSLEUTH_DB` (a database path). `backend/data/` and `*.sqlite3` are gitignored.

### ✅ Error states are handled and user actions do not silently fail

Every mutation on every page is wrapped so an `ApiError` surfaces as an alert with the
error code, the HTTP status and the offending field. Verified by
`test_inject_fault_with_invalid_parameters_is_rejected` (asserts
`error.field == "parameters.loss_rate"`), `TestErrorContract`, and the frontend
"backend unreachable" test. No button is decorative: the inject button is disabled
until a fault is selected, the step/run buttons are disabled in a terminal state, and
the experiment run button is disabled when the estimate exceeds the server limit.

### ✅ Logs avoid exposing sensitive values; internals do not leak to the browser

`test_unexpected_errors_do_not_leak_internals` uses a client with
`raise_server_exceptions=False` against a route that raises with a path in its message,
then asserts the response contains only `{"exception_type": "RuntimeError"}` and
neither `/home/secret`, `path.py` nor `Traceback`. The catch-all handler is documented
as never leaking stack traces or internal paths.

### ✅ Setup and troubleshooting instructions are verified on this environment

Every command in the README and this document was executed during the verification
pass: backend start, frontend start, both test suites, the build, the smoke test, the
evaluation script and live endpoint probes. The README's troubleshooting note names the
exact command for a dead-backend banner.

### ✅ Simulated vs live evidence is clearly labelled

Every observation carries `mode: "SIMULATED LAB"`, asserted by
`test_every_probe_records_the_mode_label` and
`test_every_step_records_structured_details_and_evidence`. `ModeBadge` renders it in
the UI on every page. `GET /api/v1/health` returns
`live_probe_enabled: false` and the simulation-mode string. The report export always
includes the `SIMULATED LAB` caveat. There is no live-probe code path, so live evidence
cannot be mixed in — the mechanism is an absence, not a filter.

---

## Academic deliverables

### ✅ Problem statement, goals, scope, architecture, and networking concepts are documented

`README.md` (problem, parts, boundaries), `docs/architecture.md`,
`docs/networking-concepts.md`, `docs/limitations.md`.

### ✅ The algorithm and information-gain equations are explained with a worked example

`docs/diagnostic-algorithm.md`: the hypotheses, the likelihood model and its design
rationale, the Bayes update with its safeguards, the EIG equations, and a **worked
example computed from the real code** (§5.4) with the per-outcome arithmetic shown by
hand for the `REACHABLE` case, plus the fresh-belief ranking table with real EIG values.

### ✅ At least eight fault scenarios have documented expected observations

All ten are documented in `docs/networking-concepts.md` (per-layer modelled behaviour)
and `docs/experiment-methodology.md` §2.1 (with the configuration variation per class).
`test_each_fault_has_a_diagnostic_signature_in_a_specific_probe` and
`test_fault_profiles_are_pairwise_distinguishable` pin the expected observable outcome
for each, and a per-fault injection table covers all ten on both topologies.

### ✅ Experimental methodology, actual metrics, and limitations are reported

`docs/experiment-methodology.md` states the design, seeding, metric definitions, the
pre-declared accepted confusions, the **actual results** (440 runs), an honest
interpretation including what the results do *not* support, the weakest class
(packet loss), the measured effect of the breadth-guard change (87% → 99.6%), and a
threats-to-validity table. `docs/experiments/latest.{json,csv,md}` hold the raw
generated artefacts.

### ⚠️ Screenshots come from the working application — NOT MET

No screenshots are committed. This environment could not capture a browser
screenshot, and fabricating one would be dishonest. The gap is recorded in
`docs/limitations.md` §5. What exists instead: the frontend tests assert the rendered
content of every page from mocked API payloads, and `docs/demo-script.md` describes
exactly what each page displays so screenshots can be captured in a few minutes by
running the demo.

### ✅ A 3–5 minute demo script is provided

`docs/demo-script.md`: three demonstrations (DNS failure, route/black-hole failure,
measured evaluation) with narration, the actual numbers to quote, an explicit
closing statement, and prepared answers to seven likely viva questions.

### ✅ References are verified and consistently cited

`docs/references.md` lists only sources actually consulted, with the specific protocol
detail each one supports. The RFC identifiers, titles and obsoletion relationships were
checked against the RFC Editor and IETF Datatracker pages (RFC 9293 obsoletes RFC 793;
RFC 1191 obsoletes RFC 1063). Where the simulator simplifies a standard, the
discrepancy is stated rather than hidden behind the citation.

---

## Required core scope (plan.md §4)

| Required core item | Status |
|---|---|
| Predefined topology selector with at least two templates | ✅ two templates, deep-copied and validated |
| Deterministic virtual-lab simulation with path traversal | ✅ metric-weighted path tree, validated |
| At least eight distinct injected fault scenarios | ✅ ten, 59 concrete campus targets |
| At least five diagnostic probe types | ✅ six (nine candidate keys with ICMP selectors) |
| Evidence records for each probe | ✅ structured details + typed evidence statements |
| Fault hypotheses with confidence/probability updates | ✅ ten hypotheses, normalized Bayes |
| Information-gain-based adaptive probe selection | ✅ EIG/cost with deterministic tie-break |
| A fixed-order baseline for comparison | ✅ same implementations, same stopping rule |
| Interactive web dashboard (topology, timeline, diagnosis, evaluation) | ✅ five pages |
| Persistent scenario/experiment history in SQLite | ✅ seven tables |
| Automated unit, integration and API tests | ✅ 466 backend + 17 frontend tests |
| Documentation, report export, working demo script | ✅ 8 docs, MD/JSON report export, demo script |

## Optional enhancements (plan.md §4) — not attempted

| Optional item | Status |
|---|---|
| Live ping / traceroute / DNS / TCP probes | **NOT IMPLEMENTED** — no code path; health reports `live_probe_enabled: false` |
| Offline PCAP upload and parsing (Scapy) | **NOT IMPLEMENTED** |
| Mininet / network-namespace adapter | **NOT IMPLEMENTED** |
| Additional topologies, IPv6, user-defined fault combinations | **NOT IMPLEMENTED** (multiple simultaneous faults are supported, but the model is single-fault calibrated) |
| PDF report export | **NOT IMPLEMENTED** — Markdown/JSON/CSV provided, which the plan states suffices for the core |

The plan's instruction was explicit that the core must not be sacrificed for optional
features. All optional items were therefore left unimplemented and are documented as
such rather than stubbed.

---

## Summary

| Category | Result |
|---|---|
| Functionality criteria | **14 / 14 met** |
| Quality criteria | **7 / 7 met** |
| Academic deliverables | **6 / 7 met** (screenshots missing, recorded) |
| Required core scope | **12 / 12 complete** |
| Optional enhancements | **0 / 5** (deliberately, per the plan's priority) |
| Backend tests | **466 passed, 0 failed, 0 errors** |
| Frontend tests | **17 passed** |
| Smoke test | **38 / 38 checks passed** |
| Frontend build | **succeeds** (644 kB / 182 kB gzipped) |
| Recorded evaluation | **440 runs**; adaptive top-1 **99.6%** at **4.20** probes vs baseline **71.4%** at **7.00** |

The one unmet deliverable is the screenshot set, which requires a browser this
environment does not provide. Everything else in §18 is met, and the incomplete items
are listed in `docs/limitations.md` rather than left implicit.

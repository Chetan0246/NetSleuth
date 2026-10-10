# Limitations and threats to validity

This document lists what this implementation does **not** do, what it cannot
conclude, and where its evaluation is weak. It exists so that the project's claims
stay bounded by the evidence.

---

## 1. Incomplete relative to plan.md

These items appear in `plan.md` and were **not** implemented. None of them is a
required-core item (plan.md §4), and no UI element pretends they exist.

| Item | Plan section | Status |
|---|---|---|
| Live ping / traceroute / DNS / single-port TCP probes | §16, §11 Page 6 | **Not implemented.** `health.live_probe_enabled` is `false`; there is no endpoint, adapter or subprocess that probes a real host. |
| Offline PCAP upload and parsing (Scapy) | §4 optional, §8 optional probes | **Not implemented.** No dependency on Scapy; no upload endpoint. |
| Mininet / network-namespace adapter | §4 optional | **Not implemented.** The lab is a software model, not a virtualized dataplane. |
| IPv6 | §7 optional | **Not implemented.** IPv4 only, validated with `ipaddress.IPv4Address`. |
| User-defined topologies (beyond the two templates) | §7.2 optional | **Not implemented.** Topologies are built-in templates; the lab page cannot author new nodes or links. |
| PDF report export | §4 optional | **Not implemented.** Markdown, JSON and CSV exports are provided; the plan states these suffice for the core. |
| Multi-fault *diagnosis* (several simultaneous injected faults) | §7.3 enhancement | **Partially supported.** Multiple faults can be injected, stored, applied and diagnosed, and `UNKNOWN_OR_MULTIPLE_CAUSES` exists for the ambiguous case. But the likelihood model is calibrated for **single-fault** explanations, so a two-fault lab may be reported as the more likely single cause or as inconclusive. No scenario in the evaluation suite injects two faults. |
| Calibrated probabilities | §9.2 | **Not done, deliberately.** The weights are engineering judgements; no fitting was performed, so "posterior 0.88" means "0.88 under this model's relative weights", not "88% of real-world probability". |

## 2. Simulator limitations

The virtual lab models selected behaviours. It is **not** a TCP/IP stack.

| Not modelled | Consequence for a diagnosis |
|---|---|
| Dynamic routing (OSPF/BGP), convergence, failover | Both templates are non-redundant, so a link failure removes the path. A real network might reroute and present a different symptom. |
| Subnets, longest-prefix match, ARP, L2 switching, VLANs | Routing is graph-based. "Correct route" is assumed from the topology. |
| Queueing and congestion; `bandwidth_mbps` is display-only | Loss is independent per link, not congestion-induced, so a "brownout" due to a saturated link cannot be reproduced. |
| TCP congestion control, retransmission timers, window scaling | The handshake RTT is derived from path latency; throughput behaviour is absent. |
| Real fragmentation and reassembly | The MTU layer simulates the DF/no-DF *decision*, not packet splitting. |
| ICMP rate limiting, reply suppression *policy* | Reply suppression is only modelled per node (`answers_icmp`) and per link for the MTU path. |
| Asymmetric paths | Links are symmetric, so forward and return paths are always the same. |
| Jitter beyond a small uniform term; no packet reordering | `HIGH_LATENCY` is deterministic in its mean. |
| Middleboxes, NAT, load balancers, anycast | A single destination address always maps to one node. |
| IPv6, tunnels, VPNs | Out of scope. |
| Real service protocols (HTTP, TLS, database wire protocols) | A service is a port with a health flag and a declared response size. |

**Where the model is most likely to mislead:** the relationship between *loss* and
*size*. In the model a lossy link fails a fitting size with a probability that does
not depend on the size, whereas real links often show size-dependent behaviour. The
ladder's `INCONCLUSIVE_NON_MONOTONE` outcome was added specifically to avoid turning
that simplification into a confident MTU diagnosis, but the underlying simplification
remains.

## 3. Diagnostic limitations

### 3.1 Causes that cannot be distinguished by end-to-end probes

- **`LINK_FAILURE` vs `ROUTING_FAILURE`.** Both mean "packets stop here". The engine
  separates them using ICMP error semantics (a router's error vs silence) and the
  control-target/gateway selectors, and the measured accuracy is 100% on both classes
  — but the two hypotheses are intentionally kept close in the likelihood table, and
  the confusion is declared as an *accepted confusion* rather than hidden. On a
  network that suppresses ICMP errors, this distinction genuinely degrades.
- **`TCP_FILTER_OR_PORT_FAILURE` vs `APPLICATION_SERVICE_FAILURE`.** A dropped port
  and a dead listener both prevent a connection. The engine separates them using
  whether the destination host *answered* (timeout vs reset) and the recorded port
  policy. A firewall configured to REJECT looks like a reset from the host.
- **Link-level packet loss location.** End-to-end probes cannot attribute loss to one
  link. The localizer reports `weak` or `none` location confidence in this case rather
  than guessing — but it also means "loss is somewhere on the path" is the best the
  tool can say.

### 3.2 Packet loss is the weakest class

Measured: 95% top-1 with a 25% inconclusive rate for the adaptive strategy (see
`experiment-methodology.md` §4.3). Two causes, both structural:

1. a 4-packet echo probe can miss 40% loss entirely by chance, and the MTU ladder sends
   only 3 attempts per size;
2. intermittent delivery is genuinely compatible with several causes, and the model
   says so, so the posterior often does not clear the 80%/20% separation.

No repeated-probe sampling estimator was implemented. This is the highest-value
improvement available.

### 3.3 Confidence is relative, not calibrated

Posteriors are normalised weights under the documented table. `0.88` means the
evidence favours that hypothesis by that ratio *within this model*. It is not a
frequency, and it must not be reported as a probability of a real-world outcome.

### 3.4 The breadth guard is a heuristic

`MIN_PROBE_TYPES_FOR_NO_FAULT = 4` prevents a premature "no fault" verdict. It is a
documented engineering guard, not a derived quantity, and 4 is a choice: with three
classes the engine could still conclude "healthy" having never tested the MTU layer.
Conversely the guard makes the healthy verdict more expensive than it needs to be
(4 probes instead of 3).

## 4. Evaluation threats

Full discussion in `experiment-methodology.md` §5. Summary:

| Threat | Assessment |
|---|---|
| Model–model circularity | The strongest threat. Lab behaviour and likelihood table share one author's protocol reasoning. The evaluation measures whether *selection* helps; it does not validate the likelihood values. |
| Uncalibrated parameters | No fitting, so no overfitting — but also no guarantee the weights are well chosen. |
| Small per-class samples | 20–40 runs per class in the recorded run; one flip changes a class accuracy by 2.5–5 points. |
| Single seed family | All recorded runs share one `base_seed`. |
| Strict top-1 semantics | The baseline rises from 71.4% to 85.0% under the accepted-confusion metric. Both are reported. |
| Compute time is not a real cost model | Simulated probes are nearly free, so planning overhead dominates. Probe count is the meaningful proxy. |
| Synthetic ground truth | Fault labels are the suite's own definitions; there is no external annotation. |
| No live-network validation | No result in this repository has been checked against a physical or virtualized network. |

## 5. Implementation limitations

- **Running diagnoses are in-process.** `DiagnosisService` keeps a `DiagnosisRun` in
  memory. A diagnosis whose process has exited cannot be resumed by `/step`; the
  stored record is still readable, and attempting to fetch a run from a previous
  process raises a `NotFoundError` that says so. There is no background job queue and
  no distributed execution.
- **Experiments are synchronous.** `POST /experiments/run` executes in the request.
  A run over 24 scenarios × 2 strategies × 3 runs completes in a few seconds, but a
  much larger suite would hold the request open. The run count is capped
  (`MAX_EXPERIMENT_TOTAL_RUNS = 4000`) and validated *before* execution; progress
  reporting through the `progress` callback exists in the runner but is not wired to
  an SSE/WebSocket endpoint.
- **SQLite with a process-wide lock.** Adequate for lab-scale single-process use.
  Concurrent multi-worker deployment is not supported.
- **No authentication or authorisation.** The API is for local lab use. It exposes a
  destructive `DELETE /lab/sessions/{id}` with no auth, which is why the UI gates it
  behind a confirmation and the README presents it as a local tool.
- **Frontend bundle size.** The production build is ~644 kB (182 kB gzipped), mostly
  Recharts. No code splitting is configured. It is a warning, not an error, and it
  does not affect functionality.
- **No screenshot in the repository.** The project plan asks for screenshots from the
  working application. None are committed here because this environment could not
  capture a browser screenshot. The README and `demo-script.md` describe what each
  page shows, and the frontend tests assert the rendered content, but a reviewer
  should capture their own screenshots by running the demo.
- **The repository has no committed git history.** The initial state contained no VCS
  metadata, so there is no commit history to review; file-level changes are described
  in the handoff instead.

## 6. Safety boundaries (by design, not by omission)

- **No scanning.** There is no port range, subnet sweep, banner grab or host discovery
  anywhere in the codebase. TCP probing targets one configured port.
- **No exploitation, evasion, traffic generation or denial of service.** Nothing in
  the project crafts packets or sends traffic to a real host.
- **No live mode in this build**, so there is no path by which a user could
  accidentally probe an unauthorised target.
- **No secrets.** No credentials, tokens or keys are used or committed; the only
  environment variables are `NETSLEUTH_DB` (database location).

## 7. What would be fixed first, given more time

In priority order:

1. **Repeated ICMP sampling with an explicit loss estimator** — the direct fix for the
   packet-loss class, and the only class where the adaptive strategy is inconclusive.
2. **A Linux network-namespace adapter** implementing the same `ProbeRunner` contract,
   so the engine could be evaluated against a real virtualized topology without
   changing the algorithm or the likelihood table — which would also be the first
   test of model–model circularity.
3. **Offline PCAP ingestion** as an alternative evidence source, letting the engine
   reason about observations it did not generate.
4. **A calibration/evaluation split** if the likelihoods were ever fitted, with the
   split documented.
5. **Multi-fault scenarios** in the suite, plus a model that explicitly represents
   combinations rather than relying on `UNKNOWN_OR_MULTIPLE_CAUSES`.
6. **Background experiment execution with progress reporting**, so a large suite does
   not occupy a request.

---

## 8. Defects found and fixed by the post-implementation audit

A deep audit was run after the implementation was first declared complete. It combined
an independent frontend/contract pass, an independent backend pass, and the author's
own review. It found **real defects that all 397 tests and the 38-check smoke test had
passed over** — which is itself the relevant lesson: a green suite proves the code does
what the tests check, not that the tests check the right things.

Every fix below has a regression test in
`backend/tests/unit/test_audit_regressions.py` (67 tests) or in the frontend
"Audit regressions" block (5 tests), so the defect cannot silently return.

### Correctness

| Defect | Impact before the fix | Fix |
|---|---|---|
| `DiagnosisRun` held a **reference** to the session's cached `LabState` | Injecting a fault mid-diagnosis changed the evidence of a run already underway: its first probe measured a healthy lab, its later probes measured a faulty one, and one Bayesian posterior was computed over the mixture. Reproduced: a run that observed `CONNECTED` for a healthy path concluded `DNS_FAILURE` after the fault was injected. | Each run freezes a private deep copy of the topology plus the fault signature it started with. `step`/`run` now refuse a run whose session faults changed, returning a 409 that names both fault counts, instead of folding inconsistent evidence into it. |
| `port_state` returned `open` for **every** port | `tcp_probe` mapped `open` + an RTT sample to `CONNECTED`, so a probe against a port with no listener reported a completed handshake and its evidence asserted "the port itself is working" — a **false observation fed into the Bayesian engine**. Reachable through an explicit `port` no service declares. | Listener presence is derived from the declared services. An unbound port is `service_down` → `REFUSED_NO_LISTENER`. A declared-but-unhealthy service still reports `DEGRADED_STALL` (a bound process that fails to serve is a different fault from a crashed one). |
| `nominal_path` flipped `link.up` on the **live** topology | Two evaluations sharing a topology could observe the all-links-up window and produce observations inconsistent with the real link state. | The predicate is overridden instead of the model mutated, making the call a pure read. |
| Experiment localization scored with a **bidirectional substring match** | Partial overlaps counted as correct localizations and inflated the reported accuracy (expecting `web-1:80` would accept a bare `web-1`; expecting `l-edge-core` would accept `edge`). | Exact component identity, plus a test asserting every scenario's expected component is in the canonical form the localizer emits. The recorded metrics are unchanged, so the docs remain accurate — with the latent risk now gone. |
| `inf` / `nan` passed fault validation | `added_latency_ms <= 0` is False for both, so they were accepted; `inf` then propagated into every RTT computation and into the JSON payload as `rtt_avg_ms: inf`. | `math.isfinite` checks on all numeric parameters, plus a ceiling on injected latency. |
| An MTU at or above the largest probed size was accepted | The packet-size ladder found every size "fitting" and returned `FULL_PATH_OK` while the fault was active — a **wrong diagnosis for a plausible parameter**. | Bounded to the largest probed packet size, with a test asserting an accepted MTU actually manifests as `LIMITED_DROP`. |
| `reset_session` cleared faults **before** validating `random_seed` | The error path left the cached lab cleared while storage still listed the faults. Single-threaded use recovered on the next re-sync, so the defect was the side-effect-before-validation pattern and a window for a concurrent reader. | Validate first, mutate second. |
| `GET /overview` hand-built its diagnosis rows | Five fields the frontend's type required were omitted, so the dashboard rendered a bare `"3/"` for the probe count, and a run read back from storage hardcoded `top_hypothesis: None`, hiding a conclusion it actually had. | The endpoint reuses the same summary builder as `GET /diagnoses`, so the two cannot drift. |
| `GET /diagnoses` without a session filter | A dead branch plus an O(n²) nested comprehension re-queried storage once per stored diagnosis. | A single `list_all` path sharing one summary builder. |
| CORS `allow_methods` omitted `PATCH` | The fault toggle uses PATCH. Invisible in development because the Vite proxy makes the app same-origin; would have failed first in a cross-origin deployment. | `PATCH` added, with a test asserting the allow-list covers every method the routers declare. |

### Frontend

| Defect | Impact before the fix | Fix |
|---|---|---|
| Switching sessions kept the previous session's diagnosis in context | Session B was displayed alongside Diagnosis A: a suspected component that does not exist in the new topology, a misleading status badge, and Step/Run operating on the old run. | Invalidation keyed on `session.id`. |
| Source/destination/service selections survived a session switch | The page held ids from the old topology: the `<select>` matched no option and `POST /diagnoses` returned 422 for an unknown node. | Re-default unconditionally on session change, keyed on `session.id`. |
| A diagnosed suspect rendered identically to an injected fault | On the very screens built to interpret results, "what you injected" and "what the diagnosis concluded" were visually indistinguishable, and the legend's blue swatch was unreachable. | Three distinct channels: red solid = injected fault, blue dashed = diagnosed suspect, grey = healthy. The legend states exactly that. |
| `useAsync` spread caller deps into the effect array | React requires a fixed dependency count; a caller with a varying-length `deps` array would throw at runtime. Latent (all callers pass `[]`). | Deps are serialised to one stable key; the stale payload is also cleared on a new request so a page cannot render old data beside a new failure. |
| Stale state: `baseline` snapshot survived `step`/`runToConclusion`; saved-session fault counts not refreshed after toggle/remove; a doc comment described service targets as `node:port` when they are `node:service_name`; "Nine fault classes" where there are ten; the terminal-status list duplicated across two pages | Stale comparison tables, stale counts, misleading comments and copy, and a status list that could drift from the backend. | Cleared/refreshed at the right points, corrected, and consolidated into `frontend/src/lib/status.ts`. |

### Dead code removed

`OUTCOMES_BY_PROBE` was a second, hand-maintained duplicate of the outcome vocabulary
that `KNOWN_OUTCOMES` already declares. The likelihood validator reads only the latter,
so the duplicate could drift unnoticed — it is replaced by `outcomes_for()`, derived
from the enums, with a test asserting the two agree exactly. `ProbeCandidate.score_key`
had a docstring promising "descending score, then cheaper, then key" while its body
returned `(0.0, 0.0, key)`; had it ever been adopted for tie-breaking it would have
silently ignored the score. It and the unused `repeatable` field were removed. Also
removed: an unreachable branch in the diagnosis service, a dead compatibility stub in
`app/main.py`, a bogus `__all__` export, and 45 unused imports.

### What the first audit did **not** change

The evaluation results were unchanged by the fixes above: 440 runs, adaptive top-1
**99.6%** at **4.20** probes versus baseline **71.4%** at **7.00**. That is the expected
outcome for fixes that close gaps in *unreachable or invalid* inputs and remove dead
code, rather than altering the likelihood model or the planner. A later audit did change
the recorded results — see §9.

---

## 9. Defects found and fixed by the second audit

A second deep audit was run over the finished implementation. Unlike the first, two of
its fixes change the **recorded experiment results**, so `docs/experiment-methodology.md`
and the artefacts in `docs/experiments/` were regenerated from the corrected engine.

The backend fixes each have a regression test in
`backend/tests/unit/test_audit_regressions.py` (78 tests), so the defect cannot silently
return. The frontend fixes are verified by `npm run typecheck` and the existing UI suite;
they do not add dedicated frontend regression tests.

### Correctness (metric-changing)

| Defect | Impact before the fix | Fix |
|---|---|---|
| Experiment localization **dropped runs that returned no component** | The metric divided correct localizations by only the runs that returned a component, so a run that ended inconclusive — or that named no component at all — was excluded instead of counted as a miss. Reported localization was 99.4% (adaptive) / 97.7% (baseline); recomputed honestly it is **80.0% / 65.0%** over all 200 component-scoped runs per strategy. | Every scenario that declares an expected component is always evaluated; a no-component or inconclusive result is a miss. |
| The baseline's **stopping rule consumed a placeholder information gain** | `select_baseline_probe` filled each candidate's EIG in from a uniform belief instead of the run's posterior, so the shared stopping rule stopped the baseline on a quantity the belief did not support. Baseline coverage moved from 81.8% to **71.8%** and mean probes from 7.00 to **6.80** once corrected. | The real belief is passed through; a uniform reference is used only when no belief is supplied. |

### Correctness (behaviour-preserving)

| Defect | Impact before the fix | Fix |
|---|---|---|
| `GATEWAY_UNREACHABLE` accepted **any link** as its target | The fault could be injected on a link that is no host's default gateway, producing a scenario whose label the topology cannot support. | Validated at injection: the target must be some host's default-gateway link. |
| An MTU **exactly equal to the largest probed size** was accepted | Residual of the first audit's fix, which used `>` instead of `>=`: the ladder found every size "fitting" and returned `FULL_PATH_OK` with the fault active. | Bounded with `>=`; a test asserts an MTU at the top of the ladder is rejected. |
| `tcp_probe` reported a **handshake RTT for a dropped SYN** | A `TIMEOUT_DROP` observation carried `handshake_rtt_ms` as if a handshake had completed, contradicting its own outcome. | The RTT is suppressed when the SYN is dropped. |
| Persisted probe **details were aliased**, not copied | The persistence layer wrote `source_node_id`/`destination_node_id` into the same dict the observation held, so exporting a run mutated the in-memory observation. | `ProbeStep.to_public` returns a copy of the details mapping. |
| The explanation generator's **"unexplained evidence" guard was always true** | Every non-forwarding diagnosis printed a "forwarding-layer block" warning even when the evidence contained none. | The guard checks the leader hypothesis before warning. |
| `_headline` formatted percentages with `:.0%` | A posterior of exactly 1.0 rendered as `"100%"`, which the forbidden-word guard rejects — a latent crash on a confident conclusion. | Formatted with `:.1%`. |
| Deleting a session left its **in-memory runs** behind | `DELETE /lab/sessions/{id}` removed the stored rows but `DiagnosisService` kept the live runs, so `get` kept serving a deleted diagnosis from memory. | `forget_session` purges matching runs; the route calls it. |

### Frontend and API contract

| Defect | Impact before the fix | Fix |
|---|---|---|
| Route-blackhole fault options **collided in the `<select>`** | Two faults on the same link rendered one `<option>`; selecting the second re-selected the first. | Options keyed by fault type + target + parameters. |
| Session-reset effects depended on **unstable objects** | Re-running on every render reset the user's selections mid-interaction. | Deps narrowed to `session?.id`. |
| The Workbench destination dropdown offered **nodes with no services** | Selecting one produced a request the lab cannot serve. | Filtered to nodes that declare services. |
| An **empty template selection** silently ran every template | "None selected" was treated as "all". | Legend states "none selected = all". |
| The **active-fault count** counted inactive faults | The heading overstated the injected faults. | Filtered by `is_active`. |
| `api.ts` **clobbered caller headers** | A caller-supplied header was overwritten by the JSON content-type. | Caller headers are spread last. |
| `getExperiment` was typed as the list item, not the detail | The detail page read fields the type did not declare. | Added `ExperimentDetail`. |
| `Overview` `openSession` had **no error handling or busy guard** | A failed open was silent and the button could be double-clicked. | try/catch/finally, disabled while opening, error notice with retry. |


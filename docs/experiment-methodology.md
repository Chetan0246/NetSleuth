# Experimental methodology and results

This document defines the evaluation, states the actual measured results, and lists
the threats to their validity. Every number below was produced by
`scripts/run_evaluation.py` and is stored in `docs/experiments/`.

> **Reproduce it:**
> ```bash
> python3 scripts/run_evaluation.py --runs-per-scenario 10
> ```
> Results in this document come from that exact invocation (seed `20261009`, 10 runs
> per scenario, probe budget 8, both strategies, 440 runs total).

---

## 1. Research question

> **Does selecting probes by expected information gain reach the same or better
> diagnostic conclusion than a fixed probe sequence, with fewer probes?**

Two things matter for this to be a fair test:

1. only the probe **selection order** may differ between the strategies;
2. ground-truth labels must never reach the engine.

## 2. Experimental design

### 2.1 Scenario suite

The suite is a catalogue of **24 scenarios**, of which this evaluation executed 22
(the filters were the default full set; two MTU scenarios share a fault class and are
included, so the counts below are by scenario). Each scenario fixes:

- the topology (`campus-basic` or `multihop-wan`),
- the source and destination, and the target service,
- the injected fault class, target and parameters,
- the expected hypothesis for that fault (the evaluator's ground truth),
- and, where the fault is component-specific, the expected component id.

Coverage per fault class, with the configuration varied so the suite is not one
memorised example per class:

| Fault class | Scenarios | Variation |
|---|---:|---|
| `LINK_DOWN` | 3 | near the source, near the destination, mid-path on the WAN topology |
| `ROUTE_BLACKHOLE` | 2 | two different links, two different destinations |
| `DNS_FAILURE` | 2 | both topologies |
| `PACKET_LOSS` | 2 | 0.4 on the campus uplink, 0.7 on the last-hop server link |
| `HIGH_LATENCY` | 2 | 400 ms campus, 900 ms long-haul WAN |
| `MTU_BLACK_HOLE` | 2 | 576 bytes (web flow), 1000 bytes (database flow, WAN link) |
| `TCP_PORT_BLOCKED` | 2 | web port 80, admin port 9090 |
| `TCP_PORT_REJECTED` | 1 | database port 5432 |
| `SERVICE_DOWN` | 2 | web service, API service |
| `GATEWAY_UNREACHABLE` | 1 | client default-gateway link |
| Control (no fault) | 2 | one per topology |

### 2.2 Seeding

Run index `i` uses `seed = base_seed + i`. Every run therefore gets a distinct but
reproducible packet-loss stream, and the same configuration reproduces the same run
records exactly. `tests/integration/test_engine_integration.py::test_same_configuration_reproduces_the_metrics`
asserts this.

For a given scenario and run index, **both strategies receive the same seed and the
same injected fault**, so any difference between them is attributable to probe
selection alone.

### 2.3 Strategies

- **adaptive** — `app/diagnosis/planner.select_next_probe`: highest EIG/cost score,
  deterministic tie-break.
- **baseline** — `app/diagnosis/baseline.select_baseline_probe`: the documented fixed
  order (destination ICMP → gateway ICMP → resolver ICMP → DNS → traceroute → TCP →
  MTU → service health → control-target ICMP).

Both share the probe registry, likelihood model, uniform priors, stopping rule
(including the breadth guard) and probe budget. The baseline simply skips candidates
the topology cannot support, recording each skip with a reason.

### 2.4 Metric definitions

| Metric | Definition |
|---|---|
| **Top-1 accuracy** | runs whose leading hypothesis equals the scenario's expected hypothesis, ÷ runs |
| **Top-3 accuracy** | runs whose expected hypothesis is among the three highest-ranked, ÷ runs |
| **Top-1 incl. accepted confusions** | top-1 hits plus the documented protocol-justified confusions, ÷ runs |
| **Diagnosis coverage** | runs that reached a non-inconclusive decision, ÷ runs |
| **Inconclusive rate** | runs that did not satisfy the stopping rule, ÷ runs |
| **Mean probes to decision** | mean executed probes per run (all runs, and conclusive only) |
| **Mean compute time** | mean *measured* wall-clock duration of the diagnosis computation — simulator time, not network latency |
| **Fault localization accuracy** | for runs with an expected component, runs whose suspected component matched, ÷ runs with an expected component. A run that ends inconclusive, or that names no component, counts as a **miss** — it is never dropped from the denominator |

### 2.5 Accepted confusions

Declared **before** the results were inspected, in
`app/experiments/scenarios.py::ACCEPTED_CONFUSIONS`:

| Ground truth | Also accepted | Protocol reason |
|---|---|---|
| `LINK_FAILURE` | `ROUTING_FAILURE` | Both mean "packets stop here". End-to-end probes distinguish them only through ICMP error semantics, which is not always decisive. |
| `ROUTING_FAILURE` | `LINK_FAILURE` | Same, in the other direction. |
| `TCP_FILTER_OR_PORT_FAILURE` | `APPLICATION_SERVICE_FAILURE` | A dropped port and a dead service can both refuse or reset the transport. |
| `APPLICATION_SERVICE_FAILURE` | `TCP_FILTER_OR_PORT_FAILURE` | Same, in the other direction. |

This metric is reported **alongside** strict top-1, never instead of it. It exists
because an evaluation that hides a systematic, protocol-explainable confusion behind
a single accuracy number is less honest than one that names it.

## 3. Actual results

**Configuration:** seed `20261009`, 10 runs/scenario, probe budget 8, 22 scenarios × 2
strategies × 10 runs = **440 runs**, model revision `netsleuth-likelihood-v1`, priors
`netsleuth-priors-v1`. Measured suite wall-clock time: **2.84 s**.

### 3.1 Headline

| Strategy | Runs | Top-1 | Top-3 | Top-1 incl. confusions | Mean probes | Coverage | Inconclusive | Mean compute ms | Localization |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **adaptive** | 220 | **99.55%** | 100% | 99.55% | **4.20** | **97.7%** | **2.3%** | 6.05 | 80.0% (n=200) |
| **baseline** | 220 | 71.36% | 100% | 85.00% | 6.80 | 71.8% | 28.2% | 2.70 | 65.0% (n=200) |

The adaptive strategy reached a **28.2 percentage point** higher top-1 accuracy while
using **2.60 fewer probes on average** (a 38% reduction) and reaching a decision in
**97.7%** of runs instead of 71.8%.

### 3.2 Per fault class

| Expected cause | Adaptive top-1 | Baseline top-1 | Adaptive mean probes | Baseline mean probes |
|---|---:|---:|---:|---:|
| `APPLICATION_SERVICE_FAILURE` | 100% (20/20) | 100% (20/20) | 2.00 | 8.00 |
| `DNS_FAILURE` | 100% (20/20) | 100% (20/20) | 4.00 | 4.00 |
| `HIGH_LATENCY` | 100% (20/20) | 100% (20/20) | 5.00 | 7.00 |
| `LINK_FAILURE` | 100% (40/40) | **25%** (10/40) | 4.50 | 6.75 |
| `MTU_BLACK_HOLE` | 100% (30/30) | **0%** (0/30) | 4.00 | 6.00 |
| `NO_FAULT_DETECTED` (control) | 100% (20/20) | 100% (20/20) | 4.00 | 6.00 |
| `PACKET_LOSS` | **95%** (19/20) | 85% (17/20) | 6.25 | 7.85 |
| `ROUTING_FAILURE` | 100% (20/20) | 100% (20/20) | 5.00 | 8.00 |
| `TCP_FILTER_OR_PORT_FAILURE` | 100% (30/30) | 100% (30/30) | 3.33 | 7.67 |

### 3.3 Confusion matrix (all 440 runs, both strategies)

| Actual ↓ / Predicted → | `APP_SVC` | `DNS` | `HIGH_LAT` | `LINK` | `MTU` | `NO_FAULT` | `LOSS` | `ROUTING` | `TCP_FILTER` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `APPLICATION_SERVICE_FAILURE` | **40** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `DNS_FAILURE` | 0 | **40** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `HIGH_LATENCY` | 0 | 0 | **40** | 0 | 0 | 0 | 0 | 0 | 0 |
| `LINK_FAILURE` | 0 | 0 | 0 | **50** | 0 | 0 | 0 | 30 | 0 |
| `MTU_BLACK_HOLE` | 0 | 0 | 0 | 0 | **30** | 30 | 0 | 0 | 0 |
| `NO_FAULT_DETECTED` | 0 | 0 | 0 | 0 | 0 | **40** | 0 | 0 | 0 |
| `PACKET_LOSS` | 0 | 0 | 0 | 0 | 4 | 0 | **36** | 0 | 0 |
| `ROUTING_FAILURE` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **40** | 0 |
| `TCP_FILTER_OR_PORT_FAILURE` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **60** |

### 3.4 Localization

| Strategy | Evaluated runs | Correct component |
|---|---:|---:|
| adaptive | 200 | 80.0% |
| baseline | 200 | 65.0% |

Every non-control scenario declares an expected component, so all 200 runs per strategy
are evaluated: a run that ends inconclusive, or that returns no component at all, counts
as a localization miss rather than being dropped. Both denominators are therefore equal
and the two rates are directly comparable.

The per-class and per-scenario records show where the misses are. The localizer names a
component for hard, link-scoped failures (link down, route black hole, MTU black hole,
gateway unreachable) and for DNS, service and port faults. It reports **no component**
for `HIGH_LATENCY` and `PACKET_LOSS`, so those scenarios are counted as misses. The
baseline's `MTU_BLACK_HOLE` localization is 0% for a different reason: its leading
hypothesis is `NO_FAULT_DETECTED`, so the localizer has no component to name. The
headline rate is therefore a localization rate over *all* component-scoped scenarios,
not only the ones the localizer answers — which is the honest reading.

## 4. Honest interpretation

### 4.1 What the results support

The adaptive planner's advantage in this model is **real, measured over 440 runs, and
reproducible** from the stored configuration. Its mechanism is identifiable from the
records, not asserted:

- **The MTU black hole (0% → 100%) is the clearest case.** The baseline runs six
  probes before the MTU ladder. On an MTU fault those six all return healthy, the
  belief accumulates towards `NO_FAULT_DETECTED`, and the confidence condition fires
  before the ladder is reached. The adaptive planner measures the ladder early
  because, among the probes it has not yet run, the ladder has one of the most
  *unpredictable* outcome distributions.
- **The link failure (25% → 100%) has the same shape**, plus a second effect: 30 of
  the 40 baseline link-failure runs are classed as `ROUTING_FAILURE`, which is the
  declared accepted confusion. The baseline is inconclusive in all three link-down
  scenarios — it never reached a decision there, and the leading hypothesis at the
  budget limit was the equally-plausible routing explanation. (The fourth scenario in
  the class, the gateway-unreachable one, it does resolve.)
- **`PACKET_LOSS` (85% → 95%) is the hardest class for both**, and the adaptive
  strategy is inconclusive in 25% of its packet-loss runs. This is a genuine
  limitation, not a rounding artefact — see §4.3.

### 4.2 What the results do *not* support

- **No claim of general superiority.** The advantage is measured inside one
  simulator with one likelihood model and a 24-scenario suite. A planner guided by
  this likelihood table is naturally advantaged when the lab's generative model
  matches that table — which it does by construction. The honest claim is:
  *information-gain-guided selection dominates a fixed order in this model, and the
  reason is that it front-loads the probes whose outcomes are most uncertain.*
- **No claim that 99.6% would hold on a real network.** The lab is deterministic
  given a seed, has no congestion, no route flaps and no ambiguity from partial
  observability of a multi-tenant network.
- **No claim about wall-clock efficiency.** The adaptive strategy's mean compute time
  (6.05 ms) is *higher* than the baseline's (2.70 ms) because the EIG computation
  costs more than the probes it saves in a simulator where a simulated probe is
  nearly free. In a real network where each probe costs seconds to minutes of
  round-trip time and operator attention, running 2.6 fewer probes would dominate the
  planning cost — but this evaluation cannot demonstrate that, and it does not try
  to. The relative probe *costs* in `PROBE_COSTS` are engineering weights, not
  measurements.
- **No claim that 100% top-3 means top-3 is uninteresting.** Top-3 is 100% for both
  strategies, so it does not separate them here. It is retained because it is the
  metric that would degrade first if the model were made harder, and because
  "the right answer was in the shortlist" is a genuinely useful property for a
  human-in-the-loop tool.

### 4.3 Where the system is genuinely weak

**Moderate packet loss.** `campus-packet-loss-moderate` (40% loss) is the only
scenario where the adaptive strategy was inconclusive, and it is also the scenario
with the lowest top-1 accuracy (adaptive 90%, baseline 70%).

Two mechanisms, both honest:

1. **A lossy path can look healthy on a small sample.** With 40% loss, an echo probe
   sending 4 packets has a real chance of getting all 4 through, and the MTU ladder
   sends 3 attempts per size. The run then legitimately observes no loss.
2. **Loss is genuinely ambiguous at the address level, and the model says so.** The
   `ICMP_REACHABILITY:destination` `PARTIAL_LOSS` row raises `PACKET_LOSS` and also
   raises `MTU_BLACK_HOLE` (1.1) and `UNKNOWN_OR_MULTIPLE_CAUSES` (1.3), because
   intermittent delivery is compatible with several causes. The `ICMP_REACHABILITY:control_destination` and
   `ICMP_REACHABILITY:gateway` selectors help — a lossy *transit* link shows up as
   the control target also being lossy while the gateway is clean — but with only
   two of the nine probes carrying loss evidence, the posterior often does not clear
   the 80%/20% separation. The engine then correctly reports `inconclusive` rather
   than guessing.

Increasing the packet count per probe, or permitting a documented number of repeated
ICMP rounds with the observations combined by an explicit estimator, would be the
right fix. It is not implemented, and the current behaviour is reported as-is:
**95% top-1 with a 25% inconclusive rate on this class**.

**The baseline's MTU blind spot is a property of the ordering, not evidence that the
baseline is a straw man.** The fixed order is the natural "check the obvious things
first" sequence, and it is exactly what a first-line troubleshooter would run. The
result that a naive healthy-path-first ordering *misses* MTU and link faults is a
finding about the ordering, and it is the direct analogue of the real-world problem
this project is about.

### 4.4 A change that measurably mattered

The stopping rule's **breadth guard** (`MIN_PROBE_TYPES_FOR_NO_FAULT = 4`, see
`diagnostic-algorithm.md` §6) was added after an intermediate evaluation run showed a
specific defect: the engine reported `NO_FAULT_DETECTED` at ~83% confidence after
three healthy probes, having never run the MTU ladder or the service health check.
Adaptive top-1 accuracy on the same suite was **87%** before the change and **99.6%**
after, with all control-run inconclusives eliminated. The change is documented
because it is the clearest example in this project of a modelling omission that
looked like a confident answer.

## 5. Threats to validity

| Threat | Assessment |
|---|---|
| **Model–model circularity** | The likelihood table was written from protocol reasoning, but the lab's generative behaviour is also protocol-reasoned by the same author. The evaluation measures whether *selection* helps under this model; it does not validate the likelihood values independently. |
| **Parameters not calibrated** | Likelihood weights and priors are engineering judgements with no fitted parameters. No calibration/evaluation split was needed because no fitting occurred. If calibration were added, the split would have to be disjoint and documented here. |
| **Small per-class sample** | 10 runs per scenario gives 20–40 runs per fault class. A single flip changes a class accuracy by 2.5–5 points. Per-class numbers should be read with their `n`, which is why every table shows it. |
| **Single seed family** | All runs derive from one `base_seed`. Different base seeds shift which packet-loss samples occur; `campus-packet-loss-moderate` is the class most sensitive to this. |
| **Simulator ≠ network** | No congestion, no jitter beyond a small uniform term, no route flaps, no asymmetric paths, no middleboxes, no IPv6. Findings about *which probe class localizes a fault* transfer better than findings about *how many probes are needed*. |
| **Metric choice sensitivity** | Top-1 is strict. With the accepted-confusion metric the baseline rises from 71.4% to 85.0%. Both are reported; neither is hidden. |
| **Compute-time comparison is not a real cost model** | Simulated probes cost microseconds. The measured compute time reflects planner overhead, not diagnostic cost. The probe-count metric is the meaningful efficiency proxy here. |
| **Synthetic ground truth** | Fault labels are the suite's own definitions. There is no external annotation to check them against. |

## 6. Reproducing and regenerating

```bash
# Full evaluation (writes docs/experiments/results-*.json, runs-*.csv, report-*.md)
python3 scripts/run_evaluation.py --runs-per-scenario 10

# Larger sample for a final report
python3 scripts/run_evaluation.py --seed 20261009 --runs-per-scenario 30

# Keep the SQLite database for inspection
python3 scripts/run_evaluation.py --runs-per-scenario 10 --keep-database
```

To recompute the metrics from already-stored runs (demonstrating they are not
computed anywhere else), use the API:

```bash
GET /api/v1/experiments/{experiment_id}          # includes metrics + stored_run_records
GET /api/v1/experiments/{experiment_id}/export?format=json   # full summary
GET /api/v1/experiments/{experiment_id}/export?format=csv    # one row per run
```

`tests/integration/test_engine_integration.py::test_metrics_are_recomputable_from_stored_records`
and
`tests/api/test_api.py::TestExperimentEndpoints::test_metrics_can_be_recomputed_from_the_stored_records`
both assert this property, so an API metric that disagreed with the stored records
would fail the suite.

## 7. Files produced by the last run

| File | Contents |
|---|---|
| `docs/experiments/latest.json` | Full summary: config, plan, every metric, per-scenario detail, confusion matrix, per-scenario variation |
| `docs/experiments/latest.csv` | One row per stored run record with ground truth, prediction, localization, probe count and timing |
| `docs/experiments/latest.md` | Markdown report with all metric tables |
| `docs/experiments/results-<stamp>.json`, `runs-<stamp>.csv`, `report-<stamp>.md` | The same three artefacts, stamped so multiple runs can coexist |

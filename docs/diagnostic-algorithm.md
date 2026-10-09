# Diagnostic algorithm

This document specifies the algorithm in `backend/app/diagnosis/`: the hypothesis
catalogue, the likelihood model, the Bayesian belief updater, the
expected-information-gain probe planner, the stopping rule, the localizer and the
explanation generator. It ends with a **worked example computed from the real code**.

The method is an **explainable probabilistic diagnostic engine**. No trained model is
involved, and the phrase "AI-powered" is not used.

---

## 1. The inference problem

Given a symptom ("the client cannot reach the web service by name"), several
different faults can produce it:

| Fault | What a naive check sees |
|---|---|
| DNS outage | "the site doesn't load" |
| Link down | "the site doesn't load" |
| Route black hole | "the site doesn't load" |
| Packet loss | "it's flaky" |
| High latency | "it's slow" |
| Path-MTU black hole | "small requests work, large ones hang" |
| Filtered TCP port | "connection times out" |
| Dead service | "connection times out" |

The symptom is ambiguous. The engine's job is to reduce that ambiguity with the
fewest, most informative observations, and to say clearly when it cannot.

## 2. Hypotheses

Nine hypotheses plus a healthy verdict (`app/diagnosis/hypotheses.py`):

| Code | Layers | Component kind |
|---|---|---|
| `LINK_FAILURE` | Link (L1/L2) / Network (L3) | link |
| `ROUTING_FAILURE` | Network (L3) | link |
| `DNS_FAILURE` | Application (L7) / DNS | node |
| `PACKET_LOSS` | Network (L3) / Link (L2) | link |
| `HIGH_LATENCY` | Network (L3) / Link (L2) | link |
| `MTU_BLACK_HOLE` | Network (L3) / MTU | link |
| `TCP_FILTER_OR_PORT_FAILURE` | Transport (L4) | service |
| `APPLICATION_SERVICE_FAILURE` | Application (L7) | service |
| `NO_FAULT_DETECTED` | all | none |
| `UNKNOWN_OR_MULTIPLE_CAUSES` | all | none |

Each hypothesis documents its layers, a summary, verification steps, remediation
options and the component kind it is *about* (which the localizer uses to decide
what it is allowed to name).

**Priors are uniform** (`1/10` each). The engine has no reliable frequency data for
this lab; using unequal priors would embed unverifiable assumptions in the result.
The prior revision is recorded in every stored run and export
(`PRIOR_CONFIG_VERSION`).

## 3. The likelihood model

The model is the table `P(observation | hypothesis, probe)` in
`app/diagnosis/likelihoods.py`. It is **data**, not code, so it can be reviewed and
diffed, and it is validated: `validate_likelihood_table()` asserts that every
candidate probe key has a row, that every outcome the probes can emit (from
`app.probes.simulated.KNOWN_OUTCOMES`) has an entry, and that every weight is
positive and finite.

### 3.1 The neutral default is the key design choice

`DEFAULT_LIKELIHOOD = 0.9`: a hypothesis a row does not mention is *roughly
unaffected* by that observation.

This matters more than any individual number. A probe observation is informative
about **the layer it tests**. A successful echo request rules out a down link, but it
is entirely consistent with a DNS outage, a blocked port or an MTU black hole. Had
the default been small (say `0.02`), that one healthy result would make almost every
hypothesis look impossible at once and the engine would report "confident" after a
single probe. That is precisely the failure mode that makes an automatic diagnosis
untrustworthy, and it also destroys the meaning of the "mean probes to decision"
metric.

With a neutral default, **a pattern of observations** produces confidence, not one
lucky result. This is directly testable:
`test_a_healthy_echo_does_not_refute_dns_or_mtu_hypotheses`.

### 3.2 The coherence adjustment

`UNKNOWN_OR_MULTIPLE_CAUSES` means "the evidence does not point at one known single
cause". Left unmodelled it is **unfalsifiable**: it is consistent with every
observation, so it permanently absorbs probability mass and a textbook single fault
can never clear the confidence threshold.

The correction encodes one observable principle, applied per outcome rather than per
row (so it lives in exactly one place, `_COHERENCE`):

- an outcome that is a **coherent, specific reading** makes "something outside the
  catalogue" *less* likely — e.g. `REACHABLE`, `HEALTHY`, `COMPLETE`,
  `FULL_PATH_OK`, `NXDOMAIN`, `LIMITED_DROP` (multiplier 0.55–0.60);
- an outcome that is **compatible with several causes at once** makes it *more*
  likely — e.g. `TIMEOUT`, `PARTIAL`, `UNREACHABLE_*`, `PARTIAL_LOSS`
  (multiplier 1.10–1.30).

`NO_FAULT_DETECTED` gets the complementary multiplier, so accumulating specific
healthy readings raises the healthy verdict while ambiguous readings do not.

### 3.3 Worked rows

Three examples of the reasoning, verbatim from the table:

```python
# A 4-packet echo to the destination succeeded.
"ICMP_REACHABILITY:destination": {
    "REACHABLE": {
        H.LINK_FAILURE: 0.01,      # a down link cannot answer
        H.ROUTING_FAILURE: 0.03,   # a withdrawn route cannot answer
        H.PACKET_LOSS: 0.35,       # loss is possible but 4/4 succeeded
        H.HIGH_LATENCY: 0.45,      # latency does not stop a reply
        H.NO_FAULT_DETECTED: 1.8,  # healthy is well supported
        # DNS / MTU / port / service stay neutral: this probe says nothing about them
    },
    ...
}

# The traceroute trail stops after the last responding hop.
"TRACEROUTE": {
    "PARTIAL": {
        H.LINK_FAILURE: 3.4,       # a dead link looks like this
        H.ROUTING_FAILURE: 3.2,    # so does a withdrawn route
        # Deliberately close: end-to-end probes cannot separate them. The ICMP
        # error semantics (network-unreachable vs silent timeout) are what do.
        ...
    },
}

# The packet ladder shows a smaller size failing while a larger one passed.
"MTU_PROBE": {
    "INCONCLUSIVE_NON_MONOTONE": {
        H.PACKET_LOSS: 6.0,        # an MTU boundary is monotone; this is not one
        H.MTU_BLACK_HOLE: 0.12,
        ...
    },
}
```

### 3.4 What the model is not

The weights are documented **protocol-reasoning judgements**. They were not fitted
to the evaluation scenarios, and no calibration split was used because no fitting
took place. If calibration were added later, the calibration and evaluation
scenarios would have to be disjoint, and that split would be documented here.

## 4. Bayesian belief updating

`app/diagnosis/bayes.py` implements

```
posterior(h) ∝ likelihood(observation | h) × prior(h)
```

with three engineering safeguards:

1. **A likelihood floor** (`LIKELIHOOD_FLOOR = 1e-4`). Every likelihood is lifted to
   the floor before use, so one unexpected observation can never annihilate a
   hypothesis and destroy the ranking. A test asserts the floor is applied and
   recorded.
2. **Normalisation to exactly 1.0** after every update (asserted in tests, including
   over a nine-observation sequence).
3. **A full audit record** (`BeliefUpdate`) storing the prior, the observation, the
   raw likelihood, the floored likelihood and the posterior for **every** hypothesis,
   plus entropy before and after. The explanation layer never has to reconstruct why
   a belief moved.

Optional **evidence softening**: for outcomes whose result is itself stochastic, the
runner passes `evidence_weight < 1` and the likelihood is interpolated toward 1.0 in
log space (`value ** weight`), which keeps the update monotone in the weight. Current
values: `PARTIAL_LOSS` 0.6, `INCONCLUSIVE_LOSS` 0.75, `*_SLOW` 0.9, everything else
1.0. Rationale: a single sample of a random quantity is weaker evidence than a
deterministic protocol outcome, and the probes already send multiple packets per
probe.

## 5. Probe selection by expected information gain

`app/diagnosis/information_gain.py` is pure and side-effect free.

Entropy of the belief, in bits:

```
H(B) = -Σ_h p(h) log2 p(h)
```

Predictive outcome distribution for candidate probe `q`:

```
P(o | q, B) = Σ_h P(o | h, q) · p(h)
```

Expected posterior entropy and information gain:

```
H(B | o, q) = entropy( posterior after observing o )
EIG(q)     = H(B) - Σ_o P(o | q, B) · H(B | o, q)
```

Selection score and ordering:

```
score(q) = EIG(q) / cost(q)                        # cost from app/lab/outcomes.PROBE_COSTS
rank by (-score, -EIG, probe_key)                  # fully deterministic
```

Costs are **relative engineering weights** — cheap 1.0 (ICMP, DNS, TCP), medium 2.0
(traceroute, service health), expensive 3.0 (MTU ladder). They are not measured
wall-clock times, and the docs and UI say so.

### 5.1 Why one-shot candidates

Every candidate is single-shot, and the planner removes executed probes. The reason is evidence integrity: re-running an identical request
would hand the Bayes update the **same** observation twice, inflating confidence
without adding information — the opposite of honest evidence accounting. Probes that
already aggregate repeated samples internally (ICMP sends 4 echoes, the MTU ladder
sends 3 attempts per size) are how stochastic quantities get measured here, so
nothing is lost.

### 5.2 Deterministic tie-breaking

The sort key ends in `probe_key`, so an exact tie always resolves the same way. This
is what makes a whole diagnosis reproducible, and
`test_ranking_is_sorted_and_deterministic` checks that reversing the input order does
not change the ranking.

### 5.3 The choice must matter

The EIG calculation is not computed for display only. Two tests pin this down:

- `test_selection_actually_depends_on_the_belief` runs five different evidence sets
  through the planner and asserts they do not all choose the same next probe, with
  spot-checks (`forwarding_block → ICMP_REACHABILITY:gateway`,
  `port_blocked → SERVICE_HEALTH`).
- `test_adaptive_order_can_differ_from_the_baseline_order` steps the planner with
  real observations and asserts it deviates from the fixed order at least once.

### 5.4 Worked example (computed from the real code)

Uniform prior over 10 hypotheses, so `H(B) = log2 10 = 3.3219` bits. Consider
`ICMP_REACHABILITY:destination`. Its possible outcomes and their likelihoods are in
the table; the outcome distribution is computed with `outcome_distribution`, and each
posterior entropy with `posterior_given`:

```python
from app.diagnosis.bayes import BeliefState
from app.diagnosis.hypotheses import Hypothesis
from app.diagnosis.information_gain import expected_information_gain
from app.diagnosis.likelihoods import likelihood_table

beliefs = {code: 1 / 10 for code in Hypothesis}
gain = expected_information_gain(
    "ICMP_REACHABILITY:destination", beliefs, likelihood_table()
)
```

which yields, on the implementation in this repository:

| Quantity | Value |
|---|---|
| `H(B)` before | 3.3219 bits |
| expected `H(B \| o)` after | 2.5927 bits |
| **EIG** | **0.7293 bits** |
| cost | 1.0 (cheap) |
| score | 0.7293 |

And here is the arithmetic behind one row of that expectation, by hand, for
`REACHABLE` (this is the calculation `expected_information_gain` performs for every
outcome):

- Priors: all 10 hypotheses at 0.1.
- Likelihoods for `REACHABLE` (destination ICMP): `LINK_FAILURE` 0.01,
  `ROUTING_FAILURE` 0.03, `PACKET_LOSS` 0.35, `HIGH_LATENCY` 0.45,
  `NO_FAULT_DETECTED` 1.8, and the neutral default 0.9 for the remaining six.
- Unnormalised weights `p(h)·L`:
  `0.1·0.01 = 0.001`, `0.1·0.03 = 0.003`, `0.1·0.35 = 0.035`, `0.1·0.45 = 0.045`,
  `0.1·1.8 = 0.18`, six at `0.1·0.9 = 0.09` → total `= 0.804`.
- Normalised posterior: `LINK_FAILURE` 0.0012, `ROUTING_FAILURE` 0.0037,
  `PACKET_LOSS` 0.0435, `HIGH_LATENCY` 0.0560, `NO_FAULT_DETECTED` 0.2239, and each
  of the six neutral hypotheses 0.1119.
- Entropy of that posterior: **2.8929 bits** — i.e. if we observe `REACHABLE`, the
  engine's uncertainty drops from 3.3219 to about 2.89 bits.

The same computation for the other five outcomes (`REACHABLE_SLOW` 2.3885,
`PARTIAL_LOSS` 2.5372, `TIMEOUT` 2.7577, `UNREACHABLE_NETWORK` 2.5084,
`UNREACHABLE_HOST` 2.5578 bits) and the predictive outcome probabilities gives the
expected 2.5927 bits and therefore EIG 0.7293 bits.

**Reading the result:** this probe is highly informative but *not decisive* about the
whole catalogue — it mostly separates "the path works" from "the path is broken",
leaving DNS, MTU, port and service questions untouched. That is why the engine keeps
probing, and it is the behaviour the neutral default exists to produce.

### 5.5 On a fresh belief, the most informative probe is a TCP connect

With a uniform prior the ranking starts:

| Rank | Probe | EIG (bits) | Cost | Score |
|---:|---|---:|---:|---:|
| 1 | `TCP_CONNECT` | 0.8282 | 1.0 | 0.8282 |
| 2 | `DNS_LOOKUP` | 0.7650 | 1.0 | 0.7650 |
| 3 | `ICMP_REACHABILITY:destination` | 0.7293 | 1.0 | 0.7293 |
| 4 | `SERVICE_HEALTH` | 0.7136 | **2.0** | 0.3568 |
| 5 | `TRACEROUTE` | 0.5556 | **2.0** | 0.2778 |
| 6 | `MTU_PROBE` | 0.6962 | **3.0** | 0.2321 |

`SERVICE_HEALTH` has a higher raw EIG than `TRACEROUTE` but ranks below it because of
its cost — an example of the score doing real work. `TCP_CONNECT` wins outright
because its outcome distribution is nearly uniform (a handshake can succeed, refuse,
time out or fail to leave the host), so its result is maximally unpredictable and
therefore maximally informative.

## 6. Stopping rule

`evaluate_stopping_rule` in `app/diagnosis/planner.py`. Order matters:

**Step 0 — breadth guard.** A `NO_FAULT_DETECTED` verdict is a claim about *every*
layer, so it may only be asserted once at least `MIN_PROBE_TYPES_FOR_NO_FAULT` (4)
distinct probe classes have been executed *and* the confidence test passes.
Otherwise the run continues and the reason explains why. Without this guard the
engine happily reported "no fault detected" at 83% confidence after three healthy
probes — while the MTU ladder and the service health check had never run, and the
injected fault was often in exactly one of those layers. This is the
**"absence of evidence requires breadth of evidence"** rule, and adding it took the
adaptive top-1 accuracy from 87% to 99.6% and removed every inconclusive control run.

**Step 1 — confidence.** Stop as `confident` when

```
p(leader) ≥ CONFIDENCE_THRESHOLD (0.80)   AND
p(leader) − p(runner-up) ≥ CONFIDENCE_LEAD (0.20)
```

Both conditions are required. Leading clearly is not the same as leading *enough*:
`test_a_large_but_insufficient_margin_is_not_confident` asserts that a 79% leader is
reported as `inconclusive` rather than rounded up to a confident answer.

**Step 2 — budget.** If `probes_used ≥ max_probes`, stop with
`budget_exhausted`. The report must then present the result as unresolved, and the
message names the actual posterior and margin.

**Step 3 — no useful probe left.** If no candidate remains, or the best remaining
candidate's `EIG < MIN_INFORMATION_GAIN_BITS` (0.02 bits), stop with
`inconclusive`. The reason quotes the actual EIG, so a reader can see that running
the probe would not have changed the ranking.

Terminal statuses: `confident`, `inconclusive`, `budget_exhausted`, `error`. The UI
renders `running` as non-terminal and refuses to present a conclusion until the
backend reports a terminal state.

## 7. Component localization

A cause class is not a location. `app/diagnosis/localizer.py` derives a suspected
component from the structured details the probes already record, with a
**location confidence that is separate from the cause confidence**:

| Evidence | Reported location | Location confidence |
|---|---|---|
| Traceroute `PARTIAL` with a suspect link | that link | `strong` |
| Forwarding-layer suspect link without a trace | that link | `moderate` |
| MTU ladder `limiting_link` | that link | `strong` |
| DNS failure with a resolver in the details | resolver node | `strong` |
| Port/service fault with a port policy | `node:port` | `strong` |
| Packet loss, end-to-end only | *no single link* | `weak` |

The last row is deliberate: end-to-end probes **cannot** attribute link-level loss to
one link, so the engine says so instead of guessing. A test asserts the weak
confidence appears rather than a fabricated link id.

## 8. Explanation generation

`app/diagnosis/explanations.py` produces the report from the recorded run only. It
never calls a language model.

- `Contribution` pairs each observation with its recorded belief update and computes
  the likelihood ratio `posterior/prior` per hypothesis. An observation with ratio
  > 1.25 is listed as *supporting*, < 0.8 as *weakening*.
- `reasoning` states, per probe, the information gained in bits and which hypotheses
  moved. It names the single most informative test.
- `supporting_evidence`, `weakening_evidence` and `unexplained` are built from those
  ratios, with the "not explained" list explicitly carrying forward a
  forwarding-layer block that the leading hypothesis does not cover.
- Caveats always include the `SIMULATED LAB` label, the probabilistic nature of the
  inference, the model/prior revisions and the ground-truth isolation statement.

`validate_explanation` enforces two guarantees and is called on **every** generated
explanation:

1. every probe label cited in the supporting evidence must correspond to an
   observation that exists in the run (otherwise `AssertionError`);
2. the text must not contain proof language (`proves`, `proven`, `proof that`,
   `definitely`, `certainly`, `guaranteed`, `100%`).

A test fabricates an explanation citing a probe that never ran and asserts the
validator rejects it. This is the mechanism behind the claim that the engine cannot
report evidence it did not observe.

## 9. Adaptive versus baseline

`app/diagnosis/baseline.py` defines the fixed order:

```
1. ICMP-style reachability to the destination
2. ICMP-style reachability to the default gateway
3. ICMP-style reachability to the resolver
4. DNS lookup
5. TTL-limited path trace (traceroute)
6. TCP connect to the destination port
7. MTU / packet-size ladder
8. Application service health check
9. ICMP-style reachability to an independent control target
```

`DiagnosisRun` is the **single** sequencing implementation; the two strategies differ
only in the function used to choose the next probe
(`select_next_probe` vs `select_baseline_probe`). Everything else — probe registry,
likelihood model, priors, stopping rule, budget — is shared by construction, which is
what makes the comparison in `experiment-methodology.md` a comparison of *probe
selection* rather than of two different systems.

The baseline is deliberately order-driven: `select_baseline_probe` still records the
EIG of the probe it is about to run (so the report can show what the fixed order
*gave up*), but the choice never depends on it.

### Why the baseline fails where it does

The measured results show the baseline scoring 0% on MTU black holes and 25% on link
failures. The mechanism is visible in the probe order: the baseline runs ICMP, the
gateway ping, the resolver ping, DNS, traceroute and TCP **before** the MTU ladder.
On a clean path those first six probes all return healthy, the belief accumulates
towards `NO_FAULT_DETECTED`, and the stopping rule's confidence test fires before the
ladder is ever reached. The adaptive planner inverts the order by exactly the amount
needed: it measures the ladder early because the ladder's outcome distribution is the
most *unpredictable* one it has not yet observed.

That is a genuine, reproducible advantage of information-gain-guided selection in this
model — and it is also a *bounded* one. See the threats to validity in
`experiment-methodology.md`.

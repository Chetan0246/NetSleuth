# NetSleuth

**Evidence-guided, multi-layer network fault localization using active probing.**

NetSleuth is an interactive diagnostic workbench for a *deterministic virtual
network lab*. It investigates connectivity failures, ranks the likely root causes
from diagnostic evidence, chooses the next useful probe by **expected information
gain**, and explains its conclusion — including when the evidence is inconclusive.

The core research question is a comparison, not a claim: **does selecting probes by
expected information gain reach the same or better conclusion as always running the
same fixed sequence of tests, and does it get there with fewer probes?**

---

## What this project actually does

Four connected parts:

| Part | What it is |
|---|---|
| **Virtual network lab** | Two topology templates (campus, multi-hop WAN) modelled as typed graphs: hosts, routers, a DNS resolver, application servers, links with latency/loss/MTU. |
| **Fault injection engine** | Ten reversible, structured fault scenarios, each with a documented target and parameters. |
| **Diagnostic engine** | Nine fault hypotheses, an explicit likelihood table, a Bayesian belief updater, an information-gain probe planner, a stopping rule, a component localizer and a rule-based explanation generator. |
| **Dashboard, report and evaluation** | Five-page React UI, Markdown/JSON report export, and an experiment suite that compares adaptive probing with a fixed-order baseline over hundreds of real runs. |

### The honest boundaries

- Every observation in the core system is produced by the **virtual lab** and is
  labelled `SIMULATED LAB` in the API, the UI and every export. This build performs
  **no live network probing**; `health.live_probe_enabled` is `false` and there is no
  code path that runs `ping`, `traceroute`, `dig`, a socket connect or a packet
  capture.
- The simulator is a **model of selected protocol behaviours**, not a TCP/IP stack.
  It models hop-by-hop lowest-hop-count forwarding, additive per-link latency,
  seeded per-link packet loss, path-MTU constraints, ICMP error semantics, TCP
  handshake outcomes, DNS resolution outcomes and service health. It does not model
  bandwidth contention, TCP congestion control, fragmentation reassembly, dynamic
  routing, or IPv6. See [docs/networking-concepts.md](docs/networking-concepts.md)
  and [docs/limitations.md](docs/limitations.md).
- Diagnosis confidence is a **relative ranking of modelled explanations**, computed
  from recorded observations. It is not a measurement of a physical network and it
  is never presented as proof.
- Ground-truth fault labels are read **only by the experiment evaluator, after a
  run**. The diagnostic engine never receives them — this is enforced by a test
  (`test_ground_truth_is_absent_from_the_engine_input`).

---

## Quick start

### Backend

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- API base: `http://127.0.0.1:8000/api/v1`
- Health: `http://127.0.0.1:8000/api/v1/health`
- Interactive API docs: `http://127.0.0.1:8000/docs`

The SQLite database is created automatically under `backend/data/netsleuth.sqlite3`.
Set `NETSLEUTH_DB=/path/to/file.sqlite3` to put it somewhere else.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. The dev server proxies `/api` to
`http://127.0.0.1:8000` (edit `API_TARGET` in `frontend/vite.config.ts` if your
backend runs elsewhere).

### Both at once

```bash
./scripts/run_dev.sh
```

It starts the backend, waits for `/api/v1/health` to report healthy, then starts the
frontend. `Ctrl-C` stops both.

---

## Tests

```bash
# backend: unit + integration + API
cd backend && python3 -m pytest -q

# frontend: type check + component tests + production build
cd frontend && npm run typecheck && npm run test && npm run build

# end-to-end smoke test against a real uvicorn server
python3 scripts/smoke_test.py
```

The smoke test boots its own server, walks the documented happy path over HTTP and
exits non-zero on the first failure. It also has dedicated paths for a route
failure, a TCP/service failure, a healthy control and lab reset.

---

## Reproducing the evaluation

```bash
python3 scripts/run_evaluation.py --runs-per-scenario 10
```

This executes the ground-truth scenario suite through the real engine and writes
`docs/experiments/results-*.json`, `runs-*.csv` and `report-*.md` (plus `latest.*`
copies). Every reported number is computed from the stored run records of that
invocation; re-running with the same seed and run count reproduces them.

Actual results from `--runs-per-scenario 10` (440 runs, seed 20261009) are in
[docs/experiment-methodology.md](docs/experiment-methodology.md) and
[docs/experiments/latest.md](docs/experiments/latest.md).

---

## Demonstration

A 3–5 minute script covering a DNS failure, a route/black-hole failure and the
measured evaluation is in [docs/demo-script.md](docs/demo-script.md).

---

## Repository layout

```text
backend/
  app/
    api/            FastAPI routers (health, lab, diagnosis, experiments) + deps
    core/           configuration constants, structured errors
    diagnosis/      hypotheses, likelihoods, bayes, information_gain, planner,
                    baseline, runner, localizer, explanations
    experiments/    scenarios (ground truth), runner, metrics, exports
    lab/            graph, templates, routing, faults, outcomes, simulator
    models/         API request/response schemas
    probes/         the probe contract and the six simulated probes
    storage/        SQLite schema/access and the session/diagnosis services
  tests/
    unit/ integration/ api/
  scripts/dev_sweep.py     development-only accuracy sweep (not a reported result)
frontend/
  src/
    components/     TopologyView (SVG), shared UI primitives
    lib/            typed API client, async-state hook
    pages/          Overview, Lab, Workbench, Report, Experiments
    test/           component tests with a mocked fetch boundary
docs/               architecture, algorithm, methodology, tests, demo, limits
scripts/            run_dev.sh, smoke_test.py, run_evaluation.py
plan.md             the specification this implementation follows
```

---

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Layer responsibilities, module map, data flow, persistence schema |
| [docs/networking-concepts.md](docs/networking-concepts.md) | Exactly which protocol behaviours are modelled and which are not |
| [docs/diagnostic-algorithm.md](docs/diagnostic-algorithm.md) | The Bayes updater, likelihood table, EIG derivation with a worked example, stopping rule |
| [docs/experiment-methodology.md](docs/experiment-methodology.md) | Scenario suite, baseline definition, metrics, **actual results**, threats to validity |
| [docs/test-plan.md](docs/test-plan.md) | What is tested at each layer and why |
| [docs/demo-script.md](docs/demo-script.md) | The 3–5 minute walkthrough and likely viva questions |
| [docs/limitations.md](docs/limitations.md) | Honest limits of the model, the evaluation and the implementation |
| [docs/references.md](docs/references.md) | Standards and tool documentation cited |
| [docs/acceptance-criteria.md](docs/acceptance-criteria.md) | Line-by-line response to the plan's definition of done, with evidence and explicit gaps |

---

## Attribution and honesty statement

The method implemented here is an **explainable probabilistic diagnostic engine**:
transparent likelihoods, Bayesian updating and expected-information-gain probe
selection. It is not a machine-learning system, and the phrase "AI-powered" is not
used anywhere in this repository because no trained model is involved.

The specific contribution claimed is the *combination* of an explicit likelihood
catalogue, an EIG-driven single-shot probe planner with documented tie-breaking, a
breadth guard on the "no fault" verdict, and a fair fixed-order baseline evaluated
on stored run records — applied to a reproducible simulated multi-layer network.

If a result in this repository is not supported by a stored record, it is a bug.
Please report it as one.

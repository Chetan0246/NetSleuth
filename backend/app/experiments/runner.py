"""Experiment runner: execute the ground-truth suite and store every run.

Design rules that keep the evaluation honest:

* **Ground truth is used only here, after a run.** The engine receives a lab state
  built from the scenario's fault, but never the ``fault_type`` or ``scenario_id``.
* **Every scored number comes from a stored run record.** :mod:`app.experiments.metrics`
  only ever consumes the run records written by this module, so a metric can always
  be recomputed from the database (``tests/integration/test_experiment_metrics.py``
  does exactly that).
* **One seed per run, and the seed is persisted.** ``seed = base_seed + run_index``
  gives each run an independent-but-reproducible packet-loss stream; re-running the
  same configuration reproduces the same records.
* **Both strategies see the same lab and the same seed.** For a given scenario and
  run index, the fault parameters and the seed are identical for the adaptive and
  the baseline strategy, so the comparison isolates probe *selection*.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..core.config import DEFAULT_MAX_PROBES, MODEL_VERSION, PRIOR_CONFIG_VERSION
from ..diagnosis.runner import run_diagnosis
from ..lab.faults import LabState, normalize_fault
from ..lab.templates import get_template
from ..storage.database import Database
from .metrics import compute_metrics
from .scenarios import ACCEPTED_CONFUSIONS, ExperimentScenario, scenarios_for


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExperimentConfig:
    runs_per_scenario: int = 10
    seed: int = 20261009
    template_ids: list[str] | None = None
    fault_types: list[Any] | None = None
    strategies: list[str] = field(default_factory=lambda: ["adaptive", "baseline"])
    max_probes: int = DEFAULT_MAX_PROBES
    include_control: bool = True

    def to_public(self) -> dict[str, Any]:
        return {
            "runs_per_scenario": self.runs_per_scenario,
            "seed": self.seed,
            "template_ids": self.template_ids,
            "fault_types": [
                item.value if hasattr(item, "value") else str(item)
                for item in (self.fault_types or [])
            ],
            "strategies": list(self.strategies),
            "max_probes": self.max_probes,
            "include_control": self.include_control,
            "model_version": MODEL_VERSION,
            "prior_config_version": PRIOR_CONFIG_VERSION,
        }


def plan_experiment(config: ExperimentConfig) -> dict[str, Any]:
    """How many runs a configuration would execute (validated before starting)."""
    scenarios = scenarios_for(
        template_ids=config.template_ids,
        fault_types=config.fault_types,
        include_control=config.include_control,
    )
    return {
        "scenarios": len(scenarios),
        "strategies": len(config.strategies),
        "runs_per_scenario": config.runs_per_scenario,
        "total_runs": len(scenarios) * len(config.strategies) * config.runs_per_scenario,
    }


def run_experiment(
    database: Database,
    config: ExperimentConfig,
    *,
    name: str = "Fault suite",
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute the suite and persist every run. Returns the experiment summary."""
    scenarios = scenarios_for(
        template_ids=config.template_ids,
        fault_types=config.fault_types,
        include_control=config.include_control,
    )
    if not scenarios:
        raise ValueError("no scenario matches the requested filter")
    if not config.strategies:
        raise ValueError("at least one strategy is required")

    experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
    plan = plan_experiment(config)
    database.save_experiment(
        {
            "id": experiment_id,
            "name": name,
            "config": config.to_public(),
            "status": "running",
            "total_runs": plan["total_runs"],
            "completed_runs": 0,
            "created_at": _now(),
        }
    )

    completed = 0
    started = time.perf_counter()
    for run_index in range(config.runs_per_scenario):
        seed = config.seed + run_index
        for scenario in scenarios:
            # Build both strategies from the *same* seed and the same fault, so the
            # comparison is fair and the loss/RNG stream is identical.
            for strategy in config.strategies:
                record = _execute_one(
                    database,
                    experiment_id=experiment_id,
                    scenario=scenario,
                    strategy=strategy,
                    seed=seed,
                    run_index=run_index,
                    max_probes=config.max_probes,
                )
                completed += 1
                if progress is not None:
                    progress(
                        {
                            "completed_runs": completed,
                            "total_runs": plan["total_runs"],
                            "scenario_id": scenario.id,
                            "strategy": strategy,
                            "seed": seed,
                        }
                    )
    runs = database.list_experiment_runs(experiment_id)
    metrics = compute_metrics(runs, config=config.to_public())
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    summary = {
        "experiment_id": experiment_id,
        "name": name,
        "config": config.to_public(),
        "plan": plan,
        "completed_runs": completed,
        "wall_clock_ms": round(elapsed_ms, 3),
        "generated_at": _now(),
        "metrics": metrics,
    }
    database.save_experiment(
        {
            "id": experiment_id,
            "name": name,
            "config": config.to_public(),
            "status": "completed",
            "total_runs": plan["total_runs"],
            "completed_runs": completed,
            "metrics": metrics,
            "summary": summary,
            "created_at": summary["generated_at"],
            "completed_at": _now(),
        }
    )
    return summary


def _execute_one(
    database: Database,
    *,
    experiment_id: str,
    scenario: ExperimentScenario,
    strategy: str,
    seed: int,
    run_index: int,
    max_probes: int,
) -> dict[str, Any]:
    topology = get_template(scenario.template_id)
    lab = LabState(topology, random_seed=seed)
    spec = scenario.fault_spec()
    if spec is not None:
        lab.add_fault(normalize_fault(spec, topology, "gt-fault"))

    started = time.perf_counter()
    run = run_diagnosis(
        diagnosis_id=f"run-{scenario.id}-{strategy}-{run_index}-{seed}",
        session_id=f"experiment:{experiment_id}",
        lab=lab,
        topology=topology,
        source_node_id=scenario.source_node_id,
        destination_node_id=scenario.destination_node_id,
        destination_service=scenario.destination_service,
        strategy=strategy,
        max_probes=max_probes,
        random_seed=seed,
        to_completion=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    beliefs = run.beliefs()
    ranked = beliefs["ranked"]
    top = ranked[0]
    top3 = [item["code"] for item in ranked[:3]]
    expected = scenario.expected_hypothesis
    localization = run.suspected_component()
    localized_id = localization.component_id
    # Exact component identity only. An earlier revision also accepted a substring
    # match in either direction, which silently counted partial overlaps as correct
    # localizations and inflated the reported accuracy: e.g. expecting `web-1:80`
    # would have accepted a bare `web-1`, and expecting `l-edge-core` would have
    # accepted `edge`. The localizer emits a canonical id (link id, node id, or
    # `node:port`), and the scenario declares the same form, so equality is the
    # correct test.
    # A scenario that declares an expected component is *always* evaluated: when the
    # localizer returns no component at all that is a localization miss, not an
    # ineligible run. Requiring ``localization.component_id`` here silently dropped
    # exactly the failures from the denominator and inflated the reported accuracy.
    is_localization_correct: bool | None = None
    if scenario.expected_component_id:
        is_localization_correct = localized_id == scenario.expected_component_id

    record = {
        "scenario_id": scenario.id,
        "seed": seed,
        "template_id": scenario.template_id,
        "source_node_id": scenario.source_node_id,
        "destination_node_id": scenario.destination_node_id,
        "destination_service": scenario.destination_service,
        "strategy": strategy,
        "actual_fault_type": scenario.fault_type.value if scenario.fault_type else None,
        "actual_target_id": scenario.target_id or None,
        "predicted_fault_type": top["code"],
        "predicted_probability": top["probability"],
        "predicted_target_id": localized_id,
        "localized_target_id": localized_id,
        "localization_confidence": localization.confidence,
        "is_correct_top1": top["code"] == expected,
        "is_correct_top3": expected in top3,
        "is_localization_correct": is_localization_correct,
        "is_inconclusive": run.status != "confident",
        "status": run.status,
        "probes_used": run.probes_used,
        "elapsed_ms": elapsed_ms,
        "ranked": ranked,
        "created_at": _now(),
    }
    database.save_experiment_run(experiment_id, record)
    return record


def accepted_confusions_for(hypothesis: str) -> frozenset[str]:
    return ACCEPTED_CONFUSIONS.get(hypothesis, frozenset())


def iter_run_records(database: Database, experiment_id: str) -> Iterable[dict[str, Any]]:
    return database.list_experiment_runs(experiment_id)


__all__ = [
    "ExperimentConfig",
    "accepted_confusions_for",
    "iter_run_records",
    "plan_experiment",
    "run_experiment",
]

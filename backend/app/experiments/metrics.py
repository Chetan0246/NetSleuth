"""Experiment metrics computed from stored run records (plan.md section 10).

Every function here takes the *stored* run records as its only input. There is no
hardcoded value anywhere in this module, and the metric definitions are stated
next to the computation so the report and the UI cannot drift from each other.

Definitions
-----------
top-1 accuracy
    Fraction of runs whose **leading hypothesis** equals the scenario's expected
    hypothesis for the injected fault.
top-3 accuracy
    Fraction of runs whose expected hypothesis appears among the three
    highest-ranked hypotheses.
accepted-confusion-adjusted accuracy
    Fraction of runs that are either a top-1 hit or one of the documented,
    protocol-justified confusions (see ``app.experiments.scenarios.ACCEPTED_CONFUSIONS``).
    Reported *alongside* top-1, never instead of it.
mean probes to decision
    Mean number of executed probes per run. Reported separately for conclusive and
    all runs, because a strategy that stops early on a wrong answer is not
    "efficient".
mean measured elapsed time
    Mean of the measured wall-clock duration of the diagnosis *computation*. This is
    simulator time, not network latency: the modelled RTTs and timeouts are reported
    per-observation inside each run, and the two are never mixed.
diagnosis coverage
    Fraction of runs that reached a non-inconclusive decision.
inconclusive rate
    Fraction of runs that did not satisfy the stopping rule.
localization accuracy
    For runs that have an expected component, the fraction whose suspected
    component matched it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .scenarios import ACCEPTED_CONFUSIONS


def compute_metrics(
    runs: list[dict[str, Any]], *, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Aggregate stored run records into the reported metric set."""
    return {
        "config": config or {},
        "run_count": len(runs),
        "overall": _aggregate(runs),
        "by_strategy": {
            strategy: _aggregate([run for run in runs if run["strategy"] == strategy])
            for strategy in sorted({run["strategy"] for run in runs})
        },
        "by_fault_class": _by_fault_class(runs),
        "confusion_matrix": confusion_matrix(runs),
        "per_scenario": _per_scenario(runs),
        "notes": [
            "All metrics are computed from stored experiment run records; no value is "
            "hardcoded.",
            "Elapsed time is the measured wall-clock duration of the diagnosis "
            "computation in the simulator (SIMULATED LAB). It is not network latency; "
            "modelled RTTs are recorded per observation inside each run.",
            "A run is 'inconclusive' when the stopping rule was not satisfied "
            "(ambiguous belief, no informative probe left, or probe budget reached).",
            "'accepted_confusion_adjusted' counts top-1 hits plus the documented, "
            "protocol-justified confusions; it never replaces the strict top-1 rate.",
        ],
    }


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(runs)
    if total == 0:
        return {
            "runs": 0,
            "top1_accuracy": None,
            "top3_accuracy": None,
            "accepted_confusion_adjusted_accuracy": None,
            "top1_correct": 0,
            "top3_correct": 0,
            "accepted_confusion_correct": 0,
            "coverage": None,
            "inconclusive_rate": None,
            "inconclusive_runs": 0,
            "mean_probes_all": None,
            "mean_probes_conclusive": None,
            "mean_elapsed_ms": None,
            "localization_accuracy": None,
            "localization_evaluated": 0,
        }
    top1 = sum(1 for run in runs if run.get("is_correct_top1"))
    top3 = sum(1 for run in runs if run.get("is_correct_top3"))
    adjusted = sum(1 for run in runs if _is_accepted(run))
    inconclusive = sum(1 for run in runs if run.get("is_inconclusive"))
    conclusive = [run for run in runs if not run.get("is_inconclusive")]
    localization_runs = [
        run for run in runs if run.get("is_localization_correct") is not None
    ]
    localization_hits = sum(
        1 for run in localization_runs if run.get("is_localization_correct")
    )
    return {
        "runs": total,
        "top1_correct": top1,
        "top3_correct": top3,
        "accepted_confusion_correct": adjusted,
        "inconclusive_runs": inconclusive,
        "top1_accuracy": round(top1 / total, 4),
        "top3_accuracy": round(top3 / total, 4),
        "accepted_confusion_adjusted_accuracy": round(adjusted / total, 4),
        "coverage": round((total - inconclusive) / total, 4),
        "inconclusive_rate": round(inconclusive / total, 4),
        "mean_probes_all": round(
            sum(run["probes_used"] for run in runs) / total, 4
        ),
        "mean_probes_conclusive": (
            round(sum(run["probes_used"] for run in conclusive) / len(conclusive), 4)
            if conclusive
            else None
        ),
        "mean_elapsed_ms": round(sum(run["elapsed_ms"] for run in runs) / total, 4),
        "localization_accuracy": (
            round(localization_hits / len(localization_runs), 4)
            if localization_runs
            else None
        ),
        "localization_evaluated": len(localization_runs),
    }


def _is_accepted(run: dict[str, Any]) -> bool:
    if run.get("is_correct_top1"):
        return True
    actual = run.get("actual_fault_type")
    if not actual:
        return False
    expected = _expected_hypothesis(run)
    allowed = ACCEPTED_CONFUSIONS.get(expected, frozenset())
    return run.get("predicted_fault_type") in allowed


def _expected_hypothesis(run: dict[str, Any]) -> str:
    """Map the stored ground-truth fault type back to its expected hypothesis."""
    from .scenarios import EXPECTED_HYPOTHESIS
    from ..lab.faults import FaultType

    fault_type = run.get("actual_fault_type")
    if not fault_type:
        return "NO_FAULT_DETECTED"
    try:
        return EXPECTED_HYPOTHESIS[FaultType(fault_type)]
    except (KeyError, ValueError):  # pragma: no cover - defensive
        return "UNKNOWN"


def _by_fault_class(runs: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[_expected_hypothesis(run)].append(run)
    out: dict[str, Any] = {}
    for hypothesis, group in sorted(grouped.items()):
        by_strategy = {
            strategy: _aggregate([run for run in group if run["strategy"] == strategy])
            for strategy in sorted({run["strategy"] for run in group})
        }
        out[hypothesis] = {"all": _aggregate(group), "by_strategy": by_strategy}
    return out


def confusion_matrix(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Actual fault class (ground truth) x predicted fault class."""
    labels = sorted(
        {_expected_hypothesis(run) for run in runs}
        | {run["predicted_fault_type"] or "UNKNOWN" for run in runs}
    )
    matrix: dict[str, dict[str, int]] = {
        actual: {predicted: 0 for predicted in labels} for actual in labels
    }
    for run in runs:
        actual = _expected_hypothesis(run)
        predicted = run["predicted_fault_type"] or "UNKNOWN"
        matrix.setdefault(actual, Counter())
        matrix[actual][predicted] = matrix[actual].get(predicted, 0) + 1
    return {"labels": labels, "matrix": matrix}


def _per_scenario(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[run["scenario_id"]].append(run)
    out: list[dict[str, Any]] = []
    for scenario_id, group in sorted(grouped.items()):
        by_strategy = {
            strategy: _aggregate([run for run in group if run["strategy"] == strategy])
            for strategy in sorted({run["strategy"] for run in group})
        }
        out.append(
            {
                "scenario_id": scenario_id,
                "expected_hypothesis": _expected_hypothesis(group[0]),
                "actual_fault_type": group[0].get("actual_fault_type"),
                "template_id": group[0].get("template_id"),
                "runs": len(group),
                "seeds": sorted({run["seed"] for run in group}),
                "by_strategy": by_strategy,
            }
        )
    return out


def scenario_variation(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """How much each scenario's outcome varies across seeds.

    Reported so a reader can see whether a metric rests on a uniform behaviour or
    on a mixture, which is the difference between a stable result and a lucky one.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[(run["scenario_id"], run["strategy"])].append(run)
    out: dict[str, Any] = {}
    for (scenario_id, strategy), group in sorted(grouped.items()):
        predictions = Counter(run["predicted_fault_type"] for run in group)
        out[f"{scenario_id}|{strategy}"] = {
            "runs": len(group),
            "distinct_predictions": len(predictions),
            "prediction_counts": dict(predictions.most_common()),
            "probe_counts": dict(sorted(Counter(run["probes_used"] for run in group).items())),
        }
    return out


__all__ = ["compute_metrics", "confusion_matrix", "scenario_variation"]

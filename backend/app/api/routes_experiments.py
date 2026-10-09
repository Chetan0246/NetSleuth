"""Experiment Studio endpoints: catalogue, estimate, run, results and export."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse

from ..core.config import (
    DEFAULT_MAX_PROBES,
    DEFAULT_RUNS_PER_SCENARIO,
    MAX_EXPERIMENT_TOTAL_RUNS,
    MAX_MAX_PROBES,
    MAX_RUNS_PER_SCENARIO,
)
from ..core.errors import ValidationError
from ..experiments.exports import (
    experiment_runs_csv,
    experiment_summary_json,
    experiment_summary_markdown,
)
from ..experiments.metrics import scenario_variation
from ..experiments.runner import ExperimentConfig, plan_experiment, run_experiment
from ..experiments.scenarios import scenario_catalogue
from ..lab.outcomes import probe_key_label
from .deps import get_database
from ..models.schemas import ExperimentEstimateResponse, ExperimentRequest, ScenarioCatalogueResponse
from ..storage.database import Database

router = APIRouter(tags=["experiments"])


@router.get("/experiments/scenarios", response_model=ScenarioCatalogueResponse)
def list_scenarios() -> ScenarioCatalogueResponse:
    from ..diagnosis.baseline import BASELINE_ORDER, BASELINE_SEQUENCE_DESCRIPTION

    catalogue = scenario_catalogue()
    return ScenarioCatalogueResponse(
        scenarios=catalogue["scenarios"],
        fault_types=catalogue["fault_types"],
        strategies=["adaptive", "baseline"],
        baseline_sequence=[
            f"{probe_key_label(key)} — {description}"
            for key, description in zip(BASELINE_ORDER, BASELINE_SEQUENCE_DESCRIPTION)
        ],
        defaults={
            "runs_per_scenario": DEFAULT_RUNS_PER_SCENARIO,
            "max_runs_per_scenario": MAX_RUNS_PER_SCENARIO,
            "max_total_runs": MAX_EXPERIMENT_TOTAL_RUNS,
            "max_probes": DEFAULT_MAX_PROBES,
            "max_probes_ceiling": MAX_MAX_PROBES,
            "seed": 20261009,
        },
    )


@router.post("/experiments/estimate", response_model=ExperimentEstimateResponse)
def estimate(payload: ExperimentRequest) -> ExperimentEstimateResponse:
    config = _config(payload)
    plan = plan_experiment(config)
    return ExperimentEstimateResponse(
        scenarios=plan["scenarios"],
        strategies=plan["strategies"],
        runs_per_scenario=plan["runs_per_scenario"],
        total_runs=plan["total_runs"],
        exceeds_limit=plan["total_runs"] > MAX_EXPERIMENT_TOTAL_RUNS,
    )


@router.post("/experiments/run", status_code=201)
def run(
    payload: ExperimentRequest,
    database: Database = Depends(get_database),
) -> dict[str, object]:
    """Execute the ground-truth suite. Runs synchronously and stores every run.

    The run count is validated *before* any work starts, so an oversized request
    fails fast instead of consuming the server.
    """
    config = _config(payload)
    plan = plan_experiment(config)
    if plan["total_runs"] > MAX_EXPERIMENT_TOTAL_RUNS:
        raise ValidationError(
            f"the requested configuration would execute {plan['total_runs']} runs, which "
            f"exceeds the configured limit of {MAX_EXPERIMENT_TOTAL_RUNS}. Lower "
            "runs_per_scenario or narrow the scenario filter.",
            field="runs_per_scenario",
            requested=plan["total_runs"],
            limit=MAX_EXPERIMENT_TOTAL_RUNS,
        )
    if plan["scenarios"] == 0:
        raise ValidationError(
            "no scenario matches the requested filter", field="fault_types"
        )
    summary = run_experiment(database, config)
    return summary


@router.get("/experiments")
def list_experiments(
    limit: int = Query(default=20, ge=1, le=100),
    database: Database = Depends(get_database),
) -> dict[str, object]:
    items = []
    for row in database.list_experiments(limit=limit):
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "completed_runs": row["completed_runs"],
                "total_runs": row["total_runs"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "config": json.loads(row["config_json"]),
            }
        )
    return {"experiments": items, "total": len(items)}


@router.get("/experiments/{experiment_id}")
def get_experiment(
    experiment_id: str,
    database: Database = Depends(get_database),
) -> dict[str, object]:
    row = database.get_experiment(experiment_id)
    runs = database.list_experiment_runs(experiment_id)
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "config": json.loads(row["config_json"]),
        "total_runs": row["total_runs"],
        "completed_runs": row["completed_runs"],
        "stored_run_records": len(runs),
        "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else None,
        "variation": scenario_variation(runs),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


@router.get("/experiments/{experiment_id}/export")
def export_experiment(
    experiment_id: str,
    format: str = Query(default="json", pattern="^(json|csv|markdown)$"),
    database: Database = Depends(get_database),
) -> Response:
    row = database.get_experiment(experiment_id)
    runs = database.list_experiment_runs(experiment_id)
    summary = (
        json.loads(row["summary_json"])
        if row["summary_json"]
        else {
            "experiment_id": row["id"],
            "name": row["name"],
            "config": json.loads(row["config_json"]),
            "completed_runs": row["completed_runs"],
            "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else {},
        }
    )
    if format == "csv":
        return PlainTextResponse(
            content=experiment_runs_csv(runs, experiment_id=experiment_id),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="netsleuth-{experiment_id}-runs.csv"'
            },
        )
    if format == "markdown":
        return PlainTextResponse(
            content=experiment_summary_markdown(summary),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="netsleuth-{experiment_id}-report.md"'
                )
            },
        )
    return Response(
        content=experiment_summary_json(summary),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="netsleuth-{experiment_id}.json"'
        },
    )


def _config(payload: ExperimentRequest) -> ExperimentConfig:
    strategies = list(dict.fromkeys(payload.strategies))
    for strategy in strategies:
        if strategy not in ("adaptive", "baseline"):
            raise ValidationError(
                f"unknown strategy {strategy!r}; expected 'adaptive' or 'baseline'",
                field="strategies",
            )
    return ExperimentConfig(
        runs_per_scenario=payload.runs_per_scenario,
        seed=payload.seed,
        template_ids=payload.template_ids,
        fault_types=payload.fault_types,
        strategies=strategies,
        max_probes=payload.max_probes,
        include_control=payload.include_control,
    )

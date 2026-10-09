"""Health, overview and catalogue endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..core.config import (
    APP_NAME,
    APP_VERSION,
    MODEL_VERSION,
    PRIOR_CONFIG_VERSION,
)
from ..diagnosis.hypotheses import hypothesis_public_catalogue
from ..lab.faults import FAULT_DESCRIPTIONS, PARAMETER_REFERENCE
from ..lab.outcomes import (
    CANONICAL_CANDIDATE_ORDER,
    PROBE_COSTS,
    PROBE_LABELS,
    probe_key as make_probe_key,
    probe_key_label,
)
from ..models.schemas import HealthResponse
from ..storage.database import Database
from ..storage.repository import DiagnosisService, SessionService
from .deps import get_database, get_diagnoses, get_sessions

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(database: Database = Depends(get_database)) -> HealthResponse:
    return HealthResponse(
        app=APP_NAME,
        version=APP_VERSION,
        model_version=MODEL_VERSION,
        prior_config_version=PRIOR_CONFIG_VERSION,
        database=str(database.path),
        simulation_mode="deterministic virtual lab (SIMULATED LAB)",
        live_probe_enabled=False,
    )


@router.get("/overview")
def overview(
    database: Database = Depends(get_database),
    sessions: SessionService = Depends(get_sessions),
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> dict[str, object]:
    """Dashboard numbers. Every value is counted from stored records.

    A fresh install returns zeros and empty lists, which is what the UI's genuine
    empty state renders; nothing here is seeded with sample data.
    """
    stats = database.stats()
    recent_sessions = sessions.list_sessions(limit=5)
    # Reuse the diagnosis service's own summary builder rather than assembling the
    # entry here. The hand-built version silently omitted five fields the frontend's
    # `DiagnosisSummary` type requires and hardcoded `top_hypothesis: None` for a run
    # read back from storage, so the dashboard displayed "3/" for the probe count and
    # hid a known conclusion after a restart.
    recent_diagnoses = diagnoses.list_all(limit=8)
    latest_experiment = None
    experiments = database.list_experiments(limit=1)
    if experiments:
        row = experiments[0]
        import json as _json

        latest_experiment = {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "completed_runs": row["completed_runs"],
            "total_runs": row["total_runs"],
            "created_at": row["created_at"],
            "metrics": _json.loads(row["metrics_json"]) if row["metrics_json"] else None,
        }
    return {
        "stats": stats,
        "recent_sessions": recent_sessions,
        "recent_diagnoses": recent_diagnoses,
        "latest_experiment": latest_experiment,
        "mode": "SIMULATED LAB",
        "live_probe_enabled": False,
    }


@router.get("/catalogue")
def catalogue() -> dict[str, object]:
    """The reference data the UI renders: hypotheses, probes, faults, parameters."""
    probes = []
    for probe_type, selector in CANONICAL_CANDIDATE_ORDER:
        key = make_probe_key(probe_type, selector)
        probes.append(
            {
                "probe_key": key,
                "probe_type": probe_type.value,
                "selector": selector,
                "label": probe_key_label(key),
                "cost": PROBE_COSTS.get(probe_type, 1.0),
            }
        )
    return {
        "hypotheses": hypothesis_public_catalogue(),
        "probes": probes,
        "probe_labels": {key.value: value for key, value in PROBE_LABELS.items()},
        "fault_types": [
            {"fault_type": fault_type.value, "description": description}
            for fault_type, description in FAULT_DESCRIPTIONS.items()
        ],
        "fault_parameters": PARAMETER_REFERENCE,
        "model_version": MODEL_VERSION,
        "prior_config_version": PRIOR_CONFIG_VERSION,
    }

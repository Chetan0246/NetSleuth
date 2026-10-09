"""Diagnosis endpoints: create, step, run, baseline comparison, report export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse

from ..diagnosis.hypotheses import hypothesis_public_catalogue
from ..experiments.exports import diagnosis_report_json, diagnosis_report_markdown
from .deps import get_diagnoses
from ..models.schemas import (
    BaselineComparisonRequest,
    DiagnosisCreateRequest,
    DiagnosisListResponse,
)
from ..storage.repository import DiagnosisService

router = APIRouter(tags=["diagnosis"])


@router.get("/diagnosis/hypotheses")
def hypotheses() -> dict[str, object]:
    return {"hypotheses": hypothesis_public_catalogue()}


@router.post("/diagnoses", status_code=201)
def create_diagnosis(
    payload: DiagnosisCreateRequest,
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> dict[str, object]:
    run = diagnoses.create(
        session_id=payload.session_id,
        source_node_id=payload.source_node_id,
        destination_node_id=payload.destination_node_id,
        destination_service=payload.destination_service,
        port=payload.port,
        max_probes=payload.max_probes,
        strategy=payload.strategy,
        run_to_completion=payload.run_to_completion,
    )
    return run.to_public()


@router.get("/diagnoses", response_model=DiagnosisListResponse)
def list_diagnoses(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> DiagnosisListResponse:
    items = diagnoses.list_for_session(session_id, limit=limit) if session_id else (
        diagnoses.list_all(limit=limit)
    )
    return DiagnosisListResponse(diagnoses=items, total=len(items))


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(
    diagnosis_id: str,
    include_report: bool = Query(default=True),
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> dict[str, object]:
    run = diagnoses.get(diagnosis_id)
    return run.to_public(include_report=include_report)


@router.post("/diagnoses/{diagnosis_id}/step")
def step_diagnosis(
    diagnosis_id: str,
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> dict[str, object]:
    return diagnoses.step(diagnosis_id).to_public()


@router.post("/diagnoses/{diagnosis_id}/run")
def run_diagnosis_endpoint(
    diagnosis_id: str,
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> dict[str, object]:
    return diagnoses.run(diagnosis_id).to_public()


@router.post("/diagnoses/{diagnosis_id}/baseline")
def run_baseline(
    diagnosis_id: str,
    payload: BaselineComparisonRequest | None = None,
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> dict[str, object]:
    """Run an equivalent fixed-order baseline and return both results.

    Both runs share the session (so the same topology and the same active faults),
    the same priors, likelihood model, stopping rule and — unless overridden — the
    same probe budget. Only the probe order differs, which is what makes the
    comparison meaningful.
    """
    source = diagnoses.get(diagnosis_id)
    adaptive_payload = source.to_public()
    baseline = diagnoses.baseline_for(
        diagnosis_id, max_probes=payload.max_probes if payload else None
    )
    baseline_payload = baseline.to_public()
    return {
        "adaptive": _comparison_block(adaptive_payload),
        "baseline": _comparison_block(baseline_payload),
        "same_conditions": {
            "session_id": source.session_id,
            "source_node_id": source.source_node_id,
            "destination_node_id": source.destination_node_id,
            "destination_service": source.destination_service,
            "max_probes": baseline.max_probes,
            "model_version": adaptive_payload["model_version"],
            "prior_config_version": adaptive_payload["prior_config_version"],
            "difference": (
                "Only the probe selection order differs. Both runs use the same probe "
                "implementations, priors, likelihood model, stopping rule and probe budget."
            ),
        },
        "baseline_full": baseline_payload,
    }


def _comparison_block(payload: dict[str, object]) -> dict[str, object]:
    beliefs = payload.get("beliefs") or {}
    ranked = beliefs.get("ranked") or [{}]
    return {
        "id": payload["id"],
        "strategy": payload["strategy"],
        "status": payload["status"],
        "probes_used": payload["probes_used"],
        "max_probes": payload["max_probes"],
        "top_hypothesis": ranked[0].get("code"),
        "top_probability": ranked[0].get("probability"),
        "entropy_bits": beliefs.get("entropy_bits"),
        "probe_sequence": [step["probe_key"] for step in payload.get("steps", [])],
        "stopping_reason": payload.get("stopping_reason"),
        "suspected_component": payload.get("suspected_component"),
    }


@router.get("/diagnoses/{diagnosis_id}/comparison")
def get_comparison(
    diagnosis_id: str, diagnoses: DiagnosisService = Depends(get_diagnoses)
) -> dict[str, object]:
    return diagnoses.comparison(diagnosis_id)


@router.get("/diagnoses/{diagnosis_id}/report")
def get_report(
    diagnosis_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json|md|json_download)$"),
    diagnoses: DiagnosisService = Depends(get_diagnoses),
) -> Response:
    """Download the diagnostic report as Markdown or JSON."""
    run = diagnoses.get(diagnosis_id)
    payload = run.to_public()
    if format in ("json", "json_download"):
        return Response(
            content=diagnosis_report_json(payload),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="netsleuth-{diagnosis_id}.json"'
            },
        )
    return PlainTextResponse(
        content=diagnosis_report_markdown(payload),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="netsleuth-{diagnosis_id}.md"'},
    )

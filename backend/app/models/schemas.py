"""Pydantic request/response schemas for the HTTP API (plan.md sections 12-13).

Every response the API returns is one of these models, so the frontend never
receives an arbitrary internal Python object and the contract is testable.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..core.config import (
    DEFAULT_MAX_PROBES,
    DEFAULT_RUNS_PER_SCENARIO,
    MAX_EXPERIMENT_TOTAL_RUNS,
    MAX_MAX_PROBES,
    MAX_RUNS_PER_SCENARIO,
)
from ..lab.faults import FaultSpec, FaultType

# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"
    app: str
    version: str
    model_version: str
    prior_config_version: str
    database: str
    simulation_mode: str
    live_probe_enabled: bool


# ---------------------------------------------------------------------------
# lab
# ---------------------------------------------------------------------------


class TemplateListResponse(BaseModel):
    templates: list[dict[str, Any]]


class FaultRequest(BaseModel):
    """Activate one validated fault on a session."""

    fault: FaultSpec
    fault_id: str | None = Field(
        default=None,
        description="Stable id for a new fault; reuse an existing id to replace it.",
        max_length=64,
    )


class FaultToggleRequest(BaseModel):
    is_active: bool


class SessionCreateRequest(BaseModel):
    template_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    random_seed: int | None = Field(default=None, ge=0, le=2**31 - 1)

    @field_validator("template_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("template_id must not be blank")
        return value


class SessionResponse(BaseModel):
    id: str
    name: str
    template_id: str
    template_name: str
    mode: Literal["simulated", "live"] = "simulated"
    random_seed: int
    topology: dict[str, Any]
    active_faults: list[dict[str, Any]]
    available_faults: list[dict[str, Any]]
    parameter_reference: list[dict[str, Any]]
    forwarding_tables: dict[str, list[dict[str, Any]]]
    created_at: str
    updated_at: str


class SessionSummary(BaseModel):
    id: str
    name: str
    template_id: str
    template_name: str
    mode: Literal["simulated", "live"] = "simulated"
    active_fault_count: int
    active_fault_types: list[str]
    node_count: int
    link_count: int
    created_at: str
    updated_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int


class FaultListResponse(BaseModel):
    session_id: str
    active_faults: list[dict[str, Any]]
    available_faults: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# diagnosis
# ---------------------------------------------------------------------------


class DiagnosisCreateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    source_node_id: str = Field(min_length=1, max_length=64)
    destination_node_id: str = Field(min_length=1, max_length=64)
    destination_service: str | None = Field(default=None, max_length=64)
    port: int | None = Field(default=None, ge=1, le=65535)
    max_probes: int = Field(default=DEFAULT_MAX_PROBES, ge=1, le=MAX_MAX_PROBES)
    strategy: Literal["adaptive", "baseline"] = "adaptive"
    #: Run the whole diagnosis immediately instead of waiting for /step calls.
    run_to_completion: bool = False


class DiagnosisListResponse(BaseModel):
    diagnoses: list[dict[str, Any]]
    total: int


class BaselineComparisonRequest(BaseModel):
    max_probes: int | None = Field(default=None, ge=1, le=MAX_MAX_PROBES)


# ---------------------------------------------------------------------------
# experiments
# ---------------------------------------------------------------------------


class ExperimentRequest(BaseModel):
    runs_per_scenario: int = Field(
        default=DEFAULT_RUNS_PER_SCENARIO, ge=1, le=MAX_RUNS_PER_SCENARIO
    )
    seed: int = Field(default=20261009, ge=0, le=2**31 - 1)
    template_ids: list[str] | None = None
    fault_types: list[FaultType] | None = None
    max_probes: int = Field(default=DEFAULT_MAX_PROBES, ge=1, le=MAX_MAX_PROBES)
    strategies: list[Literal["adaptive", "baseline"]] = Field(
        default_factory=lambda: ["adaptive", "baseline"]
    )
    #: Include healthy (no-fault) control runs.
    include_control: bool = True

    @field_validator("strategies")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one strategy must be selected")
        return list(dict.fromkeys(value))

    @field_validator("runs_per_scenario")
    @classmethod
    def _bounded(cls, value: int) -> int:
        if value < 1:
            raise ValueError("runs_per_scenario must be at least 1")
        return value


class ExperimentEstimateResponse(BaseModel):
    scenarios: int
    strategies: int
    runs_per_scenario: int
    total_runs: int
    exceeds_limit: bool
    limit: int = MAX_EXPERIMENT_TOTAL_RUNS


class ScenarioCatalogueResponse(BaseModel):
    scenarios: list[dict[str, Any]]
    fault_types: list[str]
    strategies: list[str]
    baseline_sequence: list[str]
    defaults: dict[str, Any]

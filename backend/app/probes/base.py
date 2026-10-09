"""The single probe contract shared by every diagnostic (plan.md section 8 / 15.1).

A probe turns a :class:`ProbeRequest` plus the current lab state into a
:class:`ProbeObservation`. The diagnostic engine depends only on this contract,
never on simulator internals, which is what allows the adaptive runner, the
fixed-order baseline and the experiment runner to share one implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..lab.outcomes import MODE_LIVE, MODE_SIMULATED, ProbeType, probe_key_label


class ProbeRequest(BaseModel):
    """What to run. Deliberately contains *no* ground-truth fault information."""

    probe_key: str
    probe_type: ProbeType
    source_node_id: str
    destination_node_id: str
    destination_service: str | None = None
    port: int | None = None
    #: Same-segment alternative target for the last-link ambiguity test.
    control_node_id: str | None = None
    #: Same-segment node used as the ICMP gateway target of the source.
    gateway_node_id: str | None = None
    resolver_node_id: str | None = None
    hostname: str | None = None
    sequence_number: int = 1
    #: Seed/tag suffix; the same tag always reproduces identical observations.
    tag: str = "probe"

    def describe_target(self) -> str:
        return self.destination_node_id


class EvidenceStatement(BaseModel):
    """One structured reading derived from an observation."""

    statement: str
    kind: Literal["observation", "reading", "limitation"] = "reading"
    supports: list[str] = Field(default_factory=list)
    weakens: list[str] = Field(default_factory=list)
    strength: Literal["strong", "moderate", "weak"] = "moderate"
    details: dict[str, Any] = Field(default_factory=dict)


class ProbeObservation(BaseModel):
    """The uniform result of running any probe."""

    probe_key: str
    probe_type: ProbeType
    probe_label: str
    mode: Literal["SIMULATED LAB", "LIVE PROBE"] = MODE_SIMULATED
    source_node_id: str
    destination_node_id: str
    outcome: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceStatement] = Field(default_factory=list)
    #: Modelled protocol time implied by the simulation (e.g. 2 x path latency).
    elapsed_ms: float = 0.0
    #: Measured wall-clock duration of the probe call itself (always recorded).
    wall_clock_ms: float = 0.0
    sequence_number: int = 1
    selected_reason: str | None = None
    information_gain: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def source_label(self) -> str:
        return f"{probe_key_label(self.probe_key)} -> {self.destination_node_id}"

    def to_public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ProbeRunner(Protocol):
    """Structural type implemented by every probe (see plan.md 15.1)."""

    probe_type: ProbeType

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:  # pragma: no cover
        ...


LIVE_MODE = MODE_LIVE
SIMULATED_MODE = MODE_SIMULATED

"""The diagnosis runner: execute probes, update belief, decide when to stop.

One runner serves both strategies (plan.md section 15.4). The *only* difference
between them is the function used to choose the next probe:

============  ==================================================
``adaptive``  :func:`app.diagnosis.planner.select_next_probe`
``baseline``  :func:`app.diagnosis.baseline.select_baseline_probe`
============  ==================================================

Both then share the same probe registry, likelihood table, priors, stopping rule
and budget, which is what makes the experiment in section 10 a fair comparison.

The runner is the only module that knows how to sequence a diagnosis; the API
layer just calls :meth:`DiagnosisRun.step` or :meth:`DiagnosisRun.run_to_completion`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.config import (
    CONFIDENCE_LEAD,
    CONFIDENCE_THRESHOLD,
    MIN_INFORMATION_GAIN_BITS,
    MODEL_VERSION,
    PRIOR_CONFIG_VERSION,
)
from ..core.errors import ValidationError
from ..lab.faults import FaultConfig, LabState
from ..lab.graph import Topology
from ..lab.simulator import LabSimulator
from ..probes.base import ProbeObservation, ProbeRequest
from ..probes.simulated import build_probe_registry
from .baseline import BaselinePlan, build_baseline_plan, select_baseline_probe
from .bayes import BeliefState, BeliefUpdate
from .explanations import DiagnosisExplanation, build_contributions, build_explanation
from .hypotheses import DEFAULT_PRIORS, Hypothesis
from .likelihoods import likelihood_table
from .localizer import Localization, localize
from .planner import (
    DiagnosisContext,
    ProbeChoice,
    Status,
    evaluate_stopping_rule,
    select_next_probe,
)


@dataclass
class ProbeStep:
    """One executed step of a diagnosis (observation plus its consequences)."""

    sequence_number: int
    observation: ProbeObservation
    selected_reason: str
    planned_information_gain: float | None
    planned_cost: float
    belief_after: dict[str, float]
    entropy_before: float
    entropy_after: float
    update: BeliefUpdate

    def to_public(self) -> dict[str, Any]:
        return {
            "sequence_number": self.sequence_number,
            "probe_key": self.observation.probe_key,
            "probe_type": self.observation.probe_type.value,
            "probe_label": self.observation.probe_label,
            "mode": self.observation.mode,
            "outcome": self.observation.outcome,
            "summary": self.observation.summary,
            # A copy: the persistence layer adds source/destination ids to this dict, and
            # returning the observation's own dict by reference wrote back into it.
            "details": dict(self.observation.details),
            "evidence": [item.model_dump(mode="json") for item in self.observation.evidence],
            "selected_reason": self.selected_reason,
            "planned_information_gain_bits": (
                None
                if self.planned_information_gain is None
                else round(self.planned_information_gain, 6)
            ),
            "cost": self.planned_cost,
            "modelled_elapsed_ms": round(self.observation.elapsed_ms, 3),
            "measured_wall_clock_ms": round(self.observation.wall_clock_ms, 3),
            "entropy_before_bits": round(self.entropy_before, 6),
            "entropy_after_bits": round(self.entropy_after, 6),
            "belief_after": {key: round(value, 6) for key, value in self.belief_after.items()},
            "created_at": self.observation.created_at.isoformat(),
        }


@dataclass
class DiagnosisRun:
    """A mutable in-memory diagnosis. Persisted by the storage layer on each step.

    **Lab isolation.** ``__post_init__`` immediately replaces ``lab`` with a private
    deep copy of the topology, and records the fault signature it was created with.
    Without this the run would hold a reference to the session's cached `LabState`,
    so injecting a fault mid-diagnosis would silently change the evidence of a run
    that had already collected observations under the old state — its first probe
    measuring a healthy lab and its later probes measuring a faulty one, with one
    Bayesian posterior over the mixture. Copying gives each run a frozen, internally
    consistent world.

    ``frozen_fault_signature`` is checked before every step so the API can refuse to
    continue a run whose session faults have since changed, rather than folding
    inconsistent evidence into it.
    """

    diagnosis_id: str
    session_id: str
    source_node_id: str
    destination_node_id: str
    destination_service: str | None
    port: int | None
    strategy: str
    max_probes: int
    lab: LabState
    topology: Topology
    hostname: str | None = None
    gateway_node_id: str | None = None
    resolver_node_id: str | None = None
    control_node_id: str | None = None
    status: Status = "running"
    steps: list[ProbeStep] = field(default_factory=list)
    priors: dict[Hypothesis, float] = field(default_factory=lambda: dict(DEFAULT_PRIORS))
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    stopping_reason: str = "The diagnosis has not been evaluated yet."
    next_probe: dict[str, Any] | None = None
    considered_alternatives: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, str]] = field(default_factory=list)
    random_seed: int = 0

    def __post_init__(self) -> None:
        # Freeze the world: a private topology copy plus the fault set it contains.
        self.lab = LabState(
            self.lab.topology.model_copy(deep=True),
            random_seed=self.lab.random_seed,
            faults=list(self.lab.faults),
        )
        self.topology = self.lab.topology
        self.frozen_fault_signature = fault_signature(self.lab.faults)
        self.belief = BeliefState(self.priors)
        self.simulator = LabSimulator(self.lab, seed=self.random_seed)
        self.probes = build_probe_registry(self.simulator)
        self.table = likelihood_table()
        self._baseline_plan: BaselinePlan | None = None
        if not self.resolver_node_id:
            resolver = self.simulator.primary_resolver()
            self.resolver_node_id = resolver.id if resolver else None
        if self.gateway_node_id is None:
            gateway = self.topology.node(self.source_node_id).gateway
            self.gateway_node_id = gateway
        if self.control_node_id is None:
            control = self.simulator.control_destination(
                self.source_node_id, self.destination_node_id
            )
            self.control_node_id = (
                control.id if control.id not in (self.source_node_id, self.destination_node_id)
                else None
            )
        if self.port is None:
            ref = self.simulator.resolve_destination(
                self.destination_node_id, self.destination_service
            )
            self.port = ref.port or None
            if self.hostname is None:
                self.hostname = ref.hostname

    # ---- context ---------------------------------------------------------
    @property
    def strategy_label(self) -> str:
        return "adaptive (expected information gain)" if self.strategy == "adaptive" \
            else "fixed-order baseline"

    def context(self) -> DiagnosisContext:
        return DiagnosisContext(
            source_node_id=self.source_node_id,
            destination_node_id=self.destination_node_id,
            destination_service=self.destination_service,
            port=self.port,
            hostname=self.hostname,
            gateway_node_id=self.gateway_node_id,
            resolver_node_id=self.resolver_node_id,
            control_node_id=self.control_node_id,
            topology=self.topology,
        )

    def executed_probe_keys(self) -> list[str]:
        return [step.observation.probe_key for step in self.steps]

    @property
    def probes_used(self) -> int:
        return len(self.steps)

    # ---- step ------------------------------------------------------------
    def _choose_next(self) -> ProbeChoice | None:
        context = self.context()
        if self.strategy == "adaptive":
            return select_next_probe(
                self.belief, context, self.table, executed_probe_keys=self.executed_probe_keys()
            )
        if self._baseline_plan is None:
            self._baseline_plan = build_baseline_plan(context)
        return select_baseline_probe(
            self._baseline_plan, self.executed_probe_keys(), self.belief
        )

    def _evaluate(self, next_choice: ProbeChoice | None) -> None:
        decision = evaluate_stopping_rule(
            self.belief,
            probes_used=self.probes_used,
            max_probes=self.max_probes,
            next_probe=next_choice,
            confidence_threshold=CONFIDENCE_THRESHOLD,
            confidence_lead=CONFIDENCE_LEAD,
            min_information_gain=MIN_INFORMATION_GAIN_BITS,
            executed_probe_keys=self.executed_probe_keys(),
        )
        self.stopping_reason = decision.reason
        if decision.should_stop:
            self.status = decision.status
            if self.completed_at is None:
                self.completed_at = time.time()
            self.next_probe = None
        else:
            self.status = "running"
            assert next_choice is not None  # guaranteed by the stopping rule
            self.next_probe = next_choice.to_public()
            self.considered_alternatives = [
                {
                    "probe_key": item.probe_key,
                    "expected_information_gain_bits": round(item.expected_information_gain, 6),
                    "cost": item.cost,
                    "score": round(item.scored_value, 6),
                }
                for item in next_choice.ranked_alternatives[:6]
            ] if self.strategy == "adaptive" else []

    def step(self) -> ProbeStep | None:
        """Execute exactly one adaptively selected probe.

        Returns ``None`` when the diagnosis has already reached a terminal status.
        """
        if self.status not in ("running",):
            return None
        self.rejected_candidates = [
            {"probe_key": key, "reason": reason}
            for key, reason in _rejection_reasons(self.context())
        ]
        choice = self._choose_next()
        if choice is None:
            self._evaluate(None)
            return None

        request = ProbeRequest(
            probe_key=choice.probe_key,
            probe_type=choice.probe_type,
            source_node_id=self.source_node_id,
            destination_node_id=self.destination_node_id,
            destination_service=self.destination_service,
            port=self.port,
            control_node_id=self.control_node_id,
            gateway_node_id=self.gateway_node_id,
            resolver_node_id=self.resolver_node_id,
            hostname=self.hostname,
            sequence_number=self.probes_used + 1,
            tag=f"{self.diagnosis_id}:{self.probes_used + 1}",
        )
        probe = self.probes.get(choice.probe_type)
        if probe is None:  # pragma: no cover - the registry always covers all types
            raise ValidationError(
                f"no probe implementation is registered for {choice.probe_type.value}",
                field="probe_type",
            )
        observation = probe.run(request, self.lab)
        observation.selected_reason = choice.reason
        observation.information_gain = choice.expected_information_gain

        entropy_before = self.belief.entropy
        update = self.belief.observe(
            probe_key=choice.probe_key,
            outcome=observation.outcome,
            sequence_number=observation.sequence_number,
            evidence_weight=_evidence_weight(observation),
            note=choice.reason,
        )
        step = ProbeStep(
            sequence_number=observation.sequence_number,
            observation=observation,
            selected_reason=choice.reason,
            planned_information_gain=choice.expected_information_gain,
            planned_cost=choice.cost,
            belief_after={code.value: value for code, value in self.belief.probabilities.items()},
            entropy_before=entropy_before,
            entropy_after=update.entropy_after,
            update=update,
        )
        self.steps.append(step)

        self._evaluate(self._choose_next())
        return step

    def run_to_completion(self) -> Status:
        """Step until a terminal status or the probe budget is reached."""
        guard = self.max_probes + 1
        while self.status == "running" and self.probes_used < self.max_probes + 1:
            guard -= 1
            if guard < 0:  # pragma: no cover - defensive against a planner loop
                self.status = "error"
                self.stopping_reason = "The runner detected a non-terminating probe loop."
                self.completed_at = time.time()
                break
            if self.step() is None:
                break
        return self.status

    # ---- reporting -------------------------------------------------------
    def suspected_component(self) -> Localization:
        leader = self.belief.leader().code
        return localize(
            leader, [step.observation for step in self.steps], port=self.port
        )

    def explanation(self, *, baseline_comparison: dict[str, Any] | None = None) -> DiagnosisExplanation:
        contributions = build_contributions(
            [step.observation for step in self.steps],
            [step.update for step in self.steps],
        )
        localization = self.suspected_component()
        component = (
            (localization.component_kind, localization.component_id)
            if localization.found
            else None
        )
        return build_explanation(
            status=self.status,
            observations=[step.observation for step in self.steps],
            belief=self.belief.to_public(),
            contributions=contributions,
            stopping_reason=self.stopping_reason,
            next_probe=self.next_probe,
            suspected_component=component,
            baseline_comparison=baseline_comparison,
        )

    def beliefs(self) -> dict[str, Any]:
        public = self.belief.to_public()
        public["model_version"] = MODEL_VERSION
        public["prior_config_version"] = PRIOR_CONFIG_VERSION
        return public

    def to_public(self, *, include_report: bool = True) -> dict[str, Any]:
        localization = self.suspected_component()
        payload: dict[str, Any] = {
            "id": self.diagnosis_id,
            "session_id": self.session_id,
            "status": self.status,
            "strategy": self.strategy,
            "strategy_label": self.strategy_label,
            "source_node_id": self.source_node_id,
            "destination_node_id": self.destination_node_id,
            "destination_service": self.destination_service,
            "port": self.port,
            "mode": "SIMULATED LAB",
            "max_probes": self.max_probes,
            "probes_used": self.probes_used,
            "beliefs": self.beliefs(),
            "stopping_reason": self.stopping_reason,
            "next_probe": self.next_probe,
            "considered_alternatives": self.considered_alternatives,
            "rejected_candidates": self.rejected_candidates,
            "suspected_component": localization.to_public(),
            "steps": [step.to_public() for step in self.steps],
            "random_seed": self.random_seed,
            "model_version": MODEL_VERSION,
            "prior_config_version": PRIOR_CONFIG_VERSION,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
        }
        if include_report:
            payload["report"] = self.explanation().to_public()
        return payload


def fault_signature(faults: list[FaultConfig]) -> tuple[tuple[Any, ...], ...]:
    """Canonical, comparable description of a fault set.

    Used to detect that a session's lab state changed underneath a diagnosis. Two
    fault lists with the same signature produce identical lab behaviour.
    """
    return tuple(
        sorted(
            (
                fault.id,
                fault.fault_type.value,
                fault.target_id,
                fault.is_active,
                tuple(sorted((str(key), str(value)) for key, value in (fault.parameters or {}).items())),
            )
            for fault in faults
        )
    )


def _evidence_weight(observation: ProbeObservation) -> float:
    """How strongly one observation should move the belief.

    Most outcomes are unambiguous (weight 1.0). Intermittent or loss-limited
    results are softened because a *single* sample of a stochastic quantity is
    weaker evidence than a deterministic protocol outcome — and the probes already
    report the repeated-attempt counts that justify the softening.
    """
    if observation.outcome == "PARTIAL_LOSS":
        return 0.6
    if observation.outcome == "INCONCLUSIVE_LOSS":
        return 0.75
    if observation.outcome in ("REACHABLE_SLOW", "CONNECTED_SLOW", "RESOLVED_SLOW"):
        return 0.9
    return 1.0


def _rejection_reasons(context: DiagnosisContext) -> list[tuple[str, str]]:
    from .planner import candidate_rejections

    return [
        (item["probe_key"], item["reason"]) for item in candidate_rejections(context)
    ]


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def run_diagnosis(
    *,
    diagnosis_id: str,
    session_id: str,
    lab: LabState,
    topology: Topology,
    source_node_id: str,
    destination_node_id: str,
    destination_service: str | None = None,
    port: int | None = None,
    strategy: str = "adaptive",
    max_probes: int = 8,
    random_seed: int | None = None,
    hostname: str | None = None,
    to_completion: bool = True,
) -> DiagnosisRun:
    """Convenience factory used by the API, the experiments and the tests."""
    from ..core.config import DEFAULT_MAX_PROBES

    # ``None`` means "use the configured default"; an explicit 0 is a caller error
    # and must be rejected rather than silently rewritten to the default.
    effective_max = DEFAULT_MAX_PROBES if max_probes is None else max_probes
    if effective_max < 1:
        raise ValidationError("max_probes must be at least 1", field="max_probes")
    run = DiagnosisRun(
        diagnosis_id=diagnosis_id,
        session_id=session_id,
        source_node_id=source_node_id,
        destination_node_id=destination_node_id,
        destination_service=destination_service,
        port=port,
        strategy=strategy,
        max_probes=effective_max,
        lab=lab,
        topology=topology,
        hostname=hostname,
        random_seed=lab.random_seed if random_seed is None else random_seed,
    )
    if to_completion:
        run.run_to_completion()
    else:
        run._evaluate(run._choose_next())
    return run


__all__ = [
    "DiagnosisRun",
    "ProbeStep",
    "run_diagnosis",
]

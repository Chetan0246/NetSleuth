"""The adaptive probe planner: which test to run next, and when to stop.

Three responsibilities, deliberately separated:

* :func:`build_candidates` — turn a *diagnosis context* (source, destination,
  service, topology) into the set of probes that can legitimately be proposed.
  Candidates are filtered by what the topology can actually support (no resolver
  ⇒ no resolver ping, no service ⇒ no service health check).
* :func:`select_next_probe` — score every eligible candidate with expected
  information gain per unit cost and return the winner **plus** the full ranking,
  so the UI can show why the probe was chosen and what was considered next.
* :func:`evaluate_stopping_rule` — decide whether the diagnosis may terminate.

The planner is pure with respect to the lab: it reads topology metadata and the
current belief, never the injected fault. ``select_next_probe`` is the only place
that decides the probe order for the adaptive strategy; the baseline strategy in
:mod:`app.diagnosis.baseline` reuses the exact same candidate set but a fixed
order, which is what makes the comparison fair (plan.md section 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.config import (
    CONFIDENCE_LEAD,
    CONFIDENCE_THRESHOLD,
    MIN_INFORMATION_GAIN_BITS,
    MIN_PROBE_TYPES_FOR_NO_FAULT,
)
from ..lab.graph import Topology
from ..lab.outcomes import (
    CANONICAL_CANDIDATE_ORDER,
    PROBE_COSTS,
    PROBE_LABELS,
    IcmpSelector,
    ProbeType,
    probe_key as make_probe_key,
    probe_key_label,
)
from .bayes import BeliefState
from .hypotheses import Hypothesis
from .information_gain import ProbeCandidate, rank_candidates


@dataclass
class DiagnosisContext:
    """Everything the planner legitimately knows about the diagnosis target.

    This object is built from the *session configuration*, never from the active
    fault list, so the planner cannot peek at the ground truth.
    """

    source_node_id: str
    destination_node_id: str
    destination_service: str | None = None
    port: int | None = None
    hostname: str | None = None
    gateway_node_id: str | None = None
    resolver_node_id: str | None = None
    control_node_id: str | None = None
    topology: Topology | None = None
    #: Probe keys that must not be proposed (already executed, or disabled by the caller).
    excluded_probe_keys: set[str] = field(default_factory=set)

    def target_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source_node_id": self.source_node_id,
            "destination_node_id": self.destination_node_id,
            "destination_service": self.destination_service,
            "port": self.port,
            "hostname": self.hostname,
        }
        if self.topology is not None:
            source = self.topology.node(self.source_node_id)
            destination = self.topology.node(self.destination_node_id)
            out["source_ip"] = source.ip_address
            out["destination_ip"] = destination.ip_address
            out["source_name"] = source.name
            out["destination_name"] = destination.name
            if self.control_node_id and self.topology.has_node(self.control_node_id):
                out["control_node_id"] = self.control_node_id
                out["control_ip"] = self.topology.node(self.control_node_id).ip_address
        return out


def build_candidates(context: DiagnosisContext) -> list[ProbeCandidate]:
    """Probes that can legitimately be proposed for this context.

    The canonical order (``app.lab.outcomes.CANONICAL_CANDIDATE_ORDER``) is the
    source of the candidate universe; this function only *removes* candidates the
    topology cannot support. Removing a candidate is always recorded as a reason
    string so the UI can explain an absence instead of silently omitting a test.
    """
    topology = context.topology
    candidates: list[ProbeCandidate] = []
    if topology is not None and not topology.has_node(context.destination_node_id):
        return candidates

    for probe_type, selector in CANONICAL_CANDIDATE_ORDER:
        key = make_probe_key(probe_type, selector)
        if key in context.excluded_probe_keys:
            continue
        reason = _unsupported_reason(probe_type, selector, context)
        if reason is not None:
            continue
        candidates.append(
            ProbeCandidate(
                probe_key=key,
                probe_type_value=probe_type.value,
                label=probe_key_label(key),
                cost=PROBE_COSTS.get(probe_type, 1.0),
            )
        )
    return candidates


def _unsupported_reason(
    probe_type: ProbeType, selector: str | None, context: DiagnosisContext
) -> str | None:
    """Return a human-readable reason when a candidate cannot be proposed."""
    topology = context.topology
    if topology is None:
        return None
    if probe_type is ProbeType.ICMP_REACHABILITY:
        if selector == IcmpSelector.GATEWAY.value and not context.gateway_node_id:
            return "the source host has no default gateway in this topology"
        if selector == IcmpSelector.RESOLVER.value and not context.resolver_node_id:
            return "this topology has no reachable DNS resolver to ping"
        if selector == IcmpSelector.CONTROL_DESTINATION.value and not context.control_node_id:
            return "no independent control target is available in the destination segment"
        return None
    if probe_type is ProbeType.SERVICE_HEALTH:
        if not context.destination_service:
            return (
                "no application service was selected, so a service health check has nothing "
                "to test"
            )
        return None
    if probe_type is ProbeType.MTU_PROBE:
        if not context.destination_node_id:
            return "no destination was selected"
        return None
    return None


def candidate_rejections(context: DiagnosisContext) -> list[dict[str, str]]:
    """Why certain canonical probes are not eligible (for the UI's probe panel)."""
    out: list[dict[str, str]] = []
    for probe_type, selector in CANONICAL_CANDIDATE_ORDER:
        key = make_probe_key(probe_type, selector)
        reason = _unsupported_reason(probe_type, selector, context)
        if reason is not None:
            out.append({"probe_key": key, "label": probe_key_label(key), "reason": reason})
        elif key in context.excluded_probe_keys:
            out.append(
                {
                    "probe_key": key,
                    "label": probe_key_label(key),
                    "reason": "already executed in this diagnosis",
                }
            )
    return out


@dataclass
class ProbeChoice:
    """The planner's decision for one step."""

    probe_key: str
    probe_type: ProbeType
    selector: str | None
    label: str
    reason: str
    expected_information_gain: float
    cost: float
    score: float
    outcome_distribution: dict[str, float]
    ranked_alternatives: list[InformationGain]
    entropy_before: float

    def to_public(self) -> dict[str, Any]:
        return {
            "probe_key": self.probe_key,
            "probe_type": self.probe_type.value,
            "selector": self.selector,
            "label": self.label,
            "reason": self.reason,
            "expected_information_gain_bits": round(self.expected_information_gain, 6),
            "cost": self.cost,
            "score": round(self.score, 6),
            "entropy_before_bits": round(self.entropy_before, 6),
            "outcome_distribution": {
                key: round(value, 6) for key, value in self.outcome_distribution.items()
            },
            "considered_alternatives": [
                {
                    "probe_key": item.probe_key,
                    "expected_information_gain_bits": round(item.expected_information_gain, 6),
                    "cost": item.cost,
                    "score": round(item.scored_value, 6),
                }
                for item in self.ranked_alternatives[:6]
            ],
        }


def select_next_probe(
    belief: BeliefState,
    context: DiagnosisContext,
    table: Any,
    *,
    executed_probe_keys: list[str] | None = None,
) -> ProbeChoice | None:
    """Pick the next probe by expected information gain per unit cost.

    Returns ``None`` when no eligible candidate remains. The returned choice
    carries a plain-language reason that names the ambiguity the probe is expected
    to resolve, computed from the current *predictive* outcome distribution: the
    outcomes that would most reduce entropy are the ones named.
    """
    executed = list(executed_probe_keys or [])
    # Every candidate is single-shot: an executed probe is removed from the eligible
    # set. Re-running an identical request would hand the Bayes update the same
    # observation twice, which inflates confidence without adding information. Probes
    # that need a stochastic quantity measured already aggregate repeated samples
    # internally (ICMP_ECHO_COUNT echoes; MTU_PROBE_ATTEMPTS per ladder size).
    candidates = [c for c in build_candidates(context) if c.probe_key not in executed]
    if not candidates:
        return None

    ranked = rank_candidates(candidates, belief.probabilities, table)
    if not ranked:
        return None
    best = ranked[0]
    candidate = next(item for item in candidates if item.probe_key == best.probe_key)
    probe_type, selector = _split_key(best.probe_key)
    reason = explain_selection(best, belief, probe_type, selector)
    return ProbeChoice(
        probe_key=best.probe_key,
        probe_type=probe_type,
        selector=selector,
        label=candidate.label,
        reason=reason,
        expected_information_gain=best.expected_information_gain,
        cost=best.cost,
        score=best.scored_value,
        outcome_distribution=best.outcome_distribution,
        ranked_alternatives=ranked,
        entropy_before=best.entropy_before,
    )


def _split_key(key: str) -> tuple[ProbeType, str | None]:
    if ":" in key:
        head, tail = key.split(":", 1)
        return ProbeType(head), tail
    return ProbeType(key), None


def explain_selection(
    gain: InformationGain,
    belief: BeliefState,
    probe_type: ProbeType,
    selector: str | None,
) -> str:
    """Build the plain-language "why this probe" sentence from real numbers."""
    ranked = belief.ranked()
    leader = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    # The outcomes that would most reduce entropy are the ones worth naming.
    informative = sorted(
        gain.outcome_posterior_entropies.items(), key=lambda item: (item[1], item[0])
    )[:2]
    outcome_text = " or ".join(f"{name} (resulting uncertainty {round(value, 2)} bits)"
                               for name, value in informative)
    selector_text = f" ({selector})" if selector else ""
    label = f"{PROBE_LABELS[probe_type]}{selector_text}"
    lead_text = (
        f"The leading hypothesis is {leader.code.value} at {leader.probability:.0%}"
        + (
            f", ahead of {runner.code.value} at {runner.probability:.0%}"
            if runner
            else ""
        )
        + ". "
    )
    return (
        f"{label} was selected because it is expected to remove "
        f"{gain.expected_information_gain:.3f} bits of uncertainty about the cause "
        f"(current belief entropy {gain.entropy_before:.3f} bits) at a relative cost of "
        f"{gain.cost:g}. The outcomes that would most change the ranking are "
        f"{outcome_text}. {lead_text}"
        "It is chosen for its expected information gain, not because it is next in a "
        "fixed checklist."
    )


# ---------------------------------------------------------------------------
# stopping rule
# ---------------------------------------------------------------------------

Status = Literal["running", "confident", "inconclusive", "budget_exhausted", "error"]


@dataclass
class StoppingDecision:
    should_stop: bool
    status: Status
    reason: str
    leading_code: Hypothesis | None = None
    leading_probability: float = 0.0
    lead_over_runner_up: float = 0.0
    best_available_gain: float | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "should_stop": self.should_stop,
            "status": self.status,
            "reason": self.reason,
            "leading_hypothesis": self.leading_code.value if self.leading_code else None,
            "leading_probability": round(self.leading_probability, 6),
            "lead_over_runner_up": round(self.lead_over_runner_up, 6),
            "best_available_information_gain_bits": (
                None if self.best_available_gain is None else round(self.best_available_gain, 6)
            ),
        }


def _breadth_satisfied(
    executed_probe_keys: list[str], minimum_types: int
) -> tuple[bool, str]:
    """Whether enough distinct probe classes ran to support a "no fault" verdict."""
    executed_types = {key.split(":")[0] for key in executed_probe_keys}
    if len(executed_types) >= minimum_types:
        return True, ""
    return False, (
        f"only {len(executed_types)} of the {minimum_types} required probe classes have "
        "been executed, and a healthy result from the classes that have run says nothing "
        "about the layers that have not been tested yet"
    )


def evaluate_stopping_rule(
    belief: BeliefState,
    *,
    probes_used: int,
    max_probes: int,
    next_probe: ProbeChoice | None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
    confidence_lead: float = CONFIDENCE_LEAD,
    min_information_gain: float = MIN_INFORMATION_GAIN_BITS,
    executed_probe_keys: list[str] | None = None,
    min_probe_types_for_no_fault: int = MIN_PROBE_TYPES_FOR_NO_FAULT,
) -> StoppingDecision:
    """Apply the documented stopping rule (plan.md section 9.4).

    Order of checks matters and is part of the behaviour:

    0. *Breadth* — a ``NO_FAULT_DETECTED`` verdict is only allowed once enough
       distinct probe classes have run, because "nothing is wrong" is a claim about
       every layer. Without this, three consistent probes from the same few layers
       would be reported as a confident all-clear while the layer holding the real
       fault had never been tested.
    1. *Confidence* — the leader passes the threshold **and** leads the runner-up
       by the configured margin. Only then is a fault asserted.
    2. *Budget* — the probe budget is exhausted, so the run stops whatever the
       belief says; the status is ``budget_exhausted`` and the report must present
       the result as unresolved rather than confident.
    3. *No useful probe left* — either no candidate remains, or the best remaining
       candidate would reduce entropy by less than the configured minimum. The
       status is ``inconclusive``.

    A decision is only ever ``confident`` when the separation condition holds, so
    an ambiguous belief is reported as ambiguous instead of being forced.
    """
    ranked = belief.ranked()
    leader = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    lead = leader.probability - (runner.probability if runner else 0.0)

    if leader.probability >= confidence_threshold and lead >= confidence_lead:
        if leader.code is Hypothesis.NO_FAULT_DETECTED:
            broad_enough, reason = _breadth_satisfied(
                list(executed_probe_keys or []), min_probe_types_for_no_fault
            )
            if not broad_enough:
                return StoppingDecision(
                    should_stop=False,
                    status="running",
                    reason=(
                        "The evidence collected so far is consistent with a healthy path, but "
                        f"{reason}. A conclusion of \"no fault\" is withheld until the "
                        "remaining probe classes have been exercised."
                    ),
                    leading_code=leader.code,
                    leading_probability=leader.probability,
                    lead_over_runner_up=lead,
                    best_available_gain=(
                        next_probe.expected_information_gain if next_probe else None
                    ),
                )
        return StoppingDecision(
            should_stop=True,
            status="confident",
            reason=(
                f"{leader.code.value} reached {leader.probability:.1%}, which passes the "
                f"{confidence_threshold:.0%} threshold, and leads the runner-up"
                + (f" ({runner.code.value} at {runner.probability:.1%})" if runner else "")
                + f" by {lead:.1%}, which passes the required {confidence_lead:.0%} margin."
            ),
            leading_code=leader.code,
            leading_probability=leader.probability,
            lead_over_runner_up=lead,
            best_available_gain=(next_probe.expected_information_gain if next_probe else None),
        )

    if probes_used >= max_probes:
        return StoppingDecision(
            should_stop=True,
            status="budget_exhausted",
            reason=(
                f"The probe budget of {max_probes} was reached with the leading hypothesis "
                f"{leader.code.value} at {leader.probability:.1%}"
                + (
                    f" and a margin of only {lead:.1%} over {runner.code.value}"
                    if runner
                    else ""
                )
                + ". The evidence is not sufficient to assert a single cause."
            ),
            leading_code=leader.code,
            leading_probability=leader.probability,
            lead_over_runner_up=lead,
            best_available_gain=(next_probe.expected_information_gain if next_probe else None),
        )

    if next_probe is None:
        return StoppingDecision(
            should_stop=True,
            status="inconclusive",
            reason=(
                "Every probe available for this target has been executed and the remaining "
                f"uncertainty still leaves {leader.code.value} at {leader.probability:.1%} with "
                f"a margin of {lead:.1%}. No further test of this class can separate the "
                "candidates, so the result is reported as inconclusive."
            ),
            leading_code=leader.code,
            leading_probability=leader.probability,
            lead_over_runner_up=lead,
            best_available_gain=None,
        )

    if next_probe.expected_information_gain < min_information_gain:
        return StoppingDecision(
            should_stop=True,
            status="inconclusive",
            reason=(
                "The best remaining probe would reduce uncertainty by only "
                f"{next_probe.expected_information_gain:.4f} bits, which is below the "
                f"{min_information_gain:.4f} bit minimum. Running it would not change the "
                "ranking, so the diagnosis stops and reports the ambiguity."
            ),
            leading_code=leader.code,
            leading_probability=leader.probability,
            lead_over_runner_up=lead,
            best_available_gain=next_probe.expected_information_gain,
        )

    return StoppingDecision(
        should_stop=False,
        status="running",
        reason=(
            f"{leader.code.value} leads at {leader.probability:.1%} with a margin of "
            f"{lead:.1%}, which does not yet satisfy the stopping rule. The next probe is "
            f"expected to remove {next_probe.expected_information_gain:.3f} bits."
        ),
        leading_code=leader.code,
        leading_probability=leader.probability,
        lead_over_runner_up=lead,
        best_available_gain=next_probe.expected_information_gain,
    )


__all__ = [
    "DiagnosisContext",
    "ProbeChoice",
    "Status",
    "StoppingDecision",
    "build_candidates",
    "candidate_rejections",
    "evaluate_stopping_rule",
    "explain_selection",
    "select_next_probe",
]

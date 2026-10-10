"""Fixed-order baseline strategy (plan.md section 10).

The baseline exists purely as a *fair comparison* for the adaptive planner. It:

* draws candidates from the **same** :func:`app.diagnosis.planner.build_candidates`
  set,
* runs them in the documented canonical order,
* uses the **same** probe implementations, likelihood table, priors and stopping
  rule,
* has the same probe budget and the same repeat policy.

Only the order differs, which is what makes the measured difference attributable
to probe *selection* rather than to a difference in interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.config import (
    CONFIDENCE_LEAD,
    CONFIDENCE_THRESHOLD,
    MIN_INFORMATION_GAIN_BITS,
)
from ..lab.outcomes import CANONICAL_CANDIDATE_ORDER, PROBE_COSTS, probe_key as make_probe_key
from ..lab.outcomes import probe_key_label
from .bayes import BeliefState
from .planner import (
    DiagnosisContext,
    ProbeChoice,
    StoppingDecision,
    build_candidates,
    evaluate_stopping_rule,
)

#: The documented fixed order. Identical to the canonical candidate order in
#: :mod:`app.lab.outcomes`, restated here so the baseline's sequence is explicit
#: and cannot drift silently when the candidate universe changes.
BASELINE_ORDER: list[str] = [
    make_probe_key(probe_type, selector) for probe_type, selector in CANONICAL_CANDIDATE_ORDER
]

BASELINE_SEQUENCE_DESCRIPTION: list[str] = [
    "1. ICMP-style reachability to the destination",
    "2. ICMP-style reachability to the default gateway",
    "3. ICMP-style reachability to the resolver",
    "4. DNS lookup",
    "5. TTL-limited path trace (traceroute)",
    "6. TCP connect to the destination port",
    "7. MTU / packet-size ladder",
    "8. Application service health check",
    "9. ICMP-style reachability to an independent control target",
]


@dataclass
class BaselinePlan:
    """The fixed order restricted to the candidates this context supports."""

    ordered_keys: list[str]
    skipped: list[dict[str, str]]


def build_baseline_plan(context: DiagnosisContext) -> BaselinePlan:
    """Restrict the fixed order to candidates the topology can actually support."""
    supported = {candidate.probe_key for candidate in build_candidates(context)}
    ordered = [key for key in BASELINE_ORDER if key in supported]
    skipped = [
        {
            "probe_key": key,
            "label": probe_key_label(key),
            "reason": "not supported by the selected topology or target",
        }
        for key in BASELINE_ORDER
        if key not in supported
    ]
    return BaselinePlan(ordered_keys=ordered, skipped=skipped)


def select_baseline_probe(
    plan: BaselinePlan,
    executed_keys: list[str],
    belief: BeliefState | None = None,
) -> ProbeChoice | None:
    """Next probe in the fixed order, ignoring how informative it would be.

    This is deliberately *not* information-gain driven; that is the whole point of
    the baseline. The function still returns a :class:`ProbeChoice` with the EIG
    values filled in, so the UI and the experiment export can report what the
    baseline *would* have gained — but the choice itself never depends on them.
    The EIG is computed from the *current* belief (falling back to a uniform
    reference only when no belief is supplied) so the shared stopping rule sees the
    same quantity for both strategies.
    """
    from ..lab.outcomes import ProbeType
    from .information_gain import expected_information_gain
    from .likelihoods import likelihood_table

    reference = belief.probabilities if belief is not None else _uniform_belief_placeholder()
    for key in plan.ordered_keys:
        if key not in executed_keys:
            probe_type, selector = _split(key)
            gain = expected_information_gain(
                key,
                reference,
                likelihood_table(),
                cost=PROBE_COSTS.get(probe_type, 1.0),
            )
            return ProbeChoice(
                probe_key=key,
                probe_type=probe_type,
                selector=selector,
                label=probe_key_label(key),
                reason=(
                    f"Fixed-order baseline: {probe_key_label(key)} is step "
                    f"{plan.ordered_keys.index(key) + 1} of the documented sequence. The "
                    "baseline always executes the same order regardless of the evidence, "
                    "which is exactly what it is being compared against."
                ),
                expected_information_gain=gain.expected_information_gain,
                cost=gain.cost,
                score=gain.scored_value,
                outcome_distribution=gain.outcome_distribution,
                ranked_alternatives=[],
                entropy_before=gain.entropy_before,
            )
    return None


def baseline_stopping_rule(
    belief: BeliefState,
    *,
    probes_used: int,
    max_probes: int,
    next_probe: ProbeChoice | None,
    executed_probe_keys: list[str] | None = None,
) -> StoppingDecision:
    """The baseline uses the identical stopping rule (same thresholds, same logic)."""
    return evaluate_stopping_rule(
        belief,
        probes_used=probes_used,
        max_probes=max_probes,
        next_probe=next_probe,
        confidence_threshold=CONFIDENCE_THRESHOLD,
        confidence_lead=CONFIDENCE_LEAD,
        min_information_gain=MIN_INFORMATION_GAIN_BITS,
        executed_probe_keys=executed_probe_keys,
    )


def _split(key: str) -> tuple[Any, str | None]:
    from ..lab.outcomes import ProbeType

    if ":" in key:
        head, tail = key.split(":", 1)
        return ProbeType(head), tail
    return ProbeType(key), None


def _uniform_belief_placeholder() -> dict[Any, float]:
    from .hypotheses import Hypothesis

    codes = list(Hypothesis)
    return {code: 1.0 / len(codes) for code in codes}


__all__ = [
    "BASELINE_ORDER",
    "BASELINE_SEQUENCE_DESCRIPTION",
    "BaselinePlan",
    "baseline_stopping_rule",
    "build_baseline_plan",
    "select_baseline_probe",
]

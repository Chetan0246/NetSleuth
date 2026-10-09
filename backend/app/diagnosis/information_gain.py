"""Expected information gain (EIG) for probe selection (plan.md section 9.3).

Definitions used (all in bits, base-2 logarithms)::

    H(B)      = -Σ_h p(h) log2 p(h)                     # entropy of the belief
    P(o|q,B)  = Σ_h P(o | h, q) · p(h)                  # predictive outcome dist.
    H(B|o,q)  = entropy of the posterior after observing o
    EIG(q)    = H(B) - Σ_o P(o|q,B) · H(B|o,q)

``EIG`` is the expected reduction in uncertainty about *which fault is present*.
A probe that cannot distinguish any hypothesis has EIG ≈ 0 regardless of how
"informative" its output text is, which is exactly the behaviour we want: the
planner should not run an expensive test whose result cannot change the answer.

These functions are pure: they never mutate the belief state and never touch the
lab, so they are cheap to call for every candidate on every step.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bayes import LIKELIHOOD_FLOOR, entropy
from .hypotheses import Hypothesis
from .likelihoods import LikelihoodTable


@dataclass(frozen=True)
class ProbeCandidate:
    """One probe the planner can propose."""

    probe_key: str
    probe_type_value: str
    label: str
    cost: float


@dataclass(frozen=True)
class InformationGain:
    """The full EIG calculation for one candidate, kept for explainability."""

    probe_key: str
    entropy_before: float
    expected_entropy_after: float
    expected_information_gain: float
    cost: float
    #: EIG per unit of relative probe cost (see :data:`app.lab.outcomes.PROBE_COSTS`).
    scored_value: float
    outcome_distribution: dict[str, float]
    outcome_posterior_entropies: dict[str, float]

    def to_public(self) -> dict[str, object]:
        return {
            "probe_key": self.probe_key,
            "entropy_before": round(self.entropy_before, 6),
            "expected_entropy_after": round(self.expected_entropy_after, 6),
            "expected_information_gain_bits": round(self.expected_information_gain, 6),
            "cost": self.cost,
            "score": round(self.scored_value, 6),
            "outcome_distribution": {
                key: round(value, 6) for key, value in self.outcome_distribution.items()
            },
            "outcome_posterior_entropies": {
                key: round(value, 6)
                for key, value in self.outcome_posterior_entropies.items()
            },
        }


def expected_information_gain(
    probe_key: str,
    beliefs: dict[Hypothesis, float],
    table: LikelihoodTable,
    *,
    cost: float = 1.0,
    floor: float = LIKELIHOOD_FLOOR,
) -> InformationGain:
    """Compute EIG (and the EIG/cost score) of one candidate probe."""
    if cost <= 0.0:
        cost = 1.0
    current = {code: value for code, value in beliefs.items()}
    h_before = entropy(current)
    distribution = table.outcome_distribution(probe_key, current)

    expected_h_after = 0.0
    posterior_entropies: dict[str, float] = {}
    for outcome, probability in distribution.items():
        posterior = table.posterior_given(probe_key, outcome, current, floor=floor)
        h_after = entropy(posterior)
        posterior_entropies[outcome] = h_after
        expected_h_after += probability * h_after

    eig = max(h_before - expected_h_after, 0.0)
    return InformationGain(
        probe_key=probe_key,
        entropy_before=h_before,
        expected_entropy_after=expected_h_after,
        expected_information_gain=eig,
        cost=cost,
        scored_value=eig / cost,
        outcome_distribution=distribution,
        outcome_posterior_entropies=posterior_entropies,
    )


def rank_candidates(
    candidates: list[ProbeCandidate],
    beliefs: dict[Hypothesis, float],
    table: LikelihoodTable,
    *,
    floor: float = LIKELIHOOD_FLOOR,
) -> list[InformationGain]:
    """Score and rank candidates deterministically.

    Ordering: descending EIG/cost score, then descending absolute EIG, then the
    probe key alphabetically. The final tie-break is what makes the planner and
    therefore the whole diagnosis reproducible run-to-run.
    """
    scored = [
        expected_information_gain(
            candidate.probe_key, beliefs, table, cost=candidate.cost, floor=floor
        )
        for candidate in candidates
    ]
    return sorted(
        scored,
        key=lambda item: (-item.scored_value, -item.expected_information_gain, item.probe_key),
    )


__all__ = [
    "InformationGain",
    "ProbeCandidate",
    "expected_information_gain",
    "rank_candidates",
]

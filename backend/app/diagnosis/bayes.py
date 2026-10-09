"""Bayesian belief updating over the hypothesis catalogue (plan.md section 9.2).

The updater is a plain, auditable Bayes filter::

    posterior(h) ∝ likelihood(observation | h) * prior(h)

with three engineering safeguards that the tests pin down:

1. **Floor** — every likelihood is lifted to ``LIKELIHOOD_FLOOR`` before use, so a
   single unexpected observation can never annihilate a hypothesis. Without this
   the ranking would be destroyed by one noisy probe.
2. **Normalisation** — posteriors are normalised to sum to exactly 1.0 (asserted
   in tests), which keeps the reported confidences meaningful.
3. **Full audit record** — every update stores the prior, the observation code,
   the raw likelihood, the floored likelihood and the resulting posterior, so the
   explanation layer never has to reconstruct why a belief moved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.errors import ValidationError
from .hypotheses import DEFAULT_PRIORS, Hypothesis, HypothesisSpec, hypothesis_spec
from .likelihoods import likelihood_table

#: Minimum likelihood any hypothesis may carry for an observation. Chosen to be
#: small enough to express "very unlikely" but large enough that the belief kept
#: for a hypothesis stays interpretable after several observations.
LIKELIHOOD_FLOOR = 1e-4


@dataclass(frozen=True)
class HypothesisBelief:
    """One hypothesis with its current probability and moving context."""

    code: Hypothesis
    probability: float
    prior: float
    #: Log-likelihood evidence accumulated so far (diagnostics/tests only).
    log_evidence: float = 0.0

    @property
    def spec(self) -> HypothesisSpec:
        return hypothesis_spec(self.code)


@dataclass
class BeliefUpdate:
    """A complete, replayable record of one Bayesian update."""

    observation_key: str
    outcome: str
    probe_key: str
    sequence_number: int
    priors: dict[str, float]
    raw_likelihoods: dict[str, float]
    floored_likelihoods: dict[str, float]
    posteriors: dict[str, float]
    evidence_weight: float
    entropy_before: float
    entropy_after: float
    note: str = ""

    def likelihood_contribution(self, code: Hypothesis | str) -> float:
        """Ratio posterior/prior for one hypothesis: >1 means the evidence raised it."""
        key = Hypothesis(code).value
        prior = self.priors.get(key, 0.0)
        if prior <= 0.0:
            return 0.0
        return self.posteriors.get(key, 0.0) / prior

    def to_public(self) -> dict[str, object]:
        return {
            "probe_key": self.probe_key,
            "outcome": self.outcome,
            "observation_key": self.observation_key,
            "sequence_number": self.sequence_number,
            "entropy_before": round(self.entropy_before, 6),
            "entropy_after": round(self.entropy_after, 6),
            "evidence_weight": round(self.evidence_weight, 4),
            "posteriors": {k: round(v, 6) for k, v in self.posteriors.items()},
            "likelihood_ratios": {
                code.value: round(self.likelihood_contribution(code), 4) for code in self.posteriors
            },
            "note": self.note,
        }


def entropy(probabilities: dict[Hypothesis | str, float]) -> float:
    """Shannon entropy in bits of a probability distribution.

    All-zero or empty input returns 0.0 rather than raising, so a degenerate
    distribution cannot crash a diagnosis; negative and non-finite values are
    rejected as a programming error instead of being silently clamped.
    """
    total = 0.0
    for value in probabilities.values():
        if not math.isfinite(value) or value < 0.0:
            raise ValidationError(
                f"probability values must be finite and non-negative, got {value!r}",
                field="probabilities",
            )
        total += value
    if total <= 0.0:
        return 0.0
    bits = 0.0
    for value in probabilities.values():
        if value <= 0.0:
            continue
        p = value / total
        bits -= p * math.log2(p)
    # Guard against a tiny negative value from floating-point cancellation.
    return max(bits, 0.0)


def normalise(weights: dict[Hypothesis, float]) -> dict[Hypothesis, float]:
    """Normalise weights to a probability distribution summing to 1.0."""
    total = sum(weights.values())
    if total <= 0.0:
        # Degenerate input: fall back to the uniform distribution rather than
        # returning zeros, which would make the reported confidence meaningless.
        uniform = 1.0 / len(weights) if weights else 0.0
        return {code: uniform for code in weights}
    return {code: value / total for code, value in weights.items()}


class BeliefState:
    """The engine's belief over hypotheses, updated one observation at a time."""

    def __init__(self, priors: dict[Hypothesis, float] | None = None) -> None:
        self._priors: dict[Hypothesis, float] = normalise(dict(priors or DEFAULT_PRIORS))
        self._log_evidence: dict[Hypothesis, float] = {code: 0.0 for code in self._priors}
        self._posterior: dict[Hypothesis, float] = dict(self._priors)
        self.history: list[BeliefUpdate] = []

    # ---- accessors -------------------------------------------------------
    @property
    def priors(self) -> dict[Hypothesis, float]:
        return dict(self._priors)

    @property
    def probabilities(self) -> dict[Hypothesis, float]:
        return dict(self._posterior)

    @property
    def entropy(self) -> float:
        return entropy(self._posterior)

    def probability(self, code: Hypothesis | str) -> float:
        return self._posterior.get(Hypothesis(code), 0.0)

    def ranked(self) -> list[HypothesisBelief]:
        """Hypotheses sorted by descending probability (deterministic tie-break)."""
        ordered = sorted(
            self._posterior.items(),
            key=lambda item: (-item[1], item[0].value),
        )
        return [
            HypothesisBelief(
                code=code,
                probability=value,
                prior=self._priors[code],
                log_evidence=self._log_evidence[code],
            )
            for code, value in ordered
        ]

    def leader(self) -> HypothesisBelief:
        return self.ranked()[0]

    def runner_up(self) -> HypothesisBelief | None:
        ranked = self.ranked()
        return ranked[1] if len(ranked) > 1 else None

    # ---- updating --------------------------------------------------------
    def observe(
        self,
        *,
        probe_key: str,
        outcome: str,
        sequence_number: int = 0,
        evidence_weight: float = 1.0,
        note: str = "",
    ) -> BeliefUpdate:
        """Apply one observation and return the full update record.

        ``evidence_weight`` scales how far the observation moves the belief: it is
        set by the runner for probes whose *result is itself quantified* (an
        information-gain-like softening for intermittent results) and stays 1.0
        for unambiguous outcomes.
        """
        table = likelihood_table()
        observation_key = f"{probe_key}|{outcome}"
        priors = dict(self._posterior)
        entropy_before = entropy(priors)

        raw: dict[Hypothesis, float] = {}
        for code in priors:
            likelihood = table.likelihood(probe_key, outcome, code)
            if likelihood < 0.0 or not math.isfinite(likelihood):
                raise ValidationError(
                    f"model produced an invalid likelihood {likelihood!r} for "
                    f"{code.value} / {observation_key}",
                    field="likelihood",
                )
            raw[code] = likelihood

        floored: dict[Hypothesis, float] = {}
        for code, likelihood in raw.items():
            value = max(likelihood, LIKELIHOOD_FLOOR)
            if evidence_weight != 1.0:
                # Soften: interpolate the likelihood toward 1.0 (no information) in
                # log space, which keeps the update monotone in evidence_weight.
                value = math.exp(math.log(value) * evidence_weight)
            floored[code] = value

        weighted = {code: priors[code] * floored[code] for code in priors}
        posterior = normalise(weighted)

        for code, value in posterior.items():
            if value > 0.0:
                self._log_evidence[code] += math.log(value / max(priors[code], 1e-12))
        self._posterior = posterior

        update = BeliefUpdate(
            observation_key=observation_key,
            outcome=outcome,
            probe_key=probe_key,
            sequence_number=sequence_number,
            priors={code.value: value for code, value in priors.items()},
            raw_likelihoods={code.value: value for code, value in raw.items()},
            floored_likelihoods={code.value: value for code, value in floored.items()},
            posteriors={code.value: value for code, value in posterior.items()},
            evidence_weight=evidence_weight,
            entropy_before=entropy_before,
            entropy_after=entropy(posterior),
            note=note,
        )
        self.history.append(update)
        return update

    def reset(self, priors: dict[Hypothesis, float] | None = None) -> None:
        self._priors = normalise(dict(priors or self._priors))
        self._log_evidence = {code: 0.0 for code in self._priors}
        self._posterior = dict(self._priors)
        self.history = []

    def to_public(self) -> dict[str, object]:
        ranked = self.ranked()
        leader = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        return {
            "entropy_bits": round(self.entropy, 6),
            "leader": {
                "code": leader.code.value,
                "title": leader.spec.title,
                "probability": round(leader.probability, 6),
            },
            "lead_over_runner_up": round(
                leader.probability - (runner.probability if runner else 0.0), 6
            ),
            "ranked": [
                {
                    "code": belief.code.value,
                    "title": belief.spec.title,
                    "layers": list(belief.spec.layers),
                    "summary": belief.spec.summary,
                    "component_kind": belief.spec.component_kind,
                    "probability": round(belief.probability, 6),
                    "prior": round(belief.prior, 6),
                }
                for belief in ranked
            ],
        }


__all__ = [
    "LIKELIHOOD_FLOOR",
    "BeliefState",
    "BeliefUpdate",
    "HypothesisBelief",
    "entropy",
    "normalise",
]

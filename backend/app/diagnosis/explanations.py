"""Rule-based explanation generation (plan.md section 9.5).

The explanation layer is deliberately *derived from structured evidence*: every
sentence it can produce is tied to an observation record that exists in the run.
It never calls a language model, and it refuses to emit a claim whose supporting
observation is absent — :func:`validate_explanation` enforces that.

Two guarantees the tests pin down:

1. **No unsupported claims** — every ``probe_key`` referenced in the explanation
   must appear in the run's observation list, and every hypothesis named as
   supported or weakened must appear in the recorded likelihood ratios.
2. **No proof language** — the text avoids "proves"/"proof"/"definitely" because
   the underlying inference is probabilistic. :data:`FORBIDDEN_CLAIM_WORDS` lists
   the words the validator rejects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.config import MODEL_VERSION, PRIOR_CONFIG_VERSION
from ..probes.base import ProbeObservation
from .hypotheses import Hypothesis, hypothesis_spec
from .planner import Status

#: Words that would overstate a probabilistic conclusion.
FORBIDDEN_CLAIM_WORDS: tuple[str, ...] = (
    "proves",
    "proven",
    "proof that",
    "definitely",
    "certainly",
    "guaranteed",
    "100%",
)


@dataclass
class Contribution:
    """One observation's measured effect on the belief."""

    probe_key: str
    probe_label: str
    outcome: str
    sequence_number: int
    entropy_before: float
    entropy_after: float
    #: posterior/prior per hypothesis for this observation
    likelihood_ratios: dict[str, float]
    supporting: list[str] = field(default_factory=list)
    weakening: list[str] = field(default_factory=list)

    @property
    def information_gained(self) -> float:
        return max(self.entropy_before - self.entropy_after, 0.0)


@dataclass
class DiagnosisExplanation:
    status: Status
    headline: str
    summary: str
    reasoning: list[str]
    supporting_evidence: list[str]
    weakening_evidence: list[str]
    unexplained: list[str]
    uncertainty_note: str
    recommended_next_step: str
    remediation: list[str]
    caveats: list[str]
    contributions: list[Contribution]
    model_version: str = MODEL_VERSION
    prior_config_version: str = PRIOR_CONFIG_VERSION

    def to_public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "headline": self.headline,
            "summary": self.summary,
            "reasoning": list(self.reasoning),
            "supporting_evidence": list(self.supporting_evidence),
            "weakening_evidence": list(self.weakening_evidence),
            "unexplained": list(self.unexplained),
            "uncertainty_note": self.uncertainty_note,
            "recommended_next_step": self.recommended_next_step,
            "remediation": list(self.remediation),
            "caveats": list(self.caveats),
            "contributions": [
                {
                    "probe_key": item.probe_key,
                    "probe_label": item.probe_label,
                    "outcome": item.outcome,
                    "sequence_number": item.sequence_number,
                    "entropy_before_bits": round(item.entropy_before, 6),
                    "entropy_after_bits": round(item.entropy_after, 6),
                    "information_gained_bits": round(item.information_gained, 6),
                    "likelihood_ratios": {
                        key: round(value, 4) for key, value in item.likelihood_ratios.items()
                    },
                    "supporting": list(item.supporting),
                    "weakening": list(item.weakening),
                }
                for item in self.contributions
            ],
            "model_version": self.model_version,
            "prior_config_version": self.prior_config_version,
        }


def build_contributions(
    observations: list[ProbeObservation],
    belief_history: list[Any],
) -> list[Contribution]:
    """Pair each observation with its recorded belief update."""
    out: list[Contribution] = []
    for observation, update in zip(observations, belief_history):
        ratios = {
            code: update.likelihood_contribution(code) for code in update.posteriors
        }
        supporting = sorted(
            (code for code, ratio in ratios.items() if ratio > 1.25),
            key=lambda code: (-ratios[code], code),
        )
        weakening = sorted(
            (code for code, ratio in ratios.items() if ratio < 0.8),
            key=lambda code: (ratios[code], code),
        )
        out.append(
            Contribution(
                probe_key=observation.probe_key,
                probe_label=observation.probe_label,
                outcome=observation.outcome,
                sequence_number=observation.sequence_number,
                entropy_before=update.entropy_before,
                entropy_after=update.entropy_after,
                likelihood_ratios=ratios,
                supporting=supporting,
                weakening=weakening,
            )
        )
    return out


def build_explanation(
    *,
    status: Status,
    observations: list[ProbeObservation],
    belief: dict[str, Any],
    contributions: list[Contribution],
    stopping_reason: str,
    next_probe: dict[str, Any] | None,
    suspected_component: tuple[str, str] | None,
    baseline_comparison: dict[str, Any] | None = None,
) -> DiagnosisExplanation:
    """Assemble the report text strictly from the recorded run."""
    ranked = belief.get("ranked", [])
    leader = ranked[0] if ranked else {"code": Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES.value,
                                       "probability": 0.0, "title": "unknown"}
    try:
        leader_code = Hypothesis(leader["code"])
    except ValueError:  # pragma: no cover - defensive
        leader_code = Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES
    spec = hypothesis_spec(leader_code)
    probability = float(leader.get("probability", 0.0))

    supporting, weakening, unexplained = _evidence_split(
        leader_code, contributions, observations
    )
    reasoning = _reasoning(contributions, leader_code, probability)
    headline, uncertainty_note = _headline(status, leader_code, probability, ranked)
    summary = _summary(status, spec.title, probability, len(observations), stopping_reason)
    next_step = _next_step(status, spec)
    headline = _attach_component(headline, suspected_component, status)
    caveats = [
        (
            "SIMULATED LAB: every observation in this report was produced by the deterministic "
            "virtual lab model. The diagnosis describes the configured model, not a live network."
        ),
        (
            "The engine reasons from protocol-behaviour likelihoods, so the reported confidence "
            "is a relative ranking of the modelled explanations. It is not a physical measurement "
            "of the network."
        ),
        (
            f"Model revision {MODEL_VERSION}; prior configuration {PRIOR_CONFIG_VERSION}. "
            "Ground-truth fault labels are used only by the experiment evaluator and are never "
            "passed to this engine."
        ),
    ]
    if baseline_comparison:
        caveats.append(
            "The baseline comparison executed the same probes, priors and stopping rule with a "
            "fixed order; only probe selection differed."
        )

    explanation = DiagnosisExplanation(
        status=status,
        headline=headline,
        summary=summary,
        reasoning=reasoning,
        supporting_evidence=supporting,
        weakening_evidence=weakening,
        unexplained=unexplained,
        uncertainty_note=uncertainty_note,
        recommended_next_step=next_step,
        remediation=list(spec.remediation),
        caveats=caveats,
        contributions=contributions,
    )
    validate_explanation(explanation, observations)
    return explanation


def _evidence_split(
    leader_code: Hypothesis,
    contributions: list[Contribution],
    observations: list[ProbeObservation],
) -> tuple[list[str], list[str], list[str]]:
    supporting: list[str] = []
    weakening: list[str] = []
    for contribution in contributions:
        if leader_code.value in contribution.supporting:
            supporting.append(
                f"{contribution.probe_label} returned {contribution.outcome}, which raised "
                f"{leader_code.value} by a factor of "
                f"{contribution.likelihood_ratios[leader_code.value]:.2f} "
                f"(uncertainty {contribution.entropy_before:.2f} -> "
                f"{contribution.entropy_after:.2f} bits)."
            )
        if leader_code.value in contribution.weakening:
            weakening.append(
                f"{contribution.probe_label} returned {contribution.outcome}, which lowered "
                f"{leader_code.value} by a factor of "
                f"{contribution.likelihood_ratios[leader_code.value]:.2f}."
            )
    unexplained: list[str] = []
    if not supporting:
        unexplained.append(
            "No executed probe produced evidence that specifically raises the leading "
            f"hypothesis ({leader_code.value}); it leads on the balance of evidence rather than "
            "on a positive observation."
        )
    top_alternatives = [
        code.value
        for code in (Hypothesis.LINK_FAILURE, Hypothesis.ROUTING_FAILURE)
        if code is not leader_code
    ]
    if leader_code is not Hypothesis.NO_FAULT_DETECTED and any(
        observation.details.get("block_reason") not in (None, "NONE")
        for observation in observations
    ) and leader_code.value not in top_alternatives:
        unexplained.append(
            "A forwarding-layer block was reported at some point in the run, which the leading "
            "hypothesis does not explain; keep a forwarding fault among the alternatives."
        )
    return supporting, weakening, unexplained


def _reasoning(
    contributions: list[Contribution], leader_code: Hypothesis, probability: float
) -> list[str]:
    if not contributions:
        return ["No probe has been executed yet, so no evidence has been collected."]
    ordered = sorted(contributions, key=lambda item: (-item.information_gained, item.sequence_number))
    lines = [
        "Evidence was collected in this order and changed the belief as follows:"
    ]
    for contribution in sorted(contributions, key=lambda item: item.sequence_number):
        lines.append(
            f"[{contribution.sequence_number}] {contribution.probe_label} -> "
            f"{contribution.outcome}: information gained "
            f"{contribution.information_gained:.3f} bits, raising "
            f"{_name_list(contribution.supporting)} and weakening "
            f"{_name_list(contribution.weakening)}."
        )
    most = ordered[0]
    lines.append(
        f"The single most informative test was {most.probe_label} "
        f"({most.information_gained:.3f} bits), which is why the adaptive planner ranks probes by "
        "expected entropy reduction rather than running a fixed checklist."
    )
    if len(contributions) > 1:
        first = min(contributions, key=lambda item: item.sequence_number)
        lines.append(
            f"The first test executed was {first.probe_label}; with a different result the "
            "planner would have selected a different second test, which is the behaviour the "
            "baseline cannot reproduce."
        )
    return lines


def _name_list(codes: list[str]) -> str:
    if not codes:
        return "no hypothesis materially"
    selected = codes[:2]
    return " and ".join(selected) + (" (among others)" if len(codes) > 2 else "")


def _headline(
    status: Status,
    leader_code: Hypothesis,
    probability: float,
    ranked: list[dict[str, Any]],
) -> tuple[str, str]:
    runner = ranked[1] if len(ranked) > 1 else None
    if status == "confident":
        return (
            f"Most likely cause: {leader_code.value} ({probability:.0%} confidence)",
            (
                f"The leading hypothesis is separated from the next candidate"
                + (
                    f" ({runner['code']} at {float(runner['probability']):.0%})"
                    if runner
                    else ""
                )
                + " by more than the configured margin, so the engine asserts it. The "
                "probabilistic nature of the inference still applies."
            ),
        )
    if status == "budget_exhausted":
        return (
            f"Unresolved (probe budget exhausted); best candidate "
            f"{leader_code.value} at {probability:.0%}",
            (
                "The probe budget ran out before the confidence threshold was met. Treat the "
                "leading candidate as a hypothesis to verify, not as a confirmed cause."
            ),
        )
    if status == "error":
        return (
            "Diagnosis aborted",
            "The run could not continue, so no conclusion is reported for the collected evidence.",
        )
    return (
        f"Inconclusive: {leader_code.value} leads at {probability:.0%} but is not separated",
        (
            "More than one explanation remains plausible under the current evidence, so the "
            "engine reports the ambiguity instead of forcing a single answer."
        ),
    )


def _attach_component(
    headline: str, component: tuple[str, str] | None, status: Status
) -> str:
    if component is None or status != "confident":
        return headline
    kind, component_id = component
    localized = f"localized to {kind} {component_id}"
    if component_id in headline:
        return headline
    return f"{headline} — {localized}"


def _summary(
    status: Status,
    title: str,
    probability: float,
    probe_count: int,
    stopping_reason: str,
) -> str:
    prefix = {
        "confident": "The diagnosis is conclusive under the configured stopping rule.",
        "inconclusive": "The diagnosis is inconclusive.",
        "budget_exhausted": "The diagnosis stopped on the probe budget without a conclusion.",
        "error": "The diagnosis stopped on an error.",
        "running": "The diagnosis is still running.",
    }[status]
    return (
        f"{prefix} Leading explanation: {title}, posterior {probability:.1%}, after "
        f"{probe_count} probe(s). {stopping_reason}"
    )


def _next_step(status: Status, spec: Any) -> str:
    if status == "confident":
        return (
            f"Verify before acting: {spec.verification_steps[0]}"
            if spec.verification_steps
            else "Verify the finding with an independent check before acting."
        )
    if status == "inconclusive":
        return (
            "Collect a different class of evidence, for example a packet capture on the "
            "suspected segment or the forwarding table of the last responding hop. Repeating the "
            "same probes cannot resolve the remaining ambiguity."
        )
    if status == "budget_exhausted":
        return (
            "Raise the probe budget or narrow the target (for example a single service and port) "
            "so the engine can spend its probes on the evidence that actually separates the "
            "remaining candidates."
        )
    return "Re-run the diagnosis."


def validate_explanation(
    explanation: DiagnosisExplanation, observations: list[ProbeObservation]
) -> None:
    """Assert the explanation only makes claims the run supports.

    Raises ``AssertionError`` (used by the test suite) when:

    * a probe label referenced in the text has no corresponding observation,
    * the text contains proof language from :data:`FORBIDDEN_CLAIM_WORDS`.
    """
    observed_labels = {observation.probe_label for observation in observations}
    text_blocks = list(explanation.reasoning) + list(explanation.supporting_evidence)
    text_blocks += list(explanation.weakening_evidence) + [explanation.summary, explanation.headline]

    for block in text_blocks:
        lowered = block.lower()
        for word in FORBIDDEN_CLAIM_WORDS:
            if word in lowered:
                raise AssertionError(
                    f"explanation used proof language {word!r} for a probabilistic conclusion: "
                    f"{block!r}"
                )

    for block in explanation.supporting_evidence:
        if not any(label in block for label in observed_labels):
            raise AssertionError(
                f"explanation cites evidence that no observation produced: {block!r}"
            )

    if explanation.status != "running" and not observations:
        raise AssertionError(
            "a terminal explanation must reference at least one executed probe"
        )


__all__ = [
    "FORBIDDEN_CLAIM_WORDS",
    "Contribution",
    "DiagnosisExplanation",
    "build_contributions",
    "build_explanation",
    "validate_explanation",
]

"""Unit tests for the Bayesian belief updater (plan.md section 9.2 / 17.1)."""

from __future__ import annotations

import math

import pytest

from app.core.errors import ValidationError
from app.diagnosis.bayes import (
    LIKELIHOOD_FLOOR,
    BeliefState,
    entropy,
    normalise,
)
from app.diagnosis.hypotheses import DEFAULT_PRIORS, Hypothesis


def uniform() -> dict[Hypothesis, float]:
    total = len(Hypothesis)
    return {code: 1.0 / total for code in Hypothesis}


class TestEntropy:
    def test_uniform_distribution_has_maximum_entropy(self) -> None:
        expected = math.log2(len(Hypothesis))
        assert entropy(uniform()) == pytest.approx(expected, abs=1e-9)

    def test_certain_distribution_has_zero_entropy(self) -> None:
        beliefs = {code: 0.0 for code in Hypothesis}
        beliefs[Hypothesis.DNS_FAILURE] = 1.0
        assert entropy(beliefs) == pytest.approx(0.0, abs=1e-12)

    def test_all_zero_input_returns_zero(self) -> None:
        assert entropy({code: 0.0 for code in Hypothesis}) == 0.0

    def test_empty_input_returns_zero(self) -> None:
        assert entropy({}) == 0.0

    def test_negative_probability_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-negative"):
            entropy({Hypothesis.DNS_FAILURE: -0.5, Hypothesis.PACKET_LOSS: 1.5})

    def test_non_finite_probability_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="finite"):
            entropy({Hypothesis.DNS_FAILURE: float("inf")})

    def test_entropy_is_scale_invariant(self) -> None:
        base = uniform()
        scaled = {code: value * 7.5 for code, value in base.items()}
        assert entropy(base) == pytest.approx(entropy(scaled))

    def test_entropy_drops_when_one_hypothesis_dominates(self) -> None:
        skewed = uniform()
        skewed[Hypothesis.DNS_FAILURE] = 0.7
        assert entropy(skewed) < entropy(uniform())

    def test_two_hypotheses_split_evenly_gives_one_bit(self) -> None:
        beliefs = {code: 0.0 for code in Hypothesis}
        beliefs[Hypothesis.DNS_FAILURE] = 0.5
        beliefs[Hypothesis.PACKET_LOSS] = 0.5
        assert entropy(beliefs) == pytest.approx(1.0)


class TestNormalise:
    def test_sums_to_one(self) -> None:
        result = normalise({code: 3.0 for code in Hypothesis})
        assert sum(result.values()) == pytest.approx(1.0)

    def test_preserves_ratios(self) -> None:
        result = normalise({Hypothesis.DNS_FAILURE: 3.0, Hypothesis.PACKET_LOSS: 1.0})
        assert result[Hypothesis.DNS_FAILURE] / result[Hypothesis.PACKET_LOSS] == pytest.approx(3.0)

    def test_all_zero_falls_back_to_uniform(self) -> None:
        result = normalise({code: 0.0 for code in Hypothesis})
        assert sum(result.values()) == pytest.approx(1.0)
        assert all(value == pytest.approx(1.0 / len(Hypothesis)) for value in result.values())


class TestBeliefState:
    def test_priors_are_uniform_and_documented(self) -> None:
        state = BeliefState()
        assert all(value == pytest.approx(1.0 / len(Hypothesis)) for value in state.priors.values())
        assert set(state.priors) == set(DEFAULT_PRIORS)

    def test_initial_belief_sums_to_one(self) -> None:
        assert sum(BeliefState().probabilities.values()) == pytest.approx(1.0)

    def test_posterior_always_sums_to_one(self) -> None:
        state = BeliefState()
        observations = [
            ("TCP_CONNECT", "CONNECTED"),
            ("DNS_LOOKUP", "TIMEOUT_RESOLVER"),
            ("ICMP_REACHABILITY:destination", "REACHABLE"),
            ("SERVICE_HEALTH", "HEALTHY"),
        ]
        for index, (probe_key, outcome) in enumerate(observations, start=1):
            state.observe(probe_key=probe_key, outcome=outcome, sequence_number=index)
            assert sum(state.probabilities.values()) == pytest.approx(1.0), (
                f"posterior stopped summing to 1 after {probe_key}:{outcome}"
            )

    def test_all_probabilities_stay_between_zero_and_one(self) -> None:
        state = BeliefState()
        for index, (probe_key, outcome) in enumerate(
            [
                ("MTU_PROBE", "LIMITED_DROP"),
                ("MTU_PROBE", "LIMITED_DROP"),
                ("DNS_LOOKUP", "RESOLVED"),
                ("TRACEROUTE", "NO_FIRST_HOP"),
                ("SERVICE_HEALTH", "UNREACHABLE"),
            ],
            start=1,
        ):
            state.observe(probe_key=probe_key, outcome=outcome, sequence_number=index)
            for value in state.probabilities.values():
                assert 0.0 <= value <= 1.0
                assert math.isfinite(value)

    def test_supporting_evidence_raises_the_ranking(self) -> None:
        state = BeliefState()
        before = state.probability(Hypothesis.DNS_FAILURE)
        state.observe(probe_key="DNS_LOOKUP", outcome="TIMEOUT_RESOLVER", sequence_number=1)
        assert state.probability(Hypothesis.DNS_FAILURE) > before
        assert state.leader().code is Hypothesis.DNS_FAILURE

    def test_evidence_against_an_mtu_fault_lowers_it(self) -> None:
        state = BeliefState()
        state.observe(probe_key="MTU_PROBE", outcome="LIMITED_DROP", sequence_number=1)
        high = state.probability(Hypothesis.MTU_BLACK_HOLE)
        state.observe(probe_key="MTU_PROBE", outcome="FULL_PATH_OK", sequence_number=2)
        assert state.probability(Hypothesis.MTU_BLACK_HOLE) < high

    def test_conflicting_evidence_leaves_a_contested_belief(self) -> None:
        state = BeliefState()
        state.observe(probe_key="ICMP_REACHABILITY:destination", outcome="REACHABLE",
                      sequence_number=1)
        state.observe(probe_key="TRACEROUTE", outcome="PARTIAL", sequence_number=2)
        assert state.entropy > 0.5, "contradictory evidence must not collapse to certainty"
        assert state.leader().probability < 0.9

    def test_zero_likelihoods_do_not_collapse_the_belief(self) -> None:
        state = BeliefState()
        # A healthy observation makes the link/route hypotheses extremely unlikely;
        # the floor must keep them non-zero and the distribution valid.
        for index, outcome in enumerate(["REACHABLE", "REACHABLE", "REACHABLE"], start=1):
            state.observe(probe_key="ICMP_REACHABILITY:destination", outcome=outcome,
                          sequence_number=index)
        assert state.probability(Hypothesis.LINK_FAILURE) > 0.0
        assert sum(state.probabilities.values()) == pytest.approx(1.0)

    def test_floor_is_applied_and_recorded(self) -> None:
        state = BeliefState()
        update = state.observe(
            probe_key="TCP_CONNECT", outcome="CONNECTED", sequence_number=1
        )
        assert all(
            value >= LIKELIHOOD_FLOOR - 1e-12 for value in update.floored_likelihoods.values()
        )
        assert update.raw_likelihoods, "the raw likelihoods must be recorded too"

    def test_update_records_everything_needed_to_explain_it(self) -> None:
        state = BeliefState()
        update = state.observe(
            probe_key="DNS_LOOKUP", outcome="NXDOMAIN", sequence_number=4
        )
        assert update.observation_key == "DNS_LOOKUP|NXDOMAIN"
        assert update.probe_key == "DNS_LOOKUP"
        assert update.outcome == "NXDOMAIN"
        assert update.sequence_number == 4
        assert set(update.priors) == {code.value for code in Hypothesis}
        assert set(update.posteriors) == {code.value for code in Hypothesis}
        assert update.entropy_before >= update.entropy_after
        assert update.likelihood_contribution(Hypothesis.DNS_FAILURE) > 1.0

    def test_public_payload_is_serialisable_and_ranked(self) -> None:
        import json

        state = BeliefState()
        state.observe(probe_key="DNS_LOOKUP", outcome="NXDOMAIN", sequence_number=1)
        payload = state.to_public()
        assert json.dumps(payload)
        probabilities = [item["probability"] for item in payload["ranked"]]
        assert probabilities == sorted(probabilities, reverse=True)
        assert payload["leader"]["code"] == "DNS_FAILURE"
        assert 0.0 <= payload["entropy_bits"] <= math.log2(len(Hypothesis))

    def test_ranking_tie_breaks_deterministically(self) -> None:
        first = BeliefState().ranked()
        second = BeliefState().ranked()
        assert [item.code for item in first] == [item.code for item in second]
        assert [item.code.value for item in first] == sorted(
            item.code.value for item in first
        ), "with a uniform prior, ranking must fall back to the stable code order"

    def test_runner_up_is_the_second_ranked(self) -> None:
        state = BeliefState()
        ranked = state.ranked()
        assert state.runner_up().code is ranked[1].code

    def test_evidence_weight_softens_the_update(self) -> None:
        hard = BeliefState()
        hard.observe(probe_key="DNS_LOOKUP", outcome="TIMEOUT_RESOLVER", sequence_number=1)
        soft = BeliefState()
        soft.observe(
            probe_key="DNS_LOOKUP", outcome="TIMEOUT_RESOLVER", sequence_number=1,
            evidence_weight=0.5,
        )
        assert soft.probability(Hypothesis.DNS_FAILURE) < hard.probability(
            Hypothesis.DNS_FAILURE
        )
        assert soft.probability(Hypothesis.DNS_FAILURE) > DEFAULT_PRIORS[
            Hypothesis.DNS_FAILURE
        ]

    def test_zero_evidence_weight_is_a_no_op(self) -> None:
        state = BeliefState()
        before = state.probabilities
        state.observe(
            probe_key="DNS_LOOKUP", outcome="TIMEOUT_RESOLVER", sequence_number=1,
            evidence_weight=0.0,
        )
        for code, value in before.items():
            assert state.probability(code) == pytest.approx(value, abs=1e-9)

    def test_unknown_probe_key_raises(self) -> None:
        with pytest.raises(ValidationError, match="no entry for probe"):
            BeliefState().observe(probe_key="NOT_A_PROBE", outcome="X")

    def test_unknown_outcome_raises(self) -> None:
        with pytest.raises(ValidationError, match="no entry for"):
            BeliefState().observe(probe_key="DNS_LOOKUP", outcome="MADE_UP")

    def test_custom_priors_are_honoured_and_normalised(self) -> None:
        state = BeliefState({code: 1.0 for code in Hypothesis})
        assert sum(state.priors.values()) == pytest.approx(1.0)

    def test_reset_restores_the_prior(self) -> None:
        state = BeliefState()
        state.observe(probe_key="DNS_LOOKUP", outcome="NXDOMAIN", sequence_number=1)
        state.reset()
        assert state.probability(Hypothesis.DNS_FAILURE) == pytest.approx(
            DEFAULT_PRIORS[Hypothesis.DNS_FAILURE]
        )
        assert state.history == []

    def test_history_is_append_only_and_ordered(self) -> None:
        state = BeliefState()
        state.observe(probe_key="DNS_LOOKUP", outcome="RESOLVED", sequence_number=1)
        state.observe(probe_key="TCP_CONNECT", outcome="CONNECTED", sequence_number=2)
        assert [item.sequence_number for item in state.history] == [1, 2]

    def test_long_observation_sequence_stays_numerically_stable(self) -> None:
        state = BeliefState()
        sequence = [
            ("ICMP_REACHABILITY:destination", "REACHABLE"),
            ("DNS_LOOKUP", "TIMEOUT_RESOLVER"),
            ("TCP_CONNECT", "CONNECTED"),
            ("TRACEROUTE", "PARTIAL"),
            ("MTU_PROBE", "FULL_PATH_OK"),
            ("SERVICE_HEALTH", "HEALTHY"),
            ("ICMP_REACHABILITY:gateway", "REACHABLE"),
            ("ICMP_REACHABILITY:resolver", "REACHABLE"),
            ("ICMP_REACHABILITY:control_destination", "REACHABLE"),
        ]
        for index, (probe_key, outcome) in enumerate(sequence, start=1):
            state.observe(probe_key=probe_key, outcome=outcome, sequence_number=index)
            assert sum(state.probabilities.values()) == pytest.approx(1.0)
            assert all(math.isfinite(value) for value in state.probabilities.values())
        assert state.entropy >= 0.0

    def test_entropy_is_monotone_for_repeated_consistent_evidence(self) -> None:
        state = BeliefState()
        entropies = []
        for index in range(3):
            state.observe(
                probe_key=["DNS_LOOKUP", "ICMP_REACHABILITY:resolver", "TCP_CONNECT"][index],
                outcome=["TIMEOUT_RESOLVER", "REACHABLE", "CONNECTED"][index],
                sequence_number=index + 1,
            )
            entropies.append(state.entropy)
        assert entropies[0] <= math.log2(len(Hypothesis))
        assert entropies[-1] < entropies[0]

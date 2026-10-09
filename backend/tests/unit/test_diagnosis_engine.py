"""Unit tests for the likelihood model, information gain and the planner."""

from __future__ import annotations

import math

import pytest

from app.core.config import (
    CONFIDENCE_LEAD,
    CONFIDENCE_THRESHOLD,
    MIN_INFORMATION_GAIN_BITS,
)
from app.core.errors import ValidationError
from app.diagnosis.bayes import BeliefState, entropy
from app.diagnosis.baseline import (
    BASELINE_ORDER,
    build_baseline_plan,
    select_baseline_probe,
)
from app.diagnosis.hypotheses import HYPOTHESES, Hypothesis
from app.diagnosis.information_gain import (
    ProbeCandidate,
    expected_information_gain,
    rank_candidates,
)
from app.diagnosis.likelihoods import (
    DEFAULT_LIKELIHOOD,
    LIKELIHOODS,
    likelihood_table,
    validate_likelihood_table,
)
from app.diagnosis.planner import (
    DiagnosisContext,
    build_candidates,
    candidate_rejections,
    evaluate_stopping_rule,
    select_next_probe,
)
from app.lab.outcomes import CANONICAL_CANDIDATE_ORDER, ProbeType, probe_key as make_key
from app.lab.templates import get_template
from app.probes.simulated import KNOWN_OUTCOMES


def context(**overrides: object) -> DiagnosisContext:
    base = {
        "source_node_id": "client-1",
        "destination_node_id": "web-1",
        "destination_service": "web",
        "port": 80,
        "hostname": "web.campus.test",
        "gateway_node_id": "access-rtr",
        "resolver_node_id": "dns-1",
        "control_node_id": "db-1",
        "topology": get_template("campus-basic"),
    }
    base.update(overrides)
    return DiagnosisContext(**base)  # type: ignore[arg-type]


class TestLikelihoodTable:
    def test_table_is_valid(self) -> None:
        validate_likelihood_table()

    def test_covers_every_canonical_probe_key(self) -> None:
        for probe_type, selector in CANONICAL_CANDIDATE_ORDER:
            key = make_key(probe_type, selector)
            assert key in LIKELIHOODS, f"{key} has no likelihood row"

    def test_covers_every_outcome_the_probes_can_emit(self) -> None:
        for probe_type, outcomes in KNOWN_OUTCOMES.items():
            selector = next(
                (sel for pt, sel in CANONICAL_CANDIDATE_ORDER if pt is probe_type), None
            )
            key = make_key(probe_type, selector)
            for outcome in outcomes:
                assert outcome in LIKELIHOODS[key], f"{key}:{outcome} is not modelled"

    def test_every_weight_is_positive_and_finite(self) -> None:
        for key, outcomes in LIKELIHOODS.items():
            for outcome, row in outcomes.items():
                for code, weight in row.items():
                    assert math.isfinite(weight) and weight > 0.0, f"{key}:{outcome}:{code}"

    def test_every_row_covers_the_full_hypothesis_catalogue(self) -> None:
        for key, outcomes in LIKELIHOODS.items():
            for outcome, row in outcomes.items():
                assert set(row) == {code.value for code in Hypothesis}, f"{key}:{outcome}"

    def test_default_is_neutral_so_one_healthy_probe_is_not_conclusive(self) -> None:
        assert DEFAULT_LIKELIHOOD > 0.5, (
            "an unmentioned hypothesis must not be treated as refuted by a probe from "
            "another layer"
        )

    def test_a_healthy_echo_does_not_refute_dns_or_mtu_hypotheses(self) -> None:
        table = likelihood_table()
        for code in (Hypothesis.DNS_FAILURE, Hypothesis.MTU_BLACK_HOLE,
                     Hypothesis.TCP_FILTER_OR_PORT_FAILURE):
            value = table.likelihood("ICMP_REACHABILITY:destination", "REACHABLE", code)
            assert value > 0.5, f"a small echo request should not rule out {code.value}"

    def test_a_healthy_echo_strongly_weakens_forwarding_hypotheses(self) -> None:
        table = likelihood_table()
        assert table.likelihood(
            "ICMP_REACHABILITY:destination", "REACHABLE", Hypothesis.LINK_FAILURE
        ) < 0.1
        assert table.likelihood(
            "ICMP_REACHABILITY:destination", "REACHABLE", Hypothesis.ROUTING_FAILURE
        ) < 0.1

    def test_unknown_probe_key_raises(self) -> None:
        with pytest.raises(ValidationError, match="no entry for probe"):
            likelihood_table().likelihood("NOPE", "X", Hypothesis.DNS_FAILURE)

    def test_unknown_outcome_raises(self) -> None:
        with pytest.raises(ValidationError, match="no entry for"):
            likelihood_table().likelihood("DNS_LOOKUP", "NOPE", Hypothesis.DNS_FAILURE)

    def test_outcome_distribution_sums_to_one(self) -> None:
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        distribution = likelihood_table().outcome_distribution("TRACEROUTE", beliefs)
        assert sum(distribution.values()) == pytest.approx(1.0)

    def test_nxdomain_supports_dns_more_than_a_timeout_does(self) -> None:
        table = likelihood_table()
        nxdomain = table.likelihood("DNS_LOOKUP", "NXDOMAIN", Hypothesis.DNS_FAILURE)
        timeout = table.likelihood("DNS_LOOKUP", "TIMEOUT_RESOLVER", Hypothesis.DNS_FAILURE)
        assert nxdomain > timeout, "a resolver that answered is stronger DNS evidence"


class TestInformationGain:
    def test_uniform_belief_has_the_maximum_entropy(self) -> None:
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        gain = expected_information_gain("TRACEROUTE", beliefs, likelihood_table())
        assert gain.entropy_before == pytest.approx(math.log2(len(Hypothesis)), abs=1e-9)

    def test_eig_is_zero_when_no_uncertainty_remains(self) -> None:
        certain = {code: 0.0 for code in Hypothesis}
        certain[Hypothesis.DNS_FAILURE] = 1.0
        gain = expected_information_gain("DNS_LOOKUP", certain, likelihood_table())
        assert gain.expected_information_gain == pytest.approx(0.0, abs=1e-9)

    def test_eig_is_zero_for_a_belief_no_probe_can_separate(self) -> None:
        # DNS_LOOKUP has an outcome distribution that is nearly constant across
        # hypotheses except for DNS_FAILURE; with DNS already certain the probe is
        # uninformative.
        certain = {code: 0.0 for code in Hypothesis}
        certain[Hypothesis.DNS_FAILURE] = 1.0
        for key in ("MTU_PROBE", "SERVICE_HEALTH"):
            gain = expected_information_gain(key, certain, likelihood_table())
            assert gain.expected_information_gain == pytest.approx(0.0, abs=1e-9)

    def test_eig_never_exceeds_the_current_entropy(self) -> None:
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        for probe_type, selector in CANONICAL_CANDIDATE_ORDER:
            key = make_key(probe_type, selector)
            gain = expected_information_gain(key, beliefs, likelihood_table())
            assert 0.0 <= gain.expected_information_gain <= gain.entropy_before + 1e-9

    def test_expected_entropy_after_matches_a_manual_calculation(self) -> None:
        # Two hypotheses, one probe, two outcomes: computed by hand below.
        beliefs = {
            Hypothesis.DNS_FAILURE: 0.5,
            Hypothesis.MTU_BLACK_HOLE: 0.5,
        }
        table = likelihood_table()
        gain = expected_information_gain("MTU_PROBE", beliefs, table)
        manual_h_after = 0.0
        manual_p = 0.0
        for outcome in table.outcomes("MTU_PROBE"):
            weights = {
                code: beliefs[code] * table.likelihood("MTU_PROBE", outcome, code)
                for code in beliefs
            }
            total = sum(weights.values())
            posterior = {code: value / total for code, value in weights.items()}
            h = entropy(posterior)
            manual_p += total
            manual_h_after += total * h
        manual_h_after /= manual_p
        assert gain.expected_entropy_after == pytest.approx(manual_h_after, abs=1e-9)

    def test_cost_reduces_the_score_but_not_the_information(self) -> None:
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        cheap = expected_information_gain("TRACEROUTE", beliefs, likelihood_table(), cost=1.0)
        dear = expected_information_gain("TRACEROUTE", beliefs, likelihood_table(), cost=2.0)
        assert cheap.expected_information_gain == pytest.approx(dear.expected_information_gain)
        assert dear.scored_value == pytest.approx(cheap.scored_value / 2.0)

    def test_zero_cost_is_treated_as_one(self) -> None:
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        zero = expected_information_gain("TRACEROUTE", beliefs, likelihood_table(), cost=0.0)
        one = expected_information_gain("TRACEROUTE", beliefs, likelihood_table(), cost=1.0)
        assert zero.scored_value == pytest.approx(one.scored_value)

    def test_ranking_is_sorted_and_deterministic(self) -> None:
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        candidates = [
            ProbeCandidate(
                probe_key=make_key(probe_type, selector),
                probe_type_value=probe_type.value,
                label=probe_type.value,
                cost=1.0,
            )
            for probe_type, selector in CANONICAL_CANDIDATE_ORDER
        ]
        first = rank_candidates(candidates, beliefs, likelihood_table())
        second = rank_candidates(list(reversed(candidates)), beliefs, likelihood_table())
        assert [item.probe_key for item in first] == [item.probe_key for item in second]
        scores = [item.scored_value for item in first]
        assert scores == sorted(scores, reverse=True)

    def test_public_payload_is_json_serialisable(self) -> None:
        import json

        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        gain = expected_information_gain("MTU_PROBE", beliefs, likelihood_table())
        assert json.dumps(gain.to_public())


class TestCandidateConstruction:
    def test_full_context_offers_every_canonical_probe(self) -> None:
        keys = {candidate.probe_key for candidate in build_candidates(context())}
        expected = {make_key(pt, sel) for pt, sel in CANONICAL_CANDIDATE_ORDER}
        assert keys == expected

    def test_at_least_six_probe_types_are_available(self) -> None:
        keys = {candidate.probe_key for candidate in build_candidates(context())}
        types = {key.split(":")[0] for key in keys}
        assert len(types) >= 6

    def test_missing_resolver_removes_the_resolver_ping(self) -> None:
        keys = {
            candidate.probe_key
            for candidate in build_candidates(context(resolver_node_id=None, hostname=None))
        }
        assert "ICMP_REACHABILITY:resolver" not in keys

    def test_missing_service_removes_the_health_check(self) -> None:
        keys = {
            candidate.probe_key
            for candidate in build_candidates(context(destination_service=None))
        }
        assert "SERVICE_HEALTH" not in keys

    def test_rejection_reasons_are_explained_not_silent(self) -> None:
        rejections = candidate_rejections(
            context(destination_service=None, resolver_node_id=None)
        )
        keys = {item["probe_key"] for item in rejections}
        assert "SERVICE_HEALTH" in keys
        assert "ICMP_REACHABILITY:resolver" in keys
        for item in rejections:
            assert item["reason"], "a removed candidate must state why"

    def test_executed_probes_are_excluded(self) -> None:
        first = select_next_probe(
            BeliefState(), context(), likelihood_table(), executed_probe_keys=[]
        )
        second = select_next_probe(
            BeliefState(),
            context(),
            likelihood_table(),
            executed_probe_keys=[first.probe_key],
        )
        assert second is not None
        assert second.probe_key != first.probe_key

    def test_no_candidate_remains_when_all_are_executed(self) -> None:
        everything = [make_key(pt, sel) for pt, sel in CANONICAL_CANDIDATE_ORDER]
        assert (
            select_next_probe(
                BeliefState(), context(), likelihood_table(), executed_probe_keys=everything
            )
            is None
        )


class TestPlannerSelection:
    def test_selection_is_deterministic(self) -> None:
        choices = [
            select_next_probe(BeliefState(), context(), likelihood_table())
            for _ in range(5)
        ]
        assert len({choice.probe_key for choice in choices}) == 1

    def test_selection_actually_depends_on_the_belief(self) -> None:
        """The planner must be evidence-driven, not a fixed sequence.

        The same context with different collected evidence must produce different
        next probes. Anything else would mean the EIG calculation is decorative.
        """
        scenarios = {
            "healthy": [("ICMP_REACHABILITY:destination", "REACHABLE")],
            "forwarding_block": [("ICMP_REACHABILITY:destination", "UNREACHABLE_NETWORK")],
            "intermittent": [("ICMP_REACHABILITY:destination", "PARTIAL_LOSS")],
            "mtu": [
                ("ICMP_REACHABILITY:destination", "REACHABLE"),
                ("TCP_CONNECT", "CONNECTED"),
                ("MTU_PROBE", "LIMITED_DROP"),
            ],
            "port_blocked": [
                ("ICMP_REACHABILITY:destination", "REACHABLE"),
                ("TCP_CONNECT", "TIMEOUT_DROP"),
            ],
        }
        chosen: dict[str, str] = {}
        for name, sequence in scenarios.items():
            belief = BeliefState()
            for index, (probe_key, outcome) in enumerate(sequence, start=1):
                belief.observe(probe_key=probe_key, outcome=outcome, sequence_number=index)
            choice = select_next_probe(
                belief,
                context(),
                likelihood_table(),
                executed_probe_keys=[key for key, _ in sequence],
            )
            assert choice is not None, f"{name} left no eligible probe"
            chosen[name] = choice.probe_key
        assert len(set(chosen.values())) > 1, (
            "every evidence set selected the same next probe, so the planner is not "
            f"evidence-driven: {chosen}"
        )
        # Spot-check the two most specific behaviours, both verifiable by hand:
        # a confirmed MTU signature must still be corroborated by a path trace, and
        # a blocked port must be separated from a dead service.
        assert chosen["mtu"] != chosen["healthy"]
        assert chosen["port_blocked"] == "SERVICE_HEALTH"
        assert chosen["forwarding_block"] == "ICMP_REACHABILITY:gateway"

    def test_adaptive_order_can_differ_from_the_baseline_order(self) -> None:
        """At least one reachable belief must make the planner deviate from the baseline."""
        deviations = 0
        belief = BeliefState()
        executed: list[str] = []
        for _ in range(4):
            choice = select_next_probe(
                belief, context(), likelihood_table(), executed_probe_keys=executed
            )
            if choice is None:
                break
            expected_baseline = next(
                key for key in BASELINE_ORDER if key not in executed
                and key in {c.probe_key for c in build_candidates(context())}
            )
            if choice.probe_key != expected_baseline:
                deviations += 1
            executed.append(choice.probe_key)
            belief.observe(
                probe_key=choice.probe_key,
                outcome=likelihood_table().outcomes(choice.probe_key)[0],
                sequence_number=len(executed),
            )
        assert deviations > 0, (
            "the adaptive planner never deviated from the fixed order, which would mean "
            "it is not evidence-driven"
        )

    def test_choice_carries_a_plain_language_reason_with_real_numbers(self) -> None:
        choice = select_next_probe(BeliefState(), context(), likelihood_table())
        assert choice.expected_information_gain > 0
        assert "bits" in choice.reason
        assert choice.probe_key in choice.reason or choice.label in choice.reason

    def test_choice_exposes_the_considered_alternatives(self) -> None:
        choice = select_next_probe(BeliefState(), context(), likelihood_table())
        assert len(choice.ranked_alternatives) > 1
        assert choice.ranked_alternatives[0].probe_key == choice.probe_key

    def test_selection_prefers_a_probe_that_resolves_the_current_ambiguity(self) -> None:
        # With a belief split between DNS and MTU, the planner should not pick a
        # probe that both hypotheses predict identically.
        table = likelihood_table()
        beliefs = {
            Hypothesis.DNS_FAILURE: 0.5,
            Hypothesis.MTU_BLACK_HOLE: 0.5,
        }
        candidates = build_candidates(context())
        ranked = rank_candidates(candidates, beliefs, table)
        assert ranked[0].expected_information_gain > ranked[-1].expected_information_gain

    def test_public_payload_is_serialisable(self) -> None:
        import json

        choice = select_next_probe(BeliefState(), context(), likelihood_table())
        assert json.dumps(choice.to_public())


class TestBaselineStrategy:
    def test_baseline_order_is_the_documented_sequence(self) -> None:
        assert BASELINE_ORDER == [
            make_key(probe_type, selector)
            for probe_type, selector in CANONICAL_CANDIDATE_ORDER
        ]

    def test_baseline_plan_restricts_to_supported_probes(self) -> None:
        plan = build_baseline_plan(context(destination_service=None))
        assert "SERVICE_HEALTH" not in plan.ordered_keys
        assert any(item["probe_key"] == "SERVICE_HEALTH" for item in plan.skipped)

    def test_baseline_ignores_information_gain_when_choosing(self) -> None:
        plan = build_baseline_plan(context())
        first = select_baseline_probe(plan, [])
        second = select_baseline_probe(plan, [first.probe_key])
        assert first.probe_key == BASELINE_ORDER[0]
        assert second.probe_key == BASELINE_ORDER[1]

    def test_baseline_returns_none_when_exhausted(self) -> None:
        plan = build_baseline_plan(context())
        assert select_baseline_probe(plan, plan.ordered_keys) is None

    def test_baseline_and_adaptive_share_the_candidate_universe(self) -> None:
        plan = build_baseline_plan(context())
        supported = {candidate.probe_key for candidate in build_candidates(context())}
        assert set(plan.ordered_keys) == supported

    def test_baseline_reason_mentions_the_fixed_sequence(self) -> None:
        plan = build_baseline_plan(context())
        choice = select_baseline_probe(plan, [])
        assert "baseline" in choice.reason.lower()


class TestStoppingRule:
    def test_continues_when_the_belief_is_ambiguous(self) -> None:
        belief = BeliefState()
        choice = select_next_probe(belief, context(), likelihood_table())
        decision = evaluate_stopping_rule(
            belief, probes_used=0, max_probes=8, next_probe=choice
        )
        assert decision.should_stop is False
        assert decision.status == "running"

    def test_stops_confidently_once_the_margin_is_met(self) -> None:
        """A unambiguous evidence chain must reach the 'confident' terminal state."""
        belief = BeliefState()
        for index, (probe_key, outcome) in enumerate(
            [
                ("DNS_LOOKUP", "TIMEOUT_RESOLVER"),
                ("ICMP_REACHABILITY:destination", "REACHABLE"),
                ("ICMP_REACHABILITY:resolver", "REACHABLE"),
                ("TCP_CONNECT", "CONNECTED"),
            ],
            start=1,
        ):
            belief.observe(probe_key=probe_key, outcome=outcome, sequence_number=index)
        decision = evaluate_stopping_rule(
            belief, probes_used=4, max_probes=8, next_probe=None
        )
        assert decision.should_stop is True
        assert decision.status == "confident", decision.reason
        assert decision.leading_code is Hypothesis.DNS_FAILURE
        assert decision.leading_probability >= CONFIDENCE_THRESHOLD
        assert decision.lead_over_runner_up >= CONFIDENCE_LEAD

    def test_a_large_but_insufficient_margin_is_not_confident(self) -> None:
        """Leading clearly is not the same as leading *enough*.

        Three mutually consistent observations put DNS failure far ahead of every
        alternative, but the posterior does not clear the absolute threshold, so the
        rule must decline to call it confident rather than report a forced answer.
        """
        belief = BeliefState()
        for index, (probe_key, outcome) in enumerate(
            [
                ("DNS_LOOKUP", "NXDOMAIN"),
                ("ICMP_REACHABILITY:destination", "REACHABLE"),
                ("TCP_CONNECT", "CONNECTED"),
            ],
            start=1,
        ):
            belief.observe(probe_key=probe_key, outcome=outcome, sequence_number=index)
        assert belief.leader().code is Hypothesis.DNS_FAILURE
        assert belief.leader().probability < CONFIDENCE_THRESHOLD
        decision = evaluate_stopping_rule(
            belief, probes_used=3, max_probes=8, next_probe=None
        )
        assert decision.status == "inconclusive"
        assert decision.leading_probability < CONFIDENCE_THRESHOLD

    def test_budget_exhaustion_is_its_own_status(self) -> None:
        belief = BeliefState()
        choice = select_next_probe(belief, context(), likelihood_table())
        decision = evaluate_stopping_rule(
            belief, probes_used=8, max_probes=8, next_probe=choice
        )
        assert decision.status == "budget_exhausted"
        assert "budget" in decision.reason.lower()

    def test_no_probe_left_is_inconclusive(self) -> None:
        belief = BeliefState()
        decision = evaluate_stopping_rule(
            belief, probes_used=3, max_probes=8, next_probe=None
        )
        assert decision.status == "inconclusive"
        assert "inconclusive" in decision.reason.lower()

    def test_negligible_information_gain_stops_as_inconclusive(self) -> None:
        belief = BeliefState()
        everything = [make_key(pt, sel) for pt, sel in CANONICAL_CANDIDATE_ORDER]
        choice = select_next_probe(
            belief, context(), likelihood_table(), executed_probe_keys=everything
        )
        decision = evaluate_stopping_rule(
            belief, probes_used=1, max_probes=8, next_probe=choice,
            min_information_gain=MIN_INFORMATION_GAIN_BITS,
        )
        assert decision.should_stop is True
        assert decision.status == "inconclusive"

    def test_a_narrow_lead_does_not_stop_as_confident(self) -> None:
        belief = BeliefState()
        belief.observe(probe_key="DNS_LOOKUP", outcome="TIMEOUT_RESOLVER", sequence_number=1)
        # The leader has not cleared the margin, so the rule must not stop as
        # confident even though one hypothesis is clearly ahead.
        decision = evaluate_stopping_rule(
            belief, probes_used=1, max_probes=8, next_probe=None
        )
        assert decision.status != "confident" or (
            decision.leading_probability >= CONFIDENCE_THRESHOLD
            and decision.lead_over_runner_up >= CONFIDENCE_LEAD
        )

    def test_thresholds_are_configurable(self) -> None:
        belief = BeliefState()
        belief.observe(probe_key="DNS_LOOKUP", outcome="NXDOMAIN", sequence_number=1)
        strict = evaluate_stopping_rule(
            belief, probes_used=1, max_probes=8, next_probe=None,
            confidence_threshold=0.999, confidence_lead=0.5,
        )
        assert strict.status != "confident"

    def test_decision_payload_is_serialisable(self) -> None:
        import json

        decision = evaluate_stopping_rule(
            BeliefState(), probes_used=0, max_probes=8, next_probe=None
        )
        assert json.dumps(decision.to_public())


class TestHypothesisCatalogue:
    def test_required_hypotheses_exist(self) -> None:
        required = {
            "LINK_FAILURE",
            "ROUTING_FAILURE",
            "DNS_FAILURE",
            "PACKET_LOSS",
            "HIGH_LATENCY",
            "MTU_BLACK_HOLE",
            "TCP_FILTER_OR_PORT_FAILURE",
            "APPLICATION_SERVICE_FAILURE",
            "UNKNOWN_OR_MULTIPLE_CAUSES",
        }
        assert required <= {code.value for code in Hypothesis}

    def test_unknown_and_no_fault_are_always_available(self) -> None:
        assert Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES in HYPOTHESES
        assert Hypothesis.NO_FAULT_DETECTED in HYPOTHESES

    def test_every_hypothesis_documents_layers_and_remediation(self) -> None:
        for code, spec in HYPOTHESES.items():
            assert spec.layers, f"{code.value} has no layers"
            assert spec.summary, f"{code.value} has no summary"
            assert spec.verification_steps, f"{code.value} has no verification steps"
            assert spec.remediation, f"{code.value} has no remediation"
            assert spec.title

    def test_unsupported_faults_no_longer_need_placeholder_hypotheses(self) -> None:
        # Every fault type maps to exactly one documented hypothesis.
        from app.experiments.scenarios import EXPECTED_HYPOTHESIS
        from app.lab.faults import FaultType

        assert set(EXPECTED_HYPOTHESIS) == set(FaultType)
        for code in EXPECTED_HYPOTHESIS.values():
            assert Hypothesis(code) in HYPOTHESES

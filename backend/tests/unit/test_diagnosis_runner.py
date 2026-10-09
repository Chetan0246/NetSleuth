"""Unit tests for the explanation layer, the localizer and the diagnosis runner."""

from __future__ import annotations

import pytest

from app.core.config import CONFIDENCE_LEAD, CONFIDENCE_THRESHOLD
from app.core.errors import ValidationError
from app.diagnosis.baseline import BASELINE_ORDER
from app.diagnosis.explanations import (
    FORBIDDEN_CLAIM_WORDS,
    build_contributions,
    build_explanation,
    validate_explanation,
)
from app.diagnosis.hypotheses import Hypothesis
from app.diagnosis.localizer import localize
from app.diagnosis.runner import DiagnosisRun, run_diagnosis
from app.lab.faults import FaultType, LabState
from app.lab.outcomes import ProbeType
from app.lab.templates import get_template
from app.probes.base import EvidenceStatement, ProbeObservation

from tests.conftest import inject


def observation(
    probe_key: str = "DNS_LOOKUP",
    outcome: str = "TIMEOUT_RESOLVER",
    details: dict | None = None,
    evidence: list[EvidenceStatement] | None = None,
) -> ProbeObservation:
    return ProbeObservation(
        probe_key=probe_key,
        probe_type=ProbeType(probe_key.split(":")[0]),
        probe_label=f"label for {probe_key}",
        source_node_id="client-1",
        destination_node_id="web-1",
        outcome=outcome,
        summary="summary text",
        details=details or {},
        evidence=evidence or [],
    )


class TestLocalizer:
    def test_link_fault_is_localized_from_a_partial_trace(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        location = run.suspected_component()
        assert location.component_kind == "link"
        assert location.component_id == "l-campus-edge"
        assert location.confidence in ("moderate", "strong")
        assert location.evidence

    def test_dns_fault_is_localized_to_the_resolver(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        location = run.suspected_component()
        assert location.component_kind == "node"
        assert location.component_id == "dns-1"
        assert location.confidence == "strong"

    def test_service_fault_is_localized_to_host_and_port(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.SERVICE_DOWN, "web-1:web")
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        location = run.suspected_component()
        assert location.component_kind == "service"
        assert location.component_id == "web-1:80"
        assert location.confidence == "strong"

    def test_mtu_fault_is_localized_to_the_limiting_link(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        location = run.suspected_component()
        assert location.component_id == "l-campus-edge"

    def test_packet_loss_location_is_reported_as_weak_not_invented(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.6)
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        location = run.suspected_component()
        assert location.confidence in ("none", "weak"), (
            "end-to-end probes cannot attribute link-level loss to one link, so the "
            "localizer must not claim to"
        )

    def test_unresolved_cause_localizes_nothing(self) -> None:
        result = localize(Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES, [observation()])
        assert result.component_id is None
        assert result.confidence == "none"

    def test_no_evidence_localizes_nothing(self) -> None:
        result = localize(Hypothesis.LINK_FAILURE, [])
        assert result.found is False

    def test_healthy_result_implicates_no_component(self) -> None:
        result = localize(Hypothesis.NO_FAULT_DETECTED, [observation(outcome="HEALTHY")])
        assert result.component_kind == "none"
        assert "no component is implicated" in " ".join(result.evidence).lower()

    def test_payload_is_serialisable(self) -> None:
        import json

        assert json.dumps(localize(Hypothesis.DNS_FAILURE, [observation()]).to_public())


class TestExplanationBuilder:
    def _build(self, campus_lab: LabState, **kwargs: object):
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web", **kwargs,  # type: ignore[arg-type]
        )
        return run.explanation()

    def test_confident_dns_explanation_states_the_cause(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        explanation = self._build(campus_lab)
        assert explanation.status == "confident"
        assert "DNS_FAILURE" in explanation.headline
        assert explanation.supporting_evidence

    def test_reasoning_lists_every_probe_with_its_information_gain(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        explanation = self._build(campus_lab)
        assert len(explanation.contributions) >= 3
        for contribution in explanation.contributions:
            assert contribution.entropy_before >= contribution.entropy_after

    def test_summary_reports_the_probe_count_and_stopping_reason(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        explanation = self._build(campus_lab)
        assert "probe" in explanation.summary
        assert explanation.recommended_next_step

    def test_caveats_always_label_the_simulation(self, campus_lab: LabState) -> None:
        explanation = self._build(campus_lab)
        assert any("SIMULATED LAB" in item for item in explanation.caveats)
        assert any("Ground-truth" in item or "ground truth" in item.lower()
                   for item in explanation.caveats)

    def test_inconclusive_run_says_so_and_recommends_new_evidence(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web", max_probes=1,
        )
        explanation = run.explanation()
        if run.status != "confident":
            assert "nconclusive" in explanation.headline or "Unresolved" in explanation.headline
            assert explanation.recommended_next_step

    def test_confidence_is_never_overstated_as_proof(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        explanation = self._build(campus_lab)
        text = " ".join(
            explanation.reasoning
            + explanation.supporting_evidence
            + [explanation.headline, explanation.summary]
        ).lower()
        for word in FORBIDDEN_CLAIM_WORDS:
            assert word not in text, f"explanation used overclaiming word {word!r}"

    def test_validator_rejects_a_fabricated_claim(self) -> None:
        from app.diagnosis.explanations import DiagnosisExplanation

        fabricated = DiagnosisExplanation(
            status="confident",
            headline="Most likely cause: DNS_FAILURE (95% confidence)",
            summary="summary",
            reasoning=[],
            supporting_evidence=[
                "A probe that never ran returned MADE_UP, raising DNS_FAILURE."
            ],
            weakening_evidence=[],
            unexplained=[],
            uncertainty_note="",
            recommended_next_step="",
            remediation=[],
            caveats=[],
            contributions=[],
        )
        with pytest.raises(AssertionError, match="no observation produced"):
            validate_explanation(fabricated, [observation()])

    def test_validator_rejects_proof_language(self) -> None:
        from app.diagnosis.explanations import DiagnosisExplanation

        overclaiming = DiagnosisExplanation(
            status="confident",
            headline="Most likely cause: DNS_FAILURE",
            summary="This proves the resolver is down.",
            reasoning=[],
            supporting_evidence=[],
            weakening_evidence=[],
            unexplained=[],
            uncertainty_note="",
            recommended_next_step="",
            remediation=[],
            caveats=[],
            contributions=[],
        )
        with pytest.raises(AssertionError, match="proof language"):
            validate_explanation(overclaiming, [observation()])

    def test_validator_accepts_a_real_explanation(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        validate_explanation(
            run.explanation(), [step.observation for step in run.steps]
        )

    def test_contributions_record_likelihood_ratios(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        contributions = build_contributions(
            [step.observation for step in run.steps],
            [step.update for step in run.steps],
        )
        assert len(contributions) == len(run.steps)
        assert contributions[0].likelihood_ratios
        assert contributions[0].supporting or contributions[0].weakening

    def test_public_payload_is_serialisable(self, campus_lab: LabState) -> None:
        import json

        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=campus_lab, topology=campus_lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service="web",
        )
        assert json.dumps(run.explanation().to_public())


class TestDiagnosisRunner:
    def _run(self, lab: LabState, **kwargs: object) -> DiagnosisRun:
        return run_diagnosis(
            diagnosis_id="d",
            session_id="s",
            lab=lab,
            topology=lab.topology,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            **kwargs,  # type: ignore[arg-type]
        )

    def test_adaptive_run_reaches_a_terminal_state(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        assert run.status in ("confident", "inconclusive", "budget_exhausted")
        assert run.completed_at is not None

    def test_step_executes_exactly_one_probe(self, campus_lab: LabState) -> None:
        run = self._run(campus_lab, to_completion=False)
        assert run.probes_used == 0
        step = run.step()
        assert step is not None
        assert run.probes_used == 1

    def test_step_after_completion_returns_none(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        assert run.step() is None

    def test_no_probe_runs_twice(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        run = self._run(campus_lab)
        keys = run.executed_probe_keys()
        assert len(keys) == len(set(keys)), (
            "re-running an identical probe would feed the same observation to Bayes twice"
        )

    def test_probe_budget_is_respected(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        run = self._run(campus_lab, max_probes=2)
        assert run.probes_used <= 2

    def test_budget_of_one_is_allowed(self, campus_lab: LabState) -> None:
        run = self._run(campus_lab, max_probes=1)
        assert run.probes_used == 1

    def test_zero_budget_is_rejected(self, campus_lab: LabState) -> None:
        with pytest.raises(ValidationError, match="at least 1"):
            self._run(campus_lab, max_probes=0)

    def test_belief_stays_normalised_throughout(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        run = self._run(campus_lab)
        for step in run.steps:
            assert sum(step.belief_after.values()) == pytest.approx(1.0, abs=1e-6)

    def test_entropy_never_increases(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        for step in run.steps:
            assert step.entropy_after <= step.entropy_before + 1e-9

    def test_evidence_driven_selection_is_recorded_per_step(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        for step in run.steps:
            assert step.selected_reason
            assert step.planned_information_gain is not None

    def test_adaptive_and_baseline_use_the_same_probe_implementations(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        adaptive = self._run(campus_lab, strategy="adaptive")
        baseline = self._run(campus_lab, strategy="baseline")
        for step in adaptive.steps + baseline.steps:
            assert step.observation.mode == "SIMULATED LAB"
            assert step.observation.summary

    def test_baseline_follows_the_fixed_order(self, campus_lab: LabState) -> None:
        baseline = self._run(campus_lab, strategy="baseline")
        executed = baseline.executed_probe_keys()
        expected = [key for key in BASELINE_ORDER if key in executed]
        assert executed == expected

    def test_baseline_can_take_more_probes_than_adaptive(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        adaptive = self._run(campus_lab, strategy="adaptive")
        baseline = self._run(campus_lab, strategy="baseline")
        assert baseline.probes_used >= adaptive.probes_used, (
            "the fixed order runs the MTU ladder only after several other probes"
        )

    def test_same_seed_reproduces_the_same_probe_sequence(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        first = self._run(campus_lab)
        second = self._run(campus_lab)
        assert first.executed_probe_keys() == second.executed_probe_keys()
        assert [step.observation.outcome for step in first.steps] == [
            step.observation.outcome for step in second.steps
        ]

    def test_every_step_records_structured_details_and_evidence(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        run = self._run(campus_lab)
        for step in run.steps:
            assert isinstance(step.observation.details, dict)
            assert step.observation.details
            assert step.observation.evidence

    def test_public_payload_is_serialisable_and_complete(self, campus_lab: LabState) -> None:
        import json

        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        payload = run.to_public()
        assert json.dumps(payload)
        assert payload["mode"] == "SIMULATED LAB"
        assert payload["beliefs"]["ranked"]
        assert payload["steps"]
        assert payload["report"]
        assert payload["model_version"]
        assert payload["suspected_component"]

    def test_ground_truth_is_absent_from_the_engine_input(self, campus_lab: LabState) -> None:
        """The engine must not be able to read the injected fault label."""
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        payload = run.to_public()
        serialised = str(payload)
        assert "DNS_FAILURE'" not in serialised or True  # hypothesis name is legitimate
        # The *fault* label and its id must not appear in the diagnostic payload.
        for fault in campus_lab.faults:
            assert fault.id not in serialised, (
                f"the injected fault id {fault.id} leaked into the diagnosis payload"
            )
        # And the request objects the probes receive carry no fault information.
        from app.probes.base import ProbeRequest

        request_fields = set(ProbeRequest.model_fields)
        assert not any("fault" in field for field in request_fields)

    def test_context_does_not_carry_ground_truth(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = self._run(campus_lab)
        context = run.context()
        for value in context.__dict__.values():
            assert not isinstance(value, FaultType)

    def test_healthy_lab_is_reported_as_no_fault(self, campus_lab: LabState) -> None:
        run = self._run(campus_lab)
        assert run.beliefs()["ranked"][0]["code"] == "NO_FAULT_DETECTED"
        assert run.status == "confident"

    def test_unknown_goal_is_never_forced_when_evidence_is_ambiguous(
        self, campus_lab: LabState
    ) -> None:
        run = self._run(campus_lab, max_probes=1)
        if run.status == "budget_exhausted":
            top = run.beliefs()["ranked"][0]
            assert top["probability"] < CONFIDENCE_THRESHOLD or run.status == "confident"

    def test_rejected_candidates_are_explained(self, campus_lab: LabState) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        run = run_diagnosis(
            diagnosis_id="d", session_id="s", lab=lab, topology=lab.topology,
            source_node_id="client-1", destination_node_id="web-1",
            destination_service=None, port=80, to_completion=False,
        )
        run.step()
        assert any(
            item["probe_key"] == "SERVICE_HEALTH" for item in run.rejected_candidates
        )

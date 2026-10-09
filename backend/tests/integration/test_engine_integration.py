"""Integration tests: every fault, every template, persistence and the evaluator.

These tests exercise the whole engine (lab -> probes -> Bayes -> planner -> report)
without HTTP, which is the level the plan calls the "run a diagnosis from the
backend only" exit gate (section 19, Phase 4).
"""

from __future__ import annotations

import pytest

from app.diagnosis.runner import run_diagnosis
from app.experiments.metrics import compute_metrics
from app.experiments.runner import ExperimentConfig, plan_experiment, run_experiment
from app.experiments.scenarios import SCENARIOS, EXPECTED_HYPOTHESIS, scenarios_for
from app.lab.faults import FaultType, LabState, normalize_fault, FaultSpec
from app.lab.templates import get_template
from app.storage.database import Database
from app.storage.repository import DiagnosisService, SessionService


def inject(lab: LabState, fault_type: FaultType, target_id: str, **parameters: object) -> None:
    lab.add_fault(
        normalize_fault(
            FaultSpec(fault_type=fault_type, target_id=target_id, parameters=dict(parameters)),
            lab.topology,
            f"it-{fault_type.value.lower()}",
        )
    )


#: One representative injection per fault type, with the hypothesis it should
#: produce. Kept explicit so a change in the model shows up here as a failure
#: rather than as a silent accuracy drop.
FAULT_CASES: list[tuple[str, FaultType, str, dict[str, object], str]] = [
    ("LINK_DOWN", FaultType.LINK_DOWN, "l-campus-edge", {}, "LINK_FAILURE"),
    (
        "ROUTE_BLACKHOLE",
        FaultType.ROUTE_BLACKHOLE,
        "l-campus-edge",
        {"destination_node_id": "web-1"},
        "ROUTING_FAILURE",
    ),
    ("DNS_FAILURE", FaultType.DNS_FAILURE, "dns-1", {}, "DNS_FAILURE"),
    (
        "PACKET_LOSS",
        FaultType.PACKET_LOSS,
        "l-campus-edge",
        {"loss_rate": 0.6},
        "PACKET_LOSS",
    ),
    (
        "HIGH_LATENCY",
        FaultType.HIGH_LATENCY,
        "l-campus-edge",
        {"added_latency_ms": 400.0},
        "HIGH_LATENCY",
    ),
    (
        "MTU_BLACK_HOLE",
        FaultType.MTU_BLACK_HOLE,
        "l-campus-edge",
        {"mtu_bytes": 576},
        "MTU_BLACK_HOLE",
    ),
    (
        "TCP_PORT_BLOCKED",
        FaultType.TCP_PORT_BLOCKED,
        "web-1:web",
        {},
        "TCP_FILTER_OR_PORT_FAILURE",
    ),
    (
        "TCP_PORT_REJECTED",
        FaultType.TCP_PORT_REJECTED,
        "web-1:web",
        {},
        "TCP_FILTER_OR_PORT_FAILURE",
    ),
    ("SERVICE_DOWN", FaultType.SERVICE_DOWN, "web-1:web", {}, "APPLICATION_SERVICE_FAILURE"),
    (
        "GATEWAY_UNREACHABLE",
        FaultType.GATEWAY_UNREACHABLE,
        "l-client1-access",
        {},
        "LINK_FAILURE",
    ),
]


def _run(lab: LabState, source: str = "client-1", destination: str = "web-1",
         service: str | None = "web", strategy: str = "adaptive", **kwargs: object):
    return run_diagnosis(
        diagnosis_id="integration",
        session_id="s",
        lab=lab,
        topology=lab.topology,
        source_node_id=source,
        destination_node_id=destination,
        destination_service=service,
        strategy=strategy,
        **kwargs,  # type: ignore[arg-type]
    )


class TestEveryFaultIsDiagnosed:
    @pytest.mark.parametrize(
        "name,fault_type,target,params,expected",
        FAULT_CASES,
        ids=[case[0] for case in FAULT_CASES],
    )
    def test_fault_reaches_the_expected_top_hypothesis(
        self,
        name: str,
        fault_type: FaultType,
        target: str,
        params: dict[str, object],
        expected: str,
        campus_lab: LabState,
    ) -> None:
        inject(campus_lab, fault_type, target, **params)
        run = _run(campus_lab)
        top = run.beliefs()["ranked"][0]
        assert top["code"] == expected, (
            f"{name} was diagnosed as {top['code']} at {top['probability']:.2f}; "
            f"evidence: {[s.probe_key + ':' + s.outcome for s in run.steps]}"
        )
        assert run.status == "confident", (
            f"{name} did not reach a confident decision: {run.stopping_reason}"
        )

    @pytest.mark.parametrize(
        "name,fault_type,target,params,expected",
        FAULT_CASES,
        ids=[case[0] for case in FAULT_CASES],
    )
    def test_fault_is_diagnosed_on_the_multihop_template(
        self,
        name: str,
        fault_type: FaultType,
        target: str,
        params: dict[str, object],
        expected: str,
        multihop_lab: LabState,
    ) -> None:
        """The same fault classes must be diagnosable on the second topology."""
        remap = {
            "l-campus-edge": "l-core-branch",
            "l-client1-access": "l-client1-edge",
            "web-1:web": "app-1:api",
        }
        effective_target = remap.get(target, target)
        effective_params = dict(params)
        if "destination_node_id" in effective_params:
            effective_params["destination_node_id"] = "app-1"
        inject(multihop_lab, fault_type, effective_target, **effective_params)
        run = _run(multihop_lab, destination="app-1", service="api")
        top = run.beliefs()["ranked"][0]
        assert top["code"] == expected, (
            f"{name} on multihop-wan was diagnosed as {top['code']} "
            f"({top['probability']:.2f})"
        )

    def test_healthy_lab_reports_no_fault_on_both_templates(self) -> None:
        campus = LabState(get_template("campus-basic"), random_seed=1)
        assert _run(campus).beliefs()["ranked"][0]["code"] == "NO_FAULT_DETECTED"
        multihop = LabState(get_template("multihop-wan"), random_seed=1)
        assert (
            _run(multihop, destination="app-1", service="api").beliefs()["ranked"][0]["code"]
            == "NO_FAULT_DETECTED"
        )

    def test_each_fault_is_reversible_and_then_diagnoses_as_healthy(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        assert _run(campus_lab).beliefs()["ranked"][0]["code"] == "LINK_FAILURE"
        campus_lab.clear_faults()
        assert _run(campus_lab).beliefs()["ranked"][0]["code"] == "NO_FAULT_DETECTED"

    def test_same_seed_reproduces_the_diagnosis(self) -> None:
        results = []
        for _ in range(3):
            lab = LabState(get_template("campus-basic"), random_seed=4242)
            inject(lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
            run = _run(lab)
            results.append(
                (
                    run.executed_probe_keys(),
                    [step.observation.outcome for step in run.steps],
                    run.beliefs()["ranked"][0]["code"],
                )
            )
        assert results[0] == results[1] == results[2]


class TestStrategyComparison:
    def test_both_strategies_run_on_every_fault(self, campus_lab: LabState) -> None:
        for _, fault_type, target, params, _ in FAULT_CASES:
            lab = LabState(get_template("campus-basic"), random_seed=20261009)
            inject(lab, fault_type, target, **params)
            for strategy in ("adaptive", "baseline"):
                run = _run(lab, strategy=strategy)
                assert run.status in ("confident", "inconclusive", "budget_exhausted")
                assert run.probes_used >= 1

    def test_strategies_share_the_same_probe_implementations(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        adaptive = _run(campus_lab, strategy="adaptive")
        baseline = _run(campus_lab, strategy="baseline")
        adaptive_outcomes = {
            (step.observation.probe_key, step.observation.outcome) for step in adaptive.steps
        }
        baseline_outcomes = {
            (step.observation.probe_key, step.observation.outcome) for step in baseline.steps
        }
        overlap = adaptive_outcomes & baseline_outcomes
        assert overlap, "the same probe must produce the same outcome in both strategies"
        for step in adaptive.steps + baseline.steps:
            assert step.observation.mode == "SIMULATED LAB"

    def test_strategies_share_the_stopping_rule(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        adaptive = _run(campus_lab, strategy="adaptive")
        baseline = _run(campus_lab, strategy="baseline")
        for run in (adaptive, baseline):
            if run.status == "confident":
                top = run.beliefs()["ranked"]
                assert top[0]["probability"] >= 0.8
                assert top[0]["probability"] - top[1]["probability"] >= 0.2

    def test_adaptive_needs_no_more_probes_than_the_baseline_on_average(self) -> None:
        totals = {"adaptive": 0, "baseline": 0}
        for seed in (1, 2, 3, 4, 5):
            for _, fault_type, target, params, _ in FAULT_CASES:
                lab = LabState(get_template("campus-basic"), random_seed=seed)
                inject(lab, fault_type, target, **params)
                for strategy in ("adaptive", "baseline"):
                    totals[strategy] += _run(lab, strategy=strategy).probes_used
        assert totals["adaptive"] <= totals["baseline"], (
            "the adaptive planner executed more probes in total than the fixed order, "
            "which would contradict the information-gain premise"
        )


class TestPersistenceIntegration:
    def test_session_round_trips_through_sqlite(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        created = sessions.create_session("campus-basic", name="round trip")
        loaded = sessions.get_session(created["id"])
        assert loaded["template_id"] == "campus-basic"
        assert loaded["topology"]["nodes"]
        assert loaded["available_faults"], "a session must offer injectable faults"

    def test_faults_survive_a_service_restart(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        created = sessions.create_session("campus-basic")
        sessions.apply_fault(
            created["id"],
            FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1"),
        )
        # A fresh service instance has an empty in-memory cache, so this exercises
        # the storage path rather than the cache.
        restarted = SessionService(database)
        loaded = restarted.get_session(created["id"])
        assert len(loaded["active_faults"]) == 1
        assert loaded["active_faults"][0]["fault_type"] == "DNS_FAILURE"
        assert restarted.lab_for(created["id"]).topology.node("dns-1").resolver_enabled is False

    def test_reset_removes_active_faults(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        created = sessions.create_session("campus-basic")
        sessions.apply_fault(
            created["id"],
            FaultSpec(fault_type=FaultType.LINK_DOWN, target_id="l-campus-edge"),
        )
        reset = sessions.reset_session(created["id"])
        assert reset["active_faults"] == []
        assert (
            sessions.lab_for(created["id"]).topology.link("l-campus-edge").up is True
        )

    def test_diagnosis_and_observations_are_persisted(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        created = sessions.create_session("campus-basic")
        sessions.apply_fault(
            created["id"],
            FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1"),
        )
        run = diagnoses.create(
            session_id=created["id"],
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        row = database.get_diagnosis(run.diagnosis_id)
        assert row["status"] == run.status
        assert row["probes_used"] == run.probes_used
        observations = database.list_observations(run.diagnosis_id)
        assert len(observations) == run.probes_used
        for item in observations:
            assert item["probe_key"]
            assert item["outcome"]
            assert item["details_json"]
        snapshots = database.list_belief_snapshots(run.diagnosis_id)
        assert len(snapshots) == run.probes_used
        assert all(item["entropy_after"] <= item["entropy_before"] + 1e-9 for item in snapshots)

    def test_repersisting_a_diagnosis_does_not_duplicate_observations(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        created = sessions.create_session("campus-basic")
        run = diagnoses.create(
            session_id=created["id"],
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
        )
        for _ in range(3):
            diagnoses.step(run.diagnosis_id)
        stored = database.list_observations(run.diagnosis_id)
        assert len(stored) == run.probes_used
        assert [item["sequence_number"] for item in stored] == list(
            range(1, run.probes_used + 1)
        )

    def test_stored_payload_has_no_ground_truth(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        created = sessions.create_session("campus-basic")
        sessions.apply_fault(
            created["id"],
            FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1"),
        )
        run = diagnoses.create(
            session_id=created["id"],
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        row = database.get_diagnosis(run.diagnosis_id)
        for fault in sessions.lab_for(created["id"]).faults:
            assert fault.id not in str(row["result_json"])

    def test_unknown_session_and_diagnosis_raise_not_found(self) -> None:
        from app.core.errors import NotFoundError

        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        with pytest.raises(NotFoundError):
            sessions.get_session("ghost")
        with pytest.raises(NotFoundError):
            diagnoses.get("ghost")

    def test_invalid_diagnosis_target_is_rejected(self) -> None:
        from app.core.errors import ValidationError

        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        created = sessions.create_session("campus-basic")
        with pytest.raises(ValidationError, match="unknown source node"):
            diagnoses.create(
                session_id=created["id"],
                source_node_id="ghost",
                destination_node_id="web-1",
            )
        with pytest.raises(ValidationError, match="two different nodes"):
            diagnoses.create(
                session_id=created["id"],
                source_node_id="web-1",
                destination_node_id="web-1",
            )
        with pytest.raises(ValidationError, match="does not expose a service named"):
            diagnoses.create(
                session_id=created["id"],
                source_node_id="client-1",
                destination_node_id="web-1",
                destination_service="ghost",
            )

    def test_reading_a_diagnosis_from_a_previous_process_explains_itself(self) -> None:
        from app.core.errors import NotFoundError

        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        created = sessions.create_session("campus-basic")
        run = diagnoses.create(
            session_id=created["id"],
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        fresh = DiagnosisService(database, SessionService(database))
        with pytest.raises(NotFoundError):
            fresh.get(run.diagnosis_id)


class TestExperimentIntegration:
    def test_plan_counts_runs_correctly(self) -> None:
        config = ExperimentConfig(runs_per_scenario=2, template_ids=["campus-basic"])
        plan = plan_experiment(config)
        scenarios = len(scenarios_for(template_ids=["campus-basic"]))
        assert plan["total_runs"] == scenarios * 2 * 2

    def test_experiment_produces_metrics_from_real_runs(self) -> None:
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        metrics = summary["metrics"]
        runs = database.list_experiment_runs(summary["experiment_id"])
        assert len(runs) == summary["completed_runs"] == metrics["run_count"]
        assert summary["completed_runs"] == plan_experiment(
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"])
        )["total_runs"]
        for strategy, block in metrics["by_strategy"].items():
            assert block["runs"] > 0
            assert 0.0 <= block["top1_accuracy"] <= 1.0
            assert block["mean_probes_all"] > 0

    def test_metrics_are_recomputable_from_stored_records(self) -> None:
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        runs = database.list_experiment_runs(summary["experiment_id"])
        recomputed = compute_metrics(runs, config=summary["config"])
        assert recomputed["run_count"] == summary["metrics"]["run_count"]
        for strategy in summary["metrics"]["by_strategy"]:
            assert (
                recomputed["by_strategy"][strategy]["top1_correct"]
                == summary["metrics"]["by_strategy"][strategy]["top1_correct"]
            )

    def test_same_configuration_reproduces_the_metrics(self) -> None:
        first = run_experiment(
            Database(":memory:"),
            ExperimentConfig(runs_per_scenario=1, seed=777, template_ids=["campus-basic"]),
        )
        second = run_experiment(
            Database(":memory:"),
            ExperimentConfig(runs_per_scenario=1, seed=777, template_ids=["campus-basic"]),
        )
        for strategy in first["metrics"]["by_strategy"]:
            assert (
                first["metrics"]["by_strategy"][strategy]["top1_correct"]
                == second["metrics"]["by_strategy"][strategy]["top1_correct"]
            )
            assert (
                first["metrics"]["by_strategy"][strategy]["top1_correct"]
                == second["metrics"]["by_strategy"][strategy]["top1_correct"]
            )

    def test_different_seeds_change_the_observed_evidence(self) -> None:
        """Seeding must actually change what the probes observe.

        The comparison is made on the *predicted fault class and probe count* per
        scenario across seeds, because those are the observable consequences of a
        different packet-loss stream. A single coarse proxy (probe counts only) can
        legitimately coincide between two seeds, so several seeds are compared.
        """
        signatures: set[tuple] = set()
        for seed in (11, 22, 33, 44, 55, 66):
            database = Database(":memory:")
            summary = run_experiment(
                database,
                ExperimentConfig(
                    runs_per_scenario=2,
                    seed=seed,
                    fault_types=[],
                    template_ids=["campus-basic"],
                    include_control=False,
                    strategies=["adaptive"],
                ),
            )
            runs = database.list_experiment_runs(summary["experiment_id"])
            signatures.add(
                tuple(
                    sorted(
                        (run["scenario_id"], run["seed"], run["predicted_fault_type"],
                         run["probes_used"])
                        for run in runs
                    )
                )
            )
            database.close()
        assert len(signatures) > 1, (
            "changing the seed produced identical predicted classes and probe counts for "
            "every scenario, which would mean the seed does not reach the RNG"
        )

    def test_ground_truth_is_used_only_by_the_evaluator(self) -> None:
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        for run in database.list_experiment_runs(summary["experiment_id"]):
            assert run["actual_fault_type"] is not None or run["scenario_id"].endswith(
                ("nofault-web", "nofault-api")
            )
            # The evaluator's ground truth lives in its own columns; the diagnosis
            # payload stored in ranked_json must not contain the fault id.
            assert "gt-fault" not in run["ranked_json"]

    def test_confusion_matrix_is_consistent_with_the_runs(self) -> None:
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        runs = database.list_experiment_runs(summary["experiment_id"])
        matrix = summary["metrics"]["confusion_matrix"]["matrix"]
        total = sum(sum(row.values()) for row in matrix.values())
        assert total == len(runs)

    def test_accepted_confusions_are_not_the_same_as_top1(self) -> None:
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        for block in summary["metrics"]["by_strategy"].values():
            assert block["accepted_confusion_correct"] >= block["top1_correct"]

    def test_per_fault_breakdown_covers_every_injected_class(self) -> None:
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        expected_classes = {
            EXPECTED_HYPOTHESIS[scenario.fault_type]
            for scenario in scenarios_for(template_ids=["campus-basic"])
            if scenario.fault_type
        }
        present = set(summary["metrics"]["by_fault_class"])
        assert expected_classes <= present

    def test_export_helpers_produce_non_empty_output(self) -> None:
        from app.experiments.exports import (
            experiment_runs_csv,
            experiment_summary_markdown,
        )

        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(runs_per_scenario=1, template_ids=["campus-basic"]),
        )
        runs = database.list_experiment_runs(summary["experiment_id"])
        csv_text = experiment_runs_csv(runs, experiment_id=summary["experiment_id"])
        assert csv_text.count("\n") == len(runs) + 1
        markdown = experiment_summary_markdown(summary)
        assert "Top-1" in markdown
        assert str(summary["completed_runs"]) in markdown

    def test_no_scenario_match_is_an_error_not_an_empty_success(self) -> None:
        database = Database(":memory:")
        with pytest.raises(ValueError, match="no scenario matches"):
            run_experiment(
                database,
                ExperimentConfig(
                    runs_per_scenario=1, template_ids=["does-not-exist"]
                ),
            )

    def test_scenario_catalogue_matches_the_fault_types(self) -> None:
        injected = {scenario.fault_type for scenario in SCENARIOS if scenario.fault_type}
        assert injected == set(FaultType)
        assert all(scenario.expected_component_id for scenario in SCENARIOS if scenario.fault_type)


class TestReportExport:
    def test_markdown_report_contains_the_evidence(self, campus_lab: LabState) -> None:
        from app.experiments.exports import diagnosis_report_markdown

        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        run = _run(campus_lab)
        markdown = diagnosis_report_markdown(run.to_public())
        assert "NetSleuth diagnostic report" in markdown
        assert "DNS_FAILURE" in markdown
        for step in run.steps:
            assert step.observation.probe_key in markdown
        assert "SIMULATED LAB" in markdown

    def test_json_report_round_trips(self, campus_lab: LabState) -> None:
        import json

        from app.experiments.exports import diagnosis_report_json

        run = _run(campus_lab)
        parsed = json.loads(diagnosis_report_json(run.to_public()))
        assert parsed["id"] == run.diagnosis_id
        assert parsed["beliefs"]["ranked"]

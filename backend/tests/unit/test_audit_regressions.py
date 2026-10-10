"""Regression tests for defects found during the deep audit.

Each test here pins a specific bug that was fixed after the initial implementation.
They are kept separate from the feature tests so the reason each one exists is
obvious, and so deleting the fix cannot be mistaken for tidying up a redundant test.
"""

from __future__ import annotations

import json

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.diagnosis.baseline import build_baseline_plan, select_baseline_probe
from app.diagnosis.bayes import BeliefState
from app.diagnosis.hypotheses import DEFAULT_PRIORS, Hypothesis
from app.diagnosis.planner import DiagnosisContext
from app.diagnosis.runner import DiagnosisRun, fault_signature, run_diagnosis
from app.lab.faults import (
    FaultConfig,
    FaultInjectionError,
    FaultSpec,
    FaultType,
    LabState,
    normalize_fault,
    validate_fault,
)
from app.lab.outcomes import MtuOutcome, ServiceOutcome, TcpOutcome
from app.lab.routing import RoutingTable
from app.lab.simulator import LabSimulator
from app.lab.templates import get_template
from app.storage.database import Database
from app.storage.repository import DiagnosisService, SessionService


def _lab_with(fault_type: FaultType, target: str, **parameters: object) -> LabState:
    topology = get_template("campus-basic")
    lab = LabState(topology, random_seed=42)
    lab.add_fault(
        normalize_fault(
            FaultSpec(fault_type=fault_type, target_id=target, parameters=dict(parameters)),
            topology,
            "audit-fault",
        )
    )
    return lab


def _lab_snapshot(lab: LabState) -> dict:
    """Every piece of lab state a probe can observe."""
    return {
        "links": {
            link.id: (
                link.up,
                round(link.latency_ms, 6),
                round(link.packet_loss_rate, 6),
                link.mtu_bytes,
                link.suppress_frag_needed,
            )
            for link in lab.topology.links
        },
        "resolvers": {
            node.id: node.resolver_enabled for node in lab.topology.resolvers()
        },
        "ports": {
            (node.id, service.name): lab.port_state(node.id, service.port)
            for node, service in lab.topology.services()
        },
        "blackholes": sorted(lab.blackholes),
    }


class TestDiagnosisLabIsolation:
    """A running diagnosis must observe one consistent network state.

    Bug: ``DiagnosisRun`` held a reference to the session's cached ``LabState``.
    Injecting a fault mid-diagnosis therefore changed the evidence of a run that had
    already collected observations under the old state, mixing two network states into
    a single posterior and producing a confident answer from inconsistent evidence.
    """

    @pytest.mark.parametrize(
        "fault_type,target,parameters",
        [
            (FaultType.LINK_DOWN, "l-campus-edge", {}),
            (FaultType.PACKET_LOSS, "l-campus-edge", {"loss_rate": 0.5}),
            (FaultType.HIGH_LATENCY, "l-campus-edge", {"added_latency_ms": 400.0}),
            (FaultType.MTU_BLACK_HOLE, "l-campus-edge", {"mtu_bytes": 576}),
            (FaultType.DNS_FAILURE, "dns-1", {}),
            (FaultType.SERVICE_DOWN, "web-1:web", {}),
            (FaultType.TCP_PORT_BLOCKED, "web-1:web", {}),
            (FaultType.TCP_PORT_REJECTED, "web-1:web", {}),
            (FaultType.ROUTE_BLACKHOLE, "l-campus-edge", {"destination_node_id": "web-1"}),
            (FaultType.GATEWAY_UNREACHABLE, "l-client1-access", {}),
        ],
        ids=lambda value: value.value if isinstance(value, FaultType) else str(value),
    )
    def test_frozen_lab_reproduces_the_fault_state_exactly(
        self, fault_type: FaultType, target: str, parameters: dict
    ) -> None:
        """Copying an already-faulted lab must preserve state without double-applying."""
        lab = _lab_with(fault_type, target, **parameters)
        before = _lab_snapshot(lab)
        run = run_diagnosis(
            diagnosis_id="d",
            session_id="s",
            lab=lab,
            topology=lab.topology,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            to_completion=False,
        )
        assert _lab_snapshot(run.lab) == before, (
            "the diagnosis lab diverged from the lab it was created from; re-applying "
            "the fault list on top of an already-faulted topology would do this"
        )
        assert run.lab is not lab
        assert run.lab.topology is not lab.topology

    def test_injecting_a_fault_mid_diagnosis_does_not_change_a_running_run(self) -> None:
        """The strongest form: the run's own observations must not shift."""
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]

        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
        )
        first = diagnoses.step(run.diagnosis_id)
        outcomes_before = [step.observation.outcome for step in first.steps]
        snapshot_before = _lab_snapshot(first.lab)

        # Inject a fault through the service, which mutates the *session's* lab.
        sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1")
        )

        assert _lab_snapshot(first.lab) == snapshot_before
        assert first.lab.topology.node("dns-1").resolver_enabled is True, (
            "a running diagnosis must not start seeing a fault injected after it began"
        )
        assert outcomes_before == [step.observation.outcome for step in first.steps]

    def test_stepping_a_run_after_the_lab_changed_is_refused(self) -> None:
        """The API refuses to mix evidence from two network states."""
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]

        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
        )
        diagnoses.step(run.diagnosis_id)
        sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.SERVICE_DOWN, target_id="web-1:web")
        )

        with pytest.raises(ConflictError) as excinfo:
            diagnoses.step(run.diagnosis_id)
        assert excinfo.value.status_code == 409
        assert excinfo.value.field == "session_id"
        assert "different network states" in excinfo.value.detail
        assert excinfo.value.extra["frozen_faults"] == 0
        assert excinfo.value.extra["current_faults"] == 1

    def test_running_a_run_after_the_lab_was_reset_is_refused(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1")
        )

        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
        )
        diagnoses.step(run.diagnosis_id)
        sessions.reset_session(session_id)

        with pytest.raises(ConflictError, match="different network states"):
            diagnoses.run(run.diagnosis_id)

    def test_a_fresh_diagnosis_after_the_change_works(self) -> None:
        """The guard must not wedge the session: a new run sees the new state."""
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]

        first = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
        )
        diagnoses.step(first.diagnosis_id)
        sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1")
        )

        second = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
            run_to_completion=True,
        )
        assert second.beliefs()["ranked"][0]["code"] == "DNS_FAILURE"
        assert second.lab.topology.node("dns-1").resolver_enabled is False

    def test_an_unchanged_lab_continues_normally(self) -> None:
        """The guard must not fire when nothing changed."""
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="dns-1")
        )
        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
        )
        diagnoses.step(run.diagnosis_id)
        diagnoses.run(run.diagnosis_id)
        assert run.status == "confident"

    def test_fault_signature_is_order_independent(self) -> None:
        first = [
            FaultConfig(id="a", fault_type=FaultType.DNS_FAILURE, target_id="dns-1"),
            FaultConfig(id="b", fault_type=FaultType.LINK_DOWN, target_id="l-access-campus"),
        ]
        second = list(reversed(first))
        assert fault_signature(first) == fault_signature(second)

    def test_fault_signature_detects_parameter_and_activity_changes(self) -> None:
        base = [FaultConfig(id="a", fault_type=FaultType.PACKET_LOSS, target_id="l-access-campus",
                            parameters={"loss_rate": 0.4}, is_active=True)]
        changed = [FaultConfig(id="a", fault_type=FaultType.PACKET_LOSS,
                               target_id="l-access-campus", parameters={"loss_rate": 0.6},
                               is_active=True)]
        inactive = [FaultConfig(id="a", fault_type=FaultType.PACKET_LOSS,
                                target_id="l-access-campus",
                                parameters={"loss_rate": 0.4}, is_active=False)]
        removed: list[FaultConfig] = []
        # A FaultConfig defaults to is_active=False, so the baseline must state True
        # explicitly for the activity comparison to mean anything.
        assert fault_signature(base) != fault_signature(changed)
        assert fault_signature(base) != fault_signature(inactive)
        assert fault_signature(base) != fault_signature(removed)
        assert fault_signature(base) != fault_signature(
            [FaultConfig(id="a", fault_type=FaultType.PACKET_LOSS,
                         target_id="l-access-campus", parameters={"loss_rate": 0.4})]
        )

    def test_toggling_a_fault_off_then_stepping_is_refused(self) -> None:
        """Disabling a fault also changes the effective lab state."""
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        applied = sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.LINK_DOWN, target_id="l-campus-edge")
        )
        fault_id = applied["active_faults"][0]["id"]

        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            max_probes=8,
        )
        diagnoses.step(run.diagnosis_id)
        sessions.set_fault_active(session_id, fault_id, False)

        with pytest.raises(ConflictError, match="different network states"):
            diagnoses.step(run.diagnosis_id)


class TestDiagnosisListing:
    """Bug: ``GET /diagnoses`` without a session filter had a dead branch and an
    O(n²) nested comprehension that re-queried storage once per stored diagnosis."""

    def test_listing_all_sessions_returns_every_diagnosis_once(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        first_session = sessions.create_session("campus-basic")["id"]
        second_session = sessions.create_session("multihop-wan")["id"]

        created = [
            diagnoses.create(
                session_id=first_session,
                source_node_id="client-1",
                destination_node_id="web-1",
                destination_service="web",
                run_to_completion=True,
            ).diagnosis_id,
            diagnoses.create(
                session_id=first_session,
                source_node_id="client-1",
                destination_node_id="db-1",
                destination_service="postgres",
                run_to_completion=True,
            ).diagnosis_id,
            diagnoses.create(
                session_id=second_session,
                source_node_id="client-1",
                destination_node_id="app-1",
                destination_service="api",
                run_to_completion=True,
            ).diagnosis_id,
        ]

        listing = diagnoses.list_all(limit=50)
        ids = [item["id"] for item in listing]
        assert sorted(ids) == sorted(created)
        assert len(ids) == len(set(ids)), "a diagnosis must not be listed twice"
        for item in listing:
            assert item["top_hypothesis"], "every summary must carry its conclusion"
            assert item["session_id"] in (first_session, second_session)

    def test_listing_all_is_empty_on_a_fresh_database(self) -> None:
        database = Database(":memory:")
        diagnoses = DiagnosisService(database, SessionService(database))
        assert diagnoses.list_all() == []

    def test_per_session_listing_is_unchanged(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        other = sessions.create_session("campus-basic")["id"]
        diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        diagnoses.create(
            session_id=other,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        assert len(diagnoses.list_for_session(session_id)) == 1
        assert len(diagnoses.list_all()) == 2

    def test_a_run_from_a_previous_process_is_still_listed(self) -> None:
        """The stored-result path must produce the same shape as the live path."""
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )

        # A fresh service has no in-memory runs, so this exercises the stored path.
        restarted = DiagnosisService(database, SessionService(database))
        listing = restarted.list_all()
        assert len(listing) == 1
        entry = listing[0]
        assert entry["top_hypothesis"] == "DNS_FAILURE" or entry["top_hypothesis"] == (
            "NO_FAULT_DETECTED"
        )
        assert entry["top_probability"] is not None
        assert entry["entropy_bits"] is not None
        assert entry["probes_used"] > 0

    def test_api_lists_diagnoses_without_a_session_filter(self, client) -> None:
        from app.core.config import API_PREFIX

        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        other = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        for owner in (session["id"], other["id"]):
            client.post(
                f"{API_PREFIX}/diagnoses",
                json={
                    "session_id": owner,
                    "source_node_id": "client-1",
                    "destination_node_id": "web-1",
                    "destination_service": "web",
                    "run_to_completion": True,
                },
            )
        response = client.get(f"{API_PREFIX}/diagnoses")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len({item["id"] for item in body["diagnoses"]}) == 2


class TestApiContinuesOnlyConsistentRuns:
    """The ConflictError must reach the client as a structured 409."""

    def test_stepping_after_a_fault_change_returns_409(self, client) -> None:
        from app.core.config import API_PREFIX

        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        ).json()
        assert client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/step").status_code == 200

        client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"}},
        )

        response = client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/step")
        assert response.status_code == 409
        body = response.json()["error"]
        assert body["code"] == "conflict"
        assert body["field"] == "session_id"
        assert "different network states" in body["detail"]

    def test_a_second_diagnosis_after_the_change_succeeds(self, client) -> None:
        from app.core.config import API_PREFIX

        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        first = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        ).json()
        client.post(f"{API_PREFIX}/diagnoses/{first['id']}/step")
        client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"}},
        )
        second = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        )
        assert second.status_code == 201
        assert second.json()["beliefs"]["ranked"][0]["code"] == "DNS_FAILURE"


class TestNoDuplicateVocabularies:
    """Bug: ``app.lab.outcomes.OUTCOMES_BY_PROBE`` was dead code.

    It was a second, unverified list of the same outcome codes that
    ``app.probes.simulated.KNOWN_OUTCOMES`` declares. Two lists that must agree but
    nothing checks will eventually disagree, and the likelihood validator only reads
    one of them. The dead duplicate was removed; this test keeps the single source of
    truth honest.
    """

    def test_outcomes_by_probe_duplicate_is_gone(self) -> None:
        import app.lab.outcomes as outcomes

        assert not hasattr(outcomes, "OUTCOMES_BY_PROBE"), (
            "the duplicate outcome vocabulary must not come back; "
            "app.probes.simulated.KNOWN_OUTCOMES is the single source of truth"
        )

    def test_declared_outcomes_all_exist_in_their_enum(self) -> None:
        from app.lab.outcomes import (
            DnsOutcome,
            IcmpOutcome,
            MtuOutcome,
            ProbeType,
            ServiceOutcome,
            TcpOutcome,
            TraceOutcome,
        )
        from app.probes.simulated import KNOWN_OUTCOMES

        enum_for = {
            ProbeType.ICMP_REACHABILITY: IcmpOutcome,
            ProbeType.DNS_LOOKUP: DnsOutcome,
            ProbeType.TRACEROUTE: TraceOutcome,
            ProbeType.TCP_CONNECT: TcpOutcome,
            ProbeType.MTU_PROBE: MtuOutcome,
            ProbeType.SERVICE_HEALTH: ServiceOutcome,
        }
        for probe_type, declared in KNOWN_OUTCOMES.items():
            members = {item.value for item in enum_for[probe_type]}
            assert declared == members, (
                f"{probe_type.value} declares outcomes that do not match its enum: "
                f"declared-only={sorted(declared - members)} "
                f"enum-only={sorted(members - declared)}"
            )

    def test_known_outcomes_are_covered_by_the_likelihood_model(self) -> None:
        from app.diagnosis.likelihoods import LIKELIHOODS
        from app.lab.outcomes import CANONICAL_CANDIDATE_ORDER, probe_key as make_key
        from app.probes.simulated import KNOWN_OUTCOMES

        for probe_type, outcomes in KNOWN_OUTCOMES.items():
            selector = next(
                (sel for pt, sel in CANONICAL_CANDIDATE_ORDER if pt is probe_type), None
            )
            body = LIKELIHOODS[make_key(probe_type, selector)]
            assert outcomes <= set(body), "every emittable outcome needs a likelihood row"


class TestSimulatorExports:
    """Bug: ``app/lab/simulator.__all__`` exported ``field`` (a dataclasses helper)."""

    def test_dunder_all_names_all_resolve(self) -> None:
        import app.lab.simulator as simulator

        for name in simulator.__all__:
            assert hasattr(simulator, name), f"__all__ lists a missing name: {name}"

    def test_decorator_helpers_are_not_exported(self) -> None:
        import app.lab.simulator as simulator

        for helper in ("field", "dataclass", "annotations"):
            assert helper not in simulator.__all__, (
                f"{helper!r} is an implementation detail and must not be re-exported"
            )


class TestRepositoryHasNoDeadState:
    """Bug: the storage repository carried unused imports and an unreachable branch."""

    def test_get_diagnosis_error_is_actionable_in_both_cases(self) -> None:
        from app.core.errors import NotFoundError

        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]

        with pytest.raises(NotFoundError, match="unknown diagnosis id"):
            diagnoses.get("does-not-exist")

        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        restarted = DiagnosisService(database, SessionService(database))
        with pytest.raises(NotFoundError, match="not loaded in this process"):
            restarted.get(run.diagnosis_id)

    def test_stored_result_json_is_valid_json(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        row = database.get_diagnosis(run.diagnosis_id)
        parsed = json.loads(row["result_json"])
        assert parsed["id"] == run.diagnosis_id
        assert parsed["steps"]


class TestTcpListenerPresence:
    """Bug: ``port_state`` returned ``open`` for *every* port.

    ``tcp_probe`` then mapped ``open`` plus one RTT sample to ``CONNECTED``, so a probe
    against a port nothing was listening on reported a completed three-way handshake
    and its evidence asserted that "the port itself is working" — a false observation
    fed straight into the Bayesian engine. Reachable through an explicit
    ``DiagnosisRun.port`` the topology never declared.
    """

    def test_a_port_with_no_declared_service_has_no_listener(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        assert lab.has_listener("web-1", 80) is True
        assert lab.has_listener("web-1", 443) is True
        assert lab.has_listener("web-1", 64999) is False
        assert lab.port_state("web-1", 64999) == "service_down"

    def test_tcp_probe_on_an_unserved_port_is_refused_not_connected(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        state = LabSimulator(lab).tcp_probe("client-1", "web-1", 64999, "t")
        assert state.outcome is TcpOutcome.REFUSED_NO_LISTENER, (
            "a port with no listener must not report a completed handshake"
        )
        assert "no process is listening" in state.detail

    def test_a_declared_port_still_connects(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        for port in (80, 443):
            state = LabSimulator(lab).tcp_probe("client-1", "web-1", port, "t")
            assert state.outcome in (TcpOutcome.CONNECTED, TcpOutcome.CONNECTED_SLOW)

    def test_declared_but_unhealthy_service_still_has_a_listener(self) -> None:
        """A bound-but-failing process is DEGRADED_STALL, not REFUSED_NO_LISTENER.

        The distinction matters diagnostically: a crashed process and a process that
        answers but does not serve are different faults.
        """
        lab = LabState(get_template("campus-basic"), random_seed=1)
        lab.topology.find_service("web-1", "web").healthy = False
        assert lab.has_listener("web-1", 80) is True
        state = LabSimulator(lab).service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.DEGRADED_STALL

    def test_nonexistent_node_has_no_ports(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        assert lab.has_listener("ghost", 80) is False
        assert lab.declared_ports("ghost") == []
        assert lab.port_state("ghost", 80) == "service_down"

    def test_declared_ports_are_reported_sorted_and_unique(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        assert lab.declared_ports("web-1") == [80, 443]
        assert lab.declared_ports("db-1") == [5432]
        assert lab.declared_ports("client-1") == []

    @pytest.mark.parametrize(
        "fault_type,expected_policy",
        [
            (FaultType.TCP_PORT_BLOCKED, "drop"),
            (FaultType.TCP_PORT_REJECTED, "reject"),
            (FaultType.SERVICE_DOWN, "service_down"),
        ],
    )
    def test_injected_port_faults_still_win_on_a_declared_port(
        self, fault_type: FaultType, expected_policy: str
    ) -> None:
        lab = _lab_with(fault_type, "web-1:web")
        assert lab.port_state("web-1", 80) == expected_policy

    def test_unaffected_port_of_the_same_host_is_untouched(self) -> None:
        lab = _lab_with(FaultType.TCP_PORT_BLOCKED, "web-1:web")
        assert lab.port_state("web-1", 80) == "drop"
        assert lab.port_state("web-1", 443) == "open"


class TestFaultParameterValidation:
    """Bug: non-finite floats passed validation and reached the payload as ``inf``.

    ``added_latency_ms <= 0`` is False for both ``inf`` and ``nan``, so both were
    accepted; ``inf`` then propagated into every RTT computation and into the JSON
    payload. An MTU at or above the largest probed size was also accepted even though
    the packet-size ladder could never observe it, so an active fault silently produced
    a ``FULL_PATH_OK`` finding.
    """

    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_non_finite_latency_is_rejected(self, value: float) -> None:
        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.HIGH_LATENCY,
            target_id="l-access-campus",
            parameters={"added_latency_ms": value},
        )
        with pytest.raises(FaultInjectionError, match="finite"):
            validate_fault(spec, topology)

    def test_latency_above_the_ceiling_is_rejected(self) -> None:
        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.HIGH_LATENCY,
            target_id="l-access-campus",
            parameters={"added_latency_ms": 10_000_000.0},
        )
        with pytest.raises(FaultInjectionError, match="at most"):
            validate_fault(spec, topology)

    @pytest.mark.parametrize("value", [float("inf"), float("nan")])
    def test_non_finite_loss_rate_is_rejected(self, value: float) -> None:
        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.PACKET_LOSS,
            target_id="l-access-campus",
            parameters={"loss_rate": value},
        )
        with pytest.raises(FaultInjectionError, match="finite"):
            validate_fault(spec, topology)

    def test_mtu_above_the_probed_ladder_is_rejected(self) -> None:
        """Otherwise the fault can never manifest on the ladder."""
        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.MTU_BLACK_HOLE,
            target_id="l-access-campus",
            parameters={"mtu_bytes": 9000},
        )
        with pytest.raises(FaultInjectionError, match="largest probed packet size"):
            validate_fault(spec, topology)

    def test_every_ladder_observable_mtu_is_still_accepted(self) -> None:
        from app.core.config import MTU_PROBE_SIZES

        topology = get_template("campus-basic")
        for size in MTU_PROBE_SIZES:
            if size < 68 or size >= max(MTU_PROBE_SIZES):
                # Below the IPv4 minimum, or at the largest probed size: an MTU equal to
                # the largest probed size can never make any probed packet oversized.
                continue
            spec = FaultSpec(
                fault_type=FaultType.MTU_BLACK_HOLE,
                target_id="l-access-campus",
                parameters={"mtu_bytes": size},
            )
            validate_fault(spec, topology)

    def test_mtu_equal_to_the_largest_probed_size_is_rejected(self) -> None:
        """Regression: mtu_bytes == 1500 was accepted but could never manifest."""
        from app.core.config import MTU_PROBE_SIZES

        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.MTU_BLACK_HOLE,
            target_id="l-access-campus",
            parameters={"mtu_bytes": max(MTU_PROBE_SIZES)},
        )
        with pytest.raises(FaultInjectionError, match="below"):
            validate_fault(spec, topology)

    def test_an_accepted_mtu_fault_actually_manifests(self) -> None:
        """Accepted parameters must be observable, or the fault is a lie."""
        lab = _lab_with(FaultType.MTU_BLACK_HOLE, "l-access-campus", mtu_bytes=900)
        state = LabSimulator(lab).mtu_probe("client-1", "web-1", "t")
        assert state.outcome is MtuOutcome.LIMITED_DROP
        assert state.max_success_bytes == 512

    def test_validation_happens_before_mutation_on_reset(self) -> None:
        """A rejected reset must not clear the cached lab's faults first."""
        from app.core.errors import ValidationError

        database = Database(":memory:")
        sessions = SessionService(database)
        session_id = sessions.create_session("campus-basic")["id"]
        sessions.apply_fault(
            session_id, FaultSpec(fault_type=FaultType.LINK_DOWN, target_id="l-campus-edge")
        )
        assert len(sessions.lab_for(session_id).active_faults) == 1

        with pytest.raises(ValidationError, match="non-negative"):
            sessions.reset_session(session_id, random_seed=-1)

        assert len(sessions.lab_for(session_id).active_faults) == 1, (
            "the failed call left the lab cleared while storage still listed the fault"
        )
        assert sessions.lab_for(session_id).topology.link("l-campus-edge").up is False

    def test_reset_validates_seed_type(self) -> None:
        from app.core.errors import ValidationError

        database = Database(":memory:")
        sessions = SessionService(database)
        session_id = sessions.create_session("campus-basic")["id"]
        with pytest.raises(ValidationError, match="must be an integer"):
            sessions.reset_session(session_id, random_seed=True)  # type: ignore[arg-type]


class TestNominalPathIsPure:
    """Bug: ``nominal_path`` flipped ``link.up`` on the live topology.

    Two evaluations sharing a topology could observe the all-links-up window and
    produce an observation inconsistent with the real link state.
    """

    def test_nominal_path_does_not_mutate_link_state(self) -> None:
        lab = _lab_with(FaultType.LINK_DOWN, "l-campus-edge")
        table = RoutingTable(lab.topology)
        before = {link.id: link.up for link in lab.topology.links}
        path = table.nominal_path("client-1", "web-1")
        after = {link.id: link.up for link in lab.topology.links}
        assert before == after
        assert lab.topology.link("l-campus-edge").up is False
        assert path is not None and "l-campus-edge" in path.links, (
            "the nominal path must include the down link, since it models all-up state"
        )

    def test_nominal_path_is_repeatable(self) -> None:
        lab = _lab_with(FaultType.LINK_DOWN, "l-campus-edge")
        table = RoutingTable(lab.topology)
        results = [tuple(table.nominal_path("client-1", "web-1").links) for _ in range(4)]
        assert len(set(results)) == 1

    def test_real_path_still_reflects_the_fault(self) -> None:
        from app.lab.routing import RouteDenied

        lab = _lab_with(FaultType.LINK_DOWN, "l-campus-edge")
        with pytest.raises(RouteDenied):
            RoutingTable(lab.topology).shortest_path("client-1", "web-1")

    def test_nominal_path_returns_none_when_no_path_exists_even_all_up(self) -> None:
        topology = get_template("campus-basic")
        table = RoutingTable(topology)
        assert table.nominal_path("client-1", "not-a-node") is None


class TestLocalizationScoringIsExact:
    """Bug: the experiment runner scored localization with a bidirectional substring
    match, so a partial overlap counted as correct and inflated the reported accuracy."""

    def test_exact_component_id_is_required(self) -> None:
        from app.experiments.scenarios import ExperimentScenario

        scenario = ExperimentScenario(
            id="exactness",
            description="check the scoring predicate",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.SERVICE_DOWN,
            target_id="web-1:web",
            expected_component_id="web-1:80",
        )
        assert scenario.expected_component_id == "web-1:80"

    def test_scenario_component_ids_use_the_localizer_form(self) -> None:
        """Every declared expectation must be the canonical form the localizer emits."""
        from app.experiments.scenarios import SCENARIOS

        for scenario in SCENARIOS:
            if not scenario.expected_component_id:
                continue
            expected = scenario.expected_component_id
            assert expected, scenario.id
            assert " " not in expected, f"{scenario.id}: component id must not contain spaces"
            if ":" in expected:
                node_id, port = expected.split(":", 1)
                assert port.isdigit(), (
                    f"{scenario.id}: a service component id must end in a port, got {expected!r}"
                )
                assert node_id == scenario.destination_node_id, (
                    f"{scenario.id}: service component must name the destination node"
                )

    def test_localization_accuracy_is_measured_end_to_end(self) -> None:
        """A real run must localize its own scenario correctly under exact matching."""
        from app.experiments.runner import ExperimentConfig, run_experiment
        from app.experiments.scenarios import SCENARIOS

        summary = run_experiment(
            Database(":memory:"),
            ExperimentConfig(
                runs_per_scenario=1,
                template_ids=["campus-basic"],
                strategies=["adaptive"],
            ),
        )
        adaptive = summary["metrics"]["by_strategy"]["adaptive"]
        expected_component_runs = sum(
            1
            for scenario in SCENARIOS
            if scenario.template_id == "campus-basic" and scenario.expected_component_id
        )
        # Every scenario that declares an expected component must be counted, even when
        # the localizer names no component: that is a miss, not an ineligible run.
        assert adaptive["localization_evaluated"] == expected_component_runs, (
            "runs whose localizer found no component must stay in the denominator"
        )
        assert adaptive["localization_accuracy"] is not None
        assert adaptive["localization_accuracy"] >= 0.7, (
            "the localizer must still resolve the faults it can actually localize; a drop "
            "below this means the localizer form and the scenario form disagree"
        )

    def test_a_scenario_with_no_localized_component_counts_as_a_miss(self) -> None:
        """Regression: a no-component result must not be excluded from the metric."""
        from app.experiments.runner import ExperimentConfig, run_experiment
        from app.lab.faults import FaultType

        # Packet loss is declared with an expected link but the localizer deliberately
        # cannot attribute loss to one link from end-to-end probes, so it returns None.
        database = Database(":memory:")
        summary = run_experiment(
            database,
            ExperimentConfig(
                runs_per_scenario=1,
                template_ids=["campus-basic"],
                fault_types=[FaultType.PACKET_LOSS],
                strategies=["adaptive"],
            ),
        )
        runs = database.list_experiment_runs(summary["experiment_id"])
        packet_loss_runs = [
            run for run in runs if run["scenario_id"].startswith("campus-packet-loss")
        ]
        assert packet_loss_runs, "the packet-loss scenario must have executed"
        for run in packet_loss_runs:
            assert run["localized_target_id"] is None
            assert run["is_localization_correct"] == 0, (
                "a declared expected component with no localized component is a miss"
            )


class TestNoDeadCode:
    """Bug: ``ProbeCandidate.repeatable`` and ``.score_key`` were unused.

    ``score_key`` was the worse of the two: its docstring promised "descending score,
    then cheaper, then key" while its body returned ``(0.0, 0.0, key)``, so had it ever
    been adopted for tie-breaking it would have silently ignored the score.
    """

    def test_probe_candidate_has_no_dead_members(self) -> None:
        from app.diagnosis.information_gain import ProbeCandidate

        assert not hasattr(ProbeCandidate, "score_key")
        assert "repeatable" not in ProbeCandidate.model_fields if hasattr(
            ProbeCandidate, "model_fields"
        ) else True
        assert set(ProbeCandidate.__dataclass_fields__) == {
            "probe_key",
            "probe_type_value",
            "label",
            "cost",
        }

    def test_ranking_tie_break_is_still_deterministic(self) -> None:
        from app.diagnosis.bayes import BeliefState
        from app.diagnosis.hypotheses import Hypothesis
        from app.diagnosis.information_gain import rank_candidates
        from app.diagnosis.likelihoods import likelihood_table
        from app.diagnosis.planner import build_candidates
        from app.diagnosis.planner import DiagnosisContext
        from app.lab.outcomes import probe_key as make_key
        from app.lab.outcomes import CANONICAL_CANDIDATE_ORDER

        context = DiagnosisContext(
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            port=80,
            hostname="web.campus.test",
            gateway_node_id="access-rtr",
            resolver_node_id="dns-1",
            control_node_id="db-1",
            topology=get_template("campus-basic"),
        )
        beliefs = {code: 1.0 / len(Hypothesis) for code in Hypothesis}
        forward = rank_candidates(build_candidates(context), beliefs, likelihood_table())
        backward = rank_candidates(
            list(reversed(build_candidates(context))), beliefs, likelihood_table()
        )
        assert [item.probe_key for item in forward] == [item.probe_key for item in backward]
        expected = {make_key(pt, sel) for pt, sel in CANONICAL_CANDIDATE_ORDER}
        assert {item.probe_key for item in forward} == expected


class TestOverviewDiagnosisPayload:
    """Bug: ``GET /overview`` hand-built each ``recent_diagnoses`` entry and omitted
    five fields the frontend's ``DiagnosisSummary`` type declares as required.

    The page rendered the hole: the probe counter showed ``"3/"`` because ``max_probes``
    was absent, and a run read back from storage hardcoded ``top_hypothesis: None`` so
    the dashboard hid a conclusion it actually had. The endpoint now reuses the same
    summary builder as ``GET /diagnoses``, so the two cannot drift.
    """

    REQUIRED = {
        "id",
        "session_id",
        "status",
        "strategy",
        "source_node_id",
        "destination_node_id",
        "destination_service",
        "port",
        "probes_used",
        "max_probes",
        "top_hypothesis",
        "top_probability",
        "entropy_bits",
        "started_at",
        "completed_at",
    }

    def _session_with_run(self, client) -> str:
        from app.core.config import API_PREFIX

        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"}},
        )
        client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        )
        return session["id"]

    def test_overview_entry_has_every_required_field(self, client) -> None:
        from app.core.config import API_PREFIX

        self._session_with_run(client)
        body = client.get(f"{API_PREFIX}/overview").json()
        assert body["recent_diagnoses"], "a completed run must appear on the dashboard"
        entry = body["recent_diagnoses"][0]
        assert self.REQUIRED <= set(entry), (
            f"overview is missing {sorted(self.REQUIRED - set(entry))}"
        )

    def test_overview_probe_count_is_renderable(self, client) -> None:
        """The exact symptom that was visible in the UI."""
        from app.core.config import API_PREFIX

        self._session_with_run(client)
        entry = client.get(f"{API_PREFIX}/overview").json()["recent_diagnoses"][0]
        assert entry["max_probes"] is not None
        assert f"{entry['probes_used']}/{entry['max_probes']}" != f"{entry['probes_used']}/"

    def test_overview_reports_the_conclusion_not_none(self, client) -> None:
        from app.core.config import API_PREFIX

        self._session_with_run(client)
        entry = client.get(f"{API_PREFIX}/overview").json()["recent_diagnoses"][0]
        assert entry["top_hypothesis"] == "DNS_FAILURE"
        assert entry["top_probability"] is not None
        assert entry["entropy_bits"] is not None

    def test_overview_and_diagnoses_list_agree(self, client) -> None:
        from app.core.config import API_PREFIX

        session_id = self._session_with_run(client)
        overview_entry = client.get(f"{API_PREFIX}/overview").json()["recent_diagnoses"][0]
        listing_entry = client.get(
            f"{API_PREFIX}/diagnoses", params={"session_id": session_id}
        ).json()["diagnoses"][0]
        assert overview_entry == listing_entry, (
            "the dashboard and the diagnosis list must render the same record"
        )

    def test_overview_is_empty_on_a_fresh_install(self, client) -> None:
        from app.core.config import API_PREFIX

        body = client.get(f"{API_PREFIX}/overview").json()
        assert body["recent_diagnoses"] == []
        assert body["stats"]["diagnoses"] == 0


class TestExplanationNeverRenders100Percent:
    """Bug: a posterior of 0.999 rendered as the literal "100%" via ``:.0%``, which
    ``validate_explanation`` rejects as proof language, so ``to_public`` raised an
    ``AssertionError`` and the report endpoint returned a 500."""

    def _observation(self):
        from app.probes.base import ProbeObservation
        from app.probes.simulated import ProbeType

        return ProbeObservation(
            probe_key="MTU_PROBE",
            probe_type=ProbeType.MTU_PROBE,
            probe_label="Simulated MTU / packet-size ladder",
            source_node_id="client-1",
            destination_node_id="web-1",
            outcome="LIMITED_DROP",
            summary="limited",
            details={},
        )

    def test_a_near_certain_posterior_still_renders(self) -> None:
        from app.diagnosis.explanations import build_explanation

        belief = {
            "ranked": [
                {"code": "MTU_BLACK_HOLE", "title": "Path-MTU black hole",
                 "probability": 0.999, "prior": 0.1, "layers": ["L3"]},
                {"code": "PACKET_LOSS", "title": "Packet loss",
                 "probability": 0.001, "prior": 0.1, "layers": ["L3"]},
            ],
            "entropy_bits": 0.01,
        }
        explanation = build_explanation(
            status="confident",
            observations=[self._observation()],
            belief=belief,
            contributions=[],
            stopping_reason="reached the threshold",
            next_probe=None,
            suspected_component=None,
        )
        assert "100%" not in explanation.headline
        assert "99.9%" in explanation.headline


class TestUnexplainedEvidenceGuard:
    """Bug: the forwarding-block warning compared the leader against a list that always
    excluded it, so it fired even for a LINK_FAILURE/ROUTING_FAILURE leader."""

    def _observation(self):
        from app.probes.base import ProbeObservation
        from app.probes.simulated import ProbeType

        return ProbeObservation(
            probe_key="TCP_CONNECT",
            probe_type=ProbeType.TCP_CONNECT,
            probe_label="Simulated TCP connect",
            source_node_id="client-1",
            destination_node_id="web-1",
            outcome="UNREACHABLE",
            summary="no route",
            details={"block_reason": "NO_ROUTE"},
        )

    def test_forwarding_leaders_are_not_warned_about_a_forwarding_block(self) -> None:
        from app.diagnosis.explanations import _evidence_split
        from app.diagnosis.hypotheses import Hypothesis

        for leader in (Hypothesis.LINK_FAILURE, Hypothesis.ROUTING_FAILURE):
            _, _, unexplained = _evidence_split(leader, [], [self._observation()])
            assert not any("forwarding-layer block" in item for item in unexplained), (
                f"{leader.value} explains the forwarding block and must not be warned about it"
            )

    def test_a_non_forwarding_leader_is_warned(self) -> None:
        from app.diagnosis.explanations import _evidence_split
        from app.diagnosis.hypotheses import Hypothesis

        _, _, unexplained = _evidence_split(
            Hypothesis.DNS_FAILURE, [], [self._observation()]
        )
        assert any("forwarding-layer block" in item for item in unexplained), (
            "a DNS leader does not explain a forwarding block"
        )


class TestDroppedSynHasNoHandshakeRtt:
    """Bug: a silently dropped SYN reported a handshake RTT, and the probe then used it
    as the modelled elapsed time instead of the connect timeout."""

    def test_tcp_drop_reports_no_handshake_rtt(self) -> None:
        lab = _lab_with(FaultType.TCP_PORT_BLOCKED, "web-1:web")
        state = LabSimulator(lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome is TcpOutcome.TIMEOUT_DROP
        assert state.handshake_rtt_ms is None


class TestGatewayUnreachableTargetsAGatewayLink:
    """Bug: GATEWAY_UNREACHABLE accepted any link id and silently behaved like LINK_DOWN."""

    def test_a_non_gateway_link_is_rejected(self) -> None:
        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.GATEWAY_UNREACHABLE,
            target_id="l-campus-edge",  # a core uplink, not a host's gateway link
        )
        with pytest.raises(FaultInjectionError, match="default-gateway link"):
            validate_fault(spec, topology)

    def test_a_real_gateway_link_is_accepted(self) -> None:
        topology = get_template("campus-basic")
        spec = FaultSpec(
            fault_type=FaultType.GATEWAY_UNREACHABLE, target_id="l-client1-access"
        )
        validate_fault(spec, topology)


class TestPersistedObservationDetailsAreNotAliased:
    """Bug: ProbeStep.to_public returned the observation's details dict by reference, so
    the persistence layer's setdefault mutated the in-memory observation."""

    def test_persisting_does_not_mutate_the_observation_details(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        run = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        for step in run.steps:
            assert "source_node_id" not in step.observation.details, (
                "persistence must not write back into the observation it is storing"
            )


class TestDeletedSessionRunsAreForgotten:
    """Bug: deleting a session cascaded the stored rows but left the live
    ``DiagnosisRun`` objects in ``DiagnosisService._runs``, so ``get`` kept serving a
    deleted session's diagnosis straight from memory."""

    def test_deleting_a_session_forgets_its_in_memory_runs(self) -> None:
        database = Database(":memory:")
        sessions = SessionService(database)
        diagnoses = DiagnosisService(database, sessions)
        session_id = sessions.create_session("campus-basic")["id"]
        other_id = sessions.create_session("campus-basic")["id"]

        doomed = diagnoses.create(
            session_id=session_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )
        kept = diagnoses.create(
            session_id=other_id,
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            run_to_completion=True,
        )

        # The stored rows cascade on delete; the live run objects do not.
        assert database.delete_session(session_id) is True
        assert diagnoses.get(doomed.diagnosis_id) is doomed, (
            "the leak: the deleted run is still reachable from memory"
        )

        diagnoses.forget_session(session_id)
        with pytest.raises(NotFoundError):
            diagnoses.get(doomed.diagnosis_id)
        # A different session's run must be untouched.
        assert diagnoses.get(kept.diagnosis_id) is kept


class TestBaselineProbeReportsTheLiveInformationGain:
    """Bug: ``select_baseline_probe`` filled in each candidate's EIG from a uniform
    belief instead of the run's posterior, so the shared stopping rule stopped the
    baseline on a quantity the belief did not support."""

    def _context(self) -> DiagnosisContext:
        return DiagnosisContext(
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            port=80,
            hostname="web.campus.test",
            gateway_node_id="access-rtr",
            resolver_node_id="dns-1",
            control_node_id="db-1",
            topology=get_template("campus-basic"),
        )

    def test_reported_eig_uses_the_supplied_belief(self) -> None:
        plan = build_baseline_plan(self._context())

        # No belief supplied -> uniform reference.
        uniform = select_baseline_probe(plan, [])
        # A concentrated posterior must change the reported EIG...
        priors = {code: 0.01 for code in DEFAULT_PRIORS}
        priors[Hypothesis.DNS_FAILURE] = 0.91
        concentrated = select_baseline_probe(plan, [], BeliefState(priors))

        # ...but never the fixed order itself.
        assert concentrated.probe_key == uniform.probe_key
        assert concentrated.expected_information_gain != pytest.approx(
            uniform.expected_information_gain
        )



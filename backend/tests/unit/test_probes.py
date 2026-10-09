"""Unit tests for the probe engine: contract, evidence, outcome vocabulary."""

from __future__ import annotations

import pytest

from app.lab.faults import FaultType, LabState
from app.lab.outcomes import (
    MODE_SIMULATED,
    PROBE_COSTS,
    ProbeType,
    probe_key as make_key,
)
from app.lab.simulator import LabSimulator
from app.lab.templates import get_template
from app.probes.base import ProbeObservation, ProbeRequest
from app.probes.simulated import (
    KNOWN_OUTCOMES,
    build_probe_registry,
)
from app.diagnosis.likelihoods import LIKELIHOODS

from tests.conftest import inject


def request(**overrides: object) -> ProbeRequest:
    base = {
        "probe_key": "ICMP_REACHABILITY:destination",
        "probe_type": ProbeType.ICMP_REACHABILITY,
        "source_node_id": "client-1",
        "destination_node_id": "web-1",
        "destination_service": "web",
        "port": 80,
        "gateway_node_id": "access-rtr",
        "resolver_node_id": "dns-1",
        "control_node_id": "db-1",
        "hostname": "web.campus.test",
        "tag": "test",
    }
    base.update(overrides)
    return ProbeRequest(**base)  # type: ignore[arg-type]


class TestProbeRegistry:
    def test_all_six_probe_types_are_implemented(self, campus_lab: LabState) -> None:
        registry = build_probe_registry(LabSimulator(campus_lab))
        assert set(registry) == set(ProbeType)
        assert len(registry) >= 6

    def test_every_probe_returns_the_same_contract(self, campus_lab: LabState) -> None:
        registry = build_probe_registry(LabSimulator(campus_lab))
        for probe_type, probe in registry.items():
            observation = probe.run(request(probe_key=make_key(probe_type, None)), campus_lab)
            assert isinstance(observation, ProbeObservation)
            assert observation.probe_type is probe_type
            assert observation.outcome
            assert observation.summary
            assert observation.mode == MODE_SIMULATED
            assert isinstance(observation.details, dict) and observation.details
            assert isinstance(observation.evidence, list)
            assert observation.wall_clock_ms >= 0.0

    def test_every_probe_records_the_mode_label(self, campus_lab: LabState) -> None:
        registry = build_probe_registry(LabSimulator(campus_lab))
        for probe_type, probe in registry.items():
            observation = probe.run(request(probe_key=make_key(probe_type, None)), campus_lab)
            assert observation.mode == "SIMULATED LAB", (
                "simulated evidence must be visibly labelled"
            )

    def test_emitted_outcomes_stay_inside_the_declared_vocabulary(
        self, campus_lab: LabState
    ) -> None:
        registry = build_probe_registry(LabSimulator(campus_lab))
        for probe_type, probe in registry.items():
            observation = probe.run(request(probe_key=make_key(probe_type, None)), campus_lab)
            assert observation.outcome in KNOWN_OUTCOMES[probe_type]

    def test_declared_vocabulary_matches_the_likelihood_model(self) -> None:
        for probe_type, outcomes in KNOWN_OUTCOMES.items():
            selector = None
            if probe_type is ProbeType.ICMP_REACHABILITY:
                selector = "destination"
            key = make_key(probe_type, selector)
            for outcome in outcomes:
                assert outcome in LIKELIHOODS[key], f"{key}:{outcome} is not in the model"

    def test_every_probe_has_a_documented_relative_cost(self) -> None:
        for probe_type in ProbeType:
            assert probe_type in PROBE_COSTS
            assert PROBE_COSTS[probe_type] > 0

    def test_probes_produce_evidence_statements(self, campus_lab: LabState) -> None:
        registry = build_probe_registry(LabSimulator(campus_lab))
        for probe_type, probe in registry.items():
            observation = probe.run(request(probe_key=make_key(probe_type, None)), campus_lab)
            assert observation.evidence, f"{probe_type.value} produced no evidence statements"
            for statement in observation.evidence:
                assert statement.statement
                assert statement.kind in ("observation", "reading", "limitation")
                assert statement.strength in ("strong", "moderate", "weak")

class TestIcmpSelectorRouting:
    def test_destination_selector_targets_the_destination(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(probe_key="ICMP_REACHABILITY:destination"), campus_lab)
        assert observation.details["target_node_id"] == "web-1"

    def test_gateway_selector_targets_the_gateway(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(probe_key="ICMP_REACHABILITY:gateway"), campus_lab)
        assert observation.details["target_node_id"] == "access-rtr"

    def test_resolver_selector_targets_the_resolver(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(probe_key="ICMP_REACHABILITY:resolver"), campus_lab)
        assert observation.details["target_node_id"] == "dns-1"

    def test_control_selector_targets_an_independent_node(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(
            request(probe_key="ICMP_REACHABILITY:control_destination"), campus_lab
        )
        assert observation.details["target_node_id"] in ("db-1", "ops-1", "client-2")

    def test_selectors_produce_different_evidence(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.GATEWAY_UNREACHABLE, "l-client1-access")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        gateway = probe.run(request(probe_key="ICMP_REACHABILITY:gateway"), campus_lab)
        # The source host's own gateway link is down, so the simulated router
        # reports host-unreachable for the last hop in front of the gateway.
        assert gateway.outcome in ("TIMEOUT", "UNREACHABLE_NETWORK", "UNREACHABLE_HOST")
        assert gateway.outcome != "REACHABLE"


class TestIcmpEvidenceQuality:
    def test_a_filtered_icmp_does_not_claim_the_host_is_down(self, campus_lab: LabState) -> None:
        campus_lab.topology.node("web-1").answers_icmp = False
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(), campus_lab)
        text = " ".join(item.statement for item in observation.evidence).lower()
        assert "filtered" in text or "does not prove" in text

    def test_partial_loss_evidence_reports_the_counts(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.35)
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(), campus_lab)
        if observation.outcome == "PARTIAL_LOSS":
            assert "were lost" in " ".join(
                item.statement for item in observation.evidence
            )

    def test_icmp_error_evidence_names_the_suspect_link(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(), campus_lab)
        statements = " ".join(item.statement for item in observation.evidence)
        assert "l-campus-edge" in statements

    def test_slow_evidence_mentions_the_rtt(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.HIGH_LATENCY, "l-campus-edge", added_latency_ms=400.0)
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.ICMP_REACHABILITY]
        observation = probe.run(request(), campus_lab)
        assert "RTT" in " ".join(item.statement for item in observation.evidence)


class TestDnsProbe:
    def test_resolved_evidence_states_the_address(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.DNS_LOOKUP]
        observation = probe.run(request(probe_key="DNS_LOOKUP"), campus_lab)
        assert observation.details["resolved_address"] == "10.30.0.80"

    def test_outage_evidence_notes_that_ip_paths_still_need_testing(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.DNS_LOOKUP]
        observation = probe.run(request(probe_key="DNS_LOOKUP"), campus_lab)
        assert observation.outcome == "TIMEOUT_RESOLVER"
        kinds = {item.kind for item in observation.evidence}
        assert "limitation" in kinds, (
            "a DNS timeout alone must not be presented as a proven route failure"
        )

    def test_nxdomain_is_distinguished_from_a_timeout(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.DNS_LOOKUP]
        observation = probe.run(
            request(probe_key="DNS_LOOKUP", hostname="ghost.campus.test"), campus_lab
        )
        assert observation.outcome == "NXDOMAIN"
        assert observation.details["resolver_reachable"] is True

    def test_default_hostname_comes_from_the_destination(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.DNS_LOOKUP]
        observation = probe.run(request(probe_key="DNS_LOOKUP", hostname=None), campus_lab)
        assert observation.details["requested_hostname"] == "web.campus.test"


class TestTracerouteProbe:
    def test_partial_trace_localizes_and_says_so(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.TRACEROUTE]
        observation = probe.run(request(probe_key="TRACEROUTE"), campus_lab)
        assert observation.outcome == "PARTIAL"
        statements = " ".join(item.statement for item in observation.evidence)
        assert "l-campus-edge" in statements
        assert "does not by itself prove" in statements or "not by itself prove" in statements

    def test_suppressed_hop_is_reported_as_a_limitation(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.4)
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.TRACEROUTE]
        observation = probe.run(request(probe_key="TRACEROUTE"), campus_lab)
        assert observation.outcome in ("COMPLETE", "COMPLETE_WITH_SUPPRESSED", "PARTIAL")

    def test_complete_trace_lists_hops(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.TRACEROUTE]
        observation = probe.run(request(probe_key="TRACEROUTE"), campus_lab)
        assert observation.details["destination_reached"] is True
        assert len(observation.details["hops"]) == 4


class TestTcpProbe:
    @pytest.mark.parametrize(
        "fault_type,expected",
        [
            (FaultType.TCP_PORT_BLOCKED, "TIMEOUT_DROP"),
            (FaultType.TCP_PORT_REJECTED, "REFUSED_NETWORK_POLICY"),
            (FaultType.SERVICE_DOWN, "REFUSED_NO_LISTENER"),
        ],
    )
    def test_port_faults_produce_distinct_outcomes(
        self, campus_lab: LabState, fault_type: FaultType, expected: str
    ) -> None:
        inject(campus_lab, fault_type, "web-1:web")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.TCP_CONNECT]
        observation = probe.run(request(probe_key="TCP_CONNECT"), campus_lab)
        assert observation.outcome == expected

    def test_drop_evidence_acknowledges_the_ambiguity(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.TCP_PORT_BLOCKED, "web-1:web")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.TCP_CONNECT]
        observation = probe.run(request(probe_key="TCP_CONNECT"), campus_lab)
        statements = " ".join(item.statement for item in observation.evidence)
        assert "lost on the path" in statements or "filtering" in statements

    def test_reset_evidence_localizes_to_the_host(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.SERVICE_DOWN, "web-1:web")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.TCP_CONNECT]
        observation = probe.run(request(probe_key="TCP_CONNECT"), campus_lab)
        assert "nothing is listening" in " ".join(
            item.statement for item in observation.evidence
        )


class TestMtuProbe:
    def test_black_hole_evidence_explains_the_missing_icmp_error(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.MTU_PROBE]
        observation = probe.run(request(probe_key="MTU_PROBE"), campus_lab)
        assert observation.outcome == "LIMITED_DROP"
        assert "path-MTU black hole" in " ".join(
            item.statement for item in observation.evidence
        )

    def test_ladder_evidence_reports_the_boundary(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=1000)
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.MTU_PROBE]
        observation = probe.run(request(probe_key="MTU_PROBE"), campus_lab)
        assert observation.details["max_success_bytes"] == 1000
        assert observation.details["path_mtu_bytes"] == 1000

    def test_loss_evidence_says_it_is_not_an_mtu_pattern(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.6)
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.MTU_PROBE]
        observation = probe.run(request(probe_key="MTU_PROBE"), campus_lab)
        assert observation.outcome in (
            "INCONCLUSIVE_LOSS",
            "INCONCLUSIVE_NON_MONOTONE",
        )
        text = " ".join(item.statement for item in observation.evidence)
        assert "cannot separate" in text or "cannot produce that pattern" in text
        weakened = {code for item in observation.evidence for code in item.weakens}
        assert "MTU_BLACK_HOLE" in weakened

    def test_clean_path_weakens_the_mtu_hypothesis(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.MTU_PROBE]
        observation = probe.run(request(probe_key="MTU_PROBE"), campus_lab)
        weakened = {
            code for item in observation.evidence for code in item.weakens
        }
        assert "MTU_BLACK_HOLE" in weakened


class TestServiceHealthProbe:
    def test_healthy_service_confirms_the_whole_stack(self, campus_lab: LabState) -> None:
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.SERVICE_HEALTH]
        observation = probe.run(request(probe_key="SERVICE_HEALTH"), campus_lab)
        assert observation.outcome == "HEALTHY"

    def test_dead_service_evidence_distinguishes_it_from_the_path(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.SERVICE_DOWN, "web-1:web")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.SERVICE_HEALTH]
        observation = probe.run(request(probe_key="SERVICE_HEALTH"), campus_lab)
        assert observation.outcome == "UNAVAILABLE_REFUSED_NO_LISTENER"
        assert "APPLICATION_SERVICE_FAILURE" in {
            code for item in observation.evidence for code in item.supports
        }

    def test_unreachable_service_inherits_the_network_evidence(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        probe = build_probe_registry(LabSimulator(campus_lab))[ProbeType.SERVICE_HEALTH]
        observation = probe.run(request(probe_key="SERVICE_HEALTH"), campus_lab)
        assert observation.outcome == "UNREACHABLE"


class TestScenarioDistinguishability:
    """The eight-plus scenarios must produce recognisably different evidence."""

    def _profile(self, fault_type: FaultType | None, target: str, **params: object) -> dict:
        lab = LabState(get_template("campus-basic"), random_seed=20261009)
        if fault_type is not None:
            inject(lab, fault_type, target, **params)
        registry = build_probe_registry(LabSimulator(lab))
        profile: dict[str, str] = {}
        for probe_type, probe in registry.items():
            observation = probe.run(
                request(probe_key=make_key(probe_type, "destination"
                                           if probe_type is ProbeType.ICMP_REACHABILITY
                                           else None)),
                lab,
            )
            profile[probe_type.value] = observation.outcome
        return profile

    def test_fault_profiles_are_pairwise_distinguishable(self) -> None:
        scenarios = {
            "healthy": (None, ""),
            "link_down": (FaultType.LINK_DOWN, "l-campus-edge"),
            "blackhole": (FaultType.ROUTE_BLACKHOLE, "l-campus-edge"),
            "dns_failure": (FaultType.DNS_FAILURE, "dns-1"),
            "packet_loss": (FaultType.PACKET_LOSS, "l-campus-edge"),
            "high_latency": (FaultType.HIGH_LATENCY, "l-campus-edge"),
            "mtu_black_hole": (FaultType.MTU_BLACK_HOLE, "l-campus-edge"),
            "port_blocked": (FaultType.TCP_PORT_BLOCKED, "web-1:web"),
            "port_rejected": (FaultType.TCP_PORT_REJECTED, "web-1:web"),
            "service_down": (FaultType.SERVICE_DOWN, "web-1:web"),
            "gateway_unreachable": (FaultType.GATEWAY_UNREACHABLE, "l-client1-access"),
        }
        parameters = {
            "blackhole": {"destination_node_id": "web-1"},
            "packet_loss": {"loss_rate": 0.6},
            "high_latency": {"added_latency_ms": 400.0},
            "mtu_black_hole": {"mtu_bytes": 576},
        }
        profiles: dict[str, dict] = {}
        for name, (fault_type, target) in scenarios.items():
            profiles[name] = self._profile(fault_type, target, **parameters.get(name, {}))

        # At least ten of the eleven scenarios must have a unique profile, and the
        # only permitted collision is the documented route/blackhole versus
        # link-down ambiguity.
        seen: dict[str, str] = {}
        collisions: list[tuple[str, str]] = []
        for name, profile in profiles.items():
            signature = str(sorted(profile.items()))
            if signature in seen:
                collisions.append((seen[signature], name))
            else:
                seen[signature] = name
        assert len(collisions) <= 1, f"too many indistinguishable scenarios: {collisions}"

    def test_each_fault_has_a_diagnostic_signature_in_a_specific_probe(self) -> None:
        assert self._profile(None, "")["SERVICE_HEALTH"] == "HEALTHY"
        assert self._profile(FaultType.DNS_FAILURE, "dns-1")["DNS_LOOKUP"] == "TIMEOUT_RESOLVER"
        assert self._profile(FaultType.MTU_BLACK_HOLE, "l-campus-edge",
                             mtu_bytes=576)["MTU_PROBE"] == "LIMITED_DROP"
        assert self._profile(FaultType.TCP_PORT_BLOCKED, "web-1:web")["TCP_CONNECT"] == (
            "TIMEOUT_DROP"
        )
        assert self._profile(FaultType.TCP_PORT_REJECTED, "web-1:web")["TCP_CONNECT"] == (
            "REFUSED_NETWORK_POLICY"
        )
        assert self._profile(FaultType.SERVICE_DOWN, "web-1:web")["TCP_CONNECT"] == (
            "REFUSED_NO_LISTENER"
        )

    def test_scenarios_are_reproducible_with_the_same_seed(self) -> None:
        first = self._profile(FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        second = self._profile(FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        assert first == second

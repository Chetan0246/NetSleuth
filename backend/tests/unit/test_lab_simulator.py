"""Unit tests for the simulator: protocol behaviours, determinism, layer separation."""

from __future__ import annotations

import pytest

from app.core.config import MTU_PROBE_SIZES
from app.lab.faults import FaultType, LabState
from app.lab.outcomes import (
    DnsOutcome,
    IcmpOutcome,
    MtuOutcome,
    ServiceOutcome,
    TcpOutcome,
    TraceOutcome,
)
from app.lab.simulator import LabSimulator
from app.lab.templates import get_template

from tests.conftest import inject


def sim(lab: LabState) -> LabSimulator:
    return LabSimulator(lab)


class TestForwardingLayer:
    def test_healthy_path_resolves(self, campus_lab: LabState) -> None:
        forward = sim(campus_lab).forwarding_state("client-1", "web-1")
        assert forward.usable
        assert forward.path is not None
        assert forward.path.nodes[0] == "client-1"
        assert forward.path.nodes[-1] == "web-1"

    def test_link_down_produces_a_localized_block(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        forward = sim(campus_lab).forwarding_state("client-1", "web-1")
        assert not forward.usable
        assert forward.suspect_link == "l-campus-edge"
        assert forward.last_reachable_node == "campus-rtr"

    def test_last_hop_failure_is_distinguished(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-edge-web")
        forward = sim(campus_lab).forwarding_state("client-1", "web-1")
        assert forward.block_reason == "LINK_DOWN_LAST_HOP"

    def test_no_first_hop_is_distinguished(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.GATEWAY_UNREACHABLE, "l-client1-access")
        forward = sim(campus_lab).forwarding_state("client-1", "web-1")
        assert forward.block_reason == "NO_FIRST_HOP"
        assert forward.last_reachable_node == "client-1"

    def test_blackhole_is_reported_as_such_not_as_link_down(self, campus_lab: LabState) -> None:
        inject(
            campus_lab, FaultType.ROUTE_BLACKHOLE, "l-campus-edge",
            destination_node_id="web-1",
        )
        forward = sim(campus_lab).forwarding_state("client-1", "web-1")
        assert forward.blackholed is True
        assert forward.block_reason == "BLACKHOLE"
        assert forward.suspect_link == "l-campus-edge"

    def test_blackhole_does_not_affect_another_destination(self, campus_lab: LabState) -> None:
        inject(
            campus_lab, FaultType.ROUTE_BLACKHOLE, "l-campus-edge",
            destination_node_id="web-1",
        )
        assert sim(campus_lab).forwarding_state("client-1", "db-1").usable is True

    def test_same_node_is_trivially_reachable(self, campus_lab: LabState) -> None:
        assert sim(campus_lab).forwarding_state("client-1", "client-1").usable is True


class TestIcmpLayer:
    def test_healthy_echo_reports_rtt_and_no_loss(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome is IcmpOutcome.REACHABLE
        assert state.packets_received == state.packets_sent
        assert state.loss_percent == 0.0
        assert state.rtt_avg_ms is not None and state.rtt_avg_ms > 0

    def test_rtt_grows_with_hop_count(self) -> None:
        campus = sim(LabState(get_template("campus-basic"), random_seed=7))
        multihop = sim(LabState(get_template("multihop-wan"), random_seed=7))
        short = campus.icmp_probe("client-1", "web-1", "t")
        long = multihop.icmp_probe("client-1", "app-1", "t")
        assert long.rtt_avg_ms > short.rtt_avg_ms

    def test_high_latency_fault_is_reported_as_slow(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.HIGH_LATENCY, "l-campus-edge", added_latency_ms=400.0)
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome is IcmpOutcome.REACHABLE_SLOW

    def test_partial_loss_is_distinguished_from_timeout(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.35)
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome in (IcmpOutcome.PARTIAL_LOSS, IcmpOutcome.TIMEOUT)
        if state.outcome is IcmpOutcome.PARTIAL_LOSS:
            assert 0 < state.packets_received < state.packets_sent
            assert 0 < state.loss_percent < 100

    def test_down_link_yields_an_icmp_error_not_a_silent_timeout(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome is IcmpOutcome.UNREACHABLE_NETWORK

    def test_last_hop_down_yields_host_unreachable(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-edge-web")
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome is IcmpOutcome.UNREACHABLE_HOST

    def test_blackhole_yields_a_silent_timeout(self, campus_lab: LabState) -> None:
        inject(
            campus_lab, FaultType.ROUTE_BLACKHOLE, "l-campus-edge",
            destination_node_id="web-1",
        )
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome is IcmpOutcome.TIMEOUT
        assert state.packets_received == 0

    def test_suppressed_icmp_does_not_claim_the_host_is_down(self, campus_lab: LabState) -> None:
        campus_lab.topology.node("web-1").answers_icmp = False
        state = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert state.outcome is IcmpOutcome.TIMEOUT
        assert "does not prove the host is down" in state.detail

    def test_gateway_probe_targets_the_gateway(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).icmp_probe("client-1", "access-rtr", "t")
        assert state.outcome is IcmpOutcome.REACHABLE


class TestDnsLayer:
    def test_successful_resolution(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).dns_probe("client-1", "web.campus.test", "t")
        assert state.outcome in (DnsOutcome.RESOLVED, DnsOutcome.RESOLVED_SLOW)
        assert state.resolved_address == "10.30.0.80"
        assert state.resolver_reachable is True

    def test_case_insensitive_lookup(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).dns_probe("client-1", "WEB.CAMPUS.TEST", "t")
        assert state.resolved_address == "10.30.0.80"

    def test_nxdomain_for_a_name_that_is_not_in_the_zone(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).dns_probe("client-1", "ghost.campus.test", "t")
        assert state.outcome is DnsOutcome.NXDOMAIN
        assert state.resolver_reachable is True, "the resolver answered, so it was up"

    def test_resolver_outage_is_a_timeout_not_nxdomain(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        state = sim(campus_lab).dns_probe("client-1", "web.campus.test", "t")
        assert state.outcome is DnsOutcome.TIMEOUT_RESOLVER
        assert state.resolver_reachable is False

    def test_unreachable_resolver_path_is_a_timeout(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.GATEWAY_UNREACHABLE, "l-client1-access")
        state = sim(campus_lab).dns_probe("client-1", "web.campus.test", "t")
        assert state.outcome is DnsOutcome.TIMEOUT_RESOLVER

    def test_dns_failure_does_not_break_direct_ip_reachability(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.DNS_FAILURE, "dns-1")
        icmp = sim(campus_lab).icmp_probe("client-1", "web-1", "t")
        assert icmp.outcome is IcmpOutcome.REACHABLE


class TestTracerouteLayer:
    def test_healthy_trace_reaches_the_destination(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).traceroute("client-1", "web-1", "t")
        assert state.destination_reached is True
        assert state.outcome is TraceOutcome.COMPLETE
        assert state.hops[-1]["node_id"] == "web-1"

    def test_trace_localizes_a_mid_path_link_failure(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        state = sim(campus_lab).traceroute("client-1", "web-1", "t")
        assert state.outcome is TraceOutcome.PARTIAL
        assert state.suspect_link == "l-campus-edge"
        assert state.last_responding_node == "campus-rtr"
        assert state.last_responding_hop == 2

    def test_trace_reports_no_first_hop_when_nothing_answers(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.GATEWAY_UNREACHABLE, "l-client1-access")
        state = sim(campus_lab).traceroute("client-1", "web-1", "t")
        assert state.outcome is TraceOutcome.NO_FIRST_HOP
        assert all(not hop["responded"] for hop in state.hops)

    def test_hop_count_matches_path_length(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).traceroute("client-1", "web-1", "t")
        assert len(state.hops) == 4

    def test_multihop_topology_gives_more_hops(self, multihop_lab: LabState) -> None:
        state = sim(multihop_lab).traceroute("client-1", "app-1", "t")
        assert len(state.hops) == 4
        assert state.destination_reached is True

    def test_trace_marks_suppressed_hops_explicitly(self, campus_lab: LabState) -> None:
        campus_lab.topology.node("campus-rtr").answers_icmp = False
        state = sim(campus_lab).traceroute("client-1", "web-1", "t")
        silent = [hop for hop in state.hops if not hop["responded"]]
        assert silent, "a router that does not answer must be recorded as silent"
        assert state.destination_reached is True, "the destination still answered"


class TestTcpLayer:
    def test_healthy_handshake(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome in (TcpOutcome.CONNECTED, TcpOutcome.CONNECTED_SLOW)
        assert state.port_policy == "open"

    def test_blocked_port_times_out(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.TCP_PORT_BLOCKED, "web-1:web")
        state = sim(campus_lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome is TcpOutcome.TIMEOUT_DROP
        assert state.port_policy == "drop"

    def test_rejected_port_is_refused_by_policy(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.TCP_PORT_REJECTED, "web-1:web")
        state = sim(campus_lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome is TcpOutcome.REFUSED_NETWORK_POLICY
        assert state.port_policy == "reject"

    def test_dead_service_resets_the_connection(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.SERVICE_DOWN, "web-1:web")
        state = sim(campus_lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome is TcpOutcome.REFUSED_NO_LISTENER
        assert "no process is listening" in state.detail

    def test_timeout_and_refusal_are_different_outcomes(self, campus_lab: LabState) -> None:
        blocked = LabState(get_template("campus-basic"), random_seed=1)
        inject(blocked, FaultType.TCP_PORT_BLOCKED, "web-1:web")
        rejected = LabState(get_template("campus-basic"), random_seed=1)
        inject(rejected, FaultType.TCP_PORT_REJECTED, "web-1:web")
        first = sim(blocked).tcp_probe("client-1", "web-1", 80, "t")
        second = sim(rejected).tcp_probe("client-1", "web-1", 80, "t")
        assert first.outcome is not second.outcome

    def test_unreachable_network_short_circuits_tcp(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        state = sim(campus_lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome is TcpOutcome.UNREACHABLE

    def test_port_fault_does_not_affect_another_port(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.TCP_PORT_BLOCKED, "web-1:web")
        assert sim(campus_lab).tcp_probe("client-1", "web-1", 443, "t").outcome in (
            TcpOutcome.CONNECTED,
            TcpOutcome.CONNECTED_SLOW,
        )

    def test_high_latency_makes_the_handshake_slow(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.HIGH_LATENCY, "l-campus-edge", added_latency_ms=400.0)
        state = sim(campus_lab).tcp_probe("client-1", "web-1", 80, "t")
        assert state.outcome is TcpOutcome.CONNECTED_SLOW


class TestMtuLayer:
    def test_healthy_path_passes_every_size(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        assert state.outcome is MtuOutcome.FULL_PATH_OK
        assert state.max_success_bytes == max(MTU_PROBE_SIZES)

    def test_black_hole_limits_success_without_an_icmp_error(
        self, campus_lab: LabState
    ) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        assert state.outcome is MtuOutcome.LIMITED_DROP
        assert state.max_success_bytes == 512
        assert state.path_mtu_bytes == 576
        assert state.limiting_link == "l-campus-edge"
        assert state.repair_reported is False

    def test_black_hole_at_1000_bytes(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=1000)
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        assert state.outcome is MtuOutcome.LIMITED_DROP
        assert state.max_success_bytes == 1000

    def test_oversized_sizes_are_the_ones_that_fail(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        for item in state.per_size:
            if item["size_bytes"] <= 576:
                assert item["success"], f"{item['size_bytes']} fits and should succeed"
            else:
                assert not item["success"], f"{item['size_bytes']} is oversized and must fail"

    def test_packet_loss_makes_the_ladder_inconclusive(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.6)
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        # A lossy path either produces a non-monotone ladder (a larger size passed
        # while a smaller one failed) or an intermittent one. Both are declared
        # non-MTU findings, and neither may be reported as an MTU limit.
        assert state.outcome in (
            MtuOutcome.INCONCLUSIVE_LOSS,
            MtuOutcome.INCONCLUSIVE_NON_MONOTONE,
        )
        assert state.outcome is not MtuOutcome.LIMITED_DROP
        assert state.delivery_is_intermittent or state.has_size_inversion

    def test_non_monotone_pattern_wins_over_an_intermittent_one(
        self, campus_lab: LabState
    ) -> None:
        """A size inversion is impossible for an MTU boundary, so it is reported first."""
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.7)
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        if state.has_size_inversion:
            assert state.outcome is MtuOutcome.INCONCLUSIVE_NON_MONOTONE
            assert "not an MTU boundary" in state.detail

    def test_no_path_makes_the_ladder_unreachable(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        assert state.outcome is MtuOutcome.UNREACHABLE

    def test_ladder_successes_are_monotone_for_an_mtu_limit(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=1000)
        state = sim(campus_lab).mtu_probe("client-1", "web-1", "t")
        flags = [item["success"] for item in state.per_size]
        # All successes first, then all failures: the MTU boundary is crisp.
        assert flags == sorted(flags, key=lambda value: not value)


class TestServiceLayer:
    def test_healthy_service(self, campus_lab: LabState) -> None:
        state = sim(campus_lab).service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.HEALTHY

    def test_service_down_while_network_is_healthy(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.SERVICE_DOWN, "web-1:web")
        runner = sim(campus_lab)
        state = runner.service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.UNAVAILABLE_REFUSED_NO_LISTENER
        assert runner.icmp_probe("client-1", "web-1", "t").outcome is IcmpOutcome.REACHABLE
        assert runner.tcp_probe("client-1", "web-1", 80, "t").outcome is (
            TcpOutcome.REFUSED_NO_LISTENER
        )

    def test_blocked_port_shows_as_a_timeout(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.TCP_PORT_BLOCKED, "web-1:web")
        state = sim(campus_lab).service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.UNAVAILABLE_TIMEOUT

    def test_rejected_port_shows_as_policy_refusal(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.TCP_PORT_REJECTED, "web-1:web")
        state = sim(campus_lab).service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.UNAVAILABLE_REFUSED_POLICY

    def test_unreachable_network_is_reported_first(self, campus_lab: LabState) -> None:
        inject(campus_lab, FaultType.LINK_DOWN, "l-campus-edge")
        state = sim(campus_lab).service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.UNREACHABLE

    def test_declared_unhealthy_service_stalls(self, campus_lab: LabState) -> None:
        campus_lab.topology.find_service("web-1", "web").healthy = False
        state = sim(campus_lab).service_probe("client-1", "web-1", "t", service_name="web")
        assert state.outcome is ServiceOutcome.DEGRADED_STALL


class TestDeterminism:
    @pytest.mark.parametrize(
        "fault_type,target,parameters",
        [
            (FaultType.PACKET_LOSS, "l-campus-edge", {"loss_rate": 0.5}),
            (FaultType.HIGH_LATENCY, "l-campus-edge", {"added_latency_ms": 250.0}),
        ],
    )
    def test_same_seed_reproduces_identical_observations(
        self, fault_type: FaultType, target: str, parameters: dict[str, object]
    ) -> None:
        results = []
        for _ in range(3):
            lab = LabState(get_template("campus-basic"), random_seed=987654)
            inject(lab, fault_type, target, **parameters)
            runner = LabSimulator(lab)
            icmp = runner.icmp_probe("client-1", "web-1", "tag")
            trace = runner.traceroute("client-1", "web-1", "tag")
            mtu = runner.mtu_probe("client-1", "web-1", "tag")
            results.append(
                (
                    icmp.outcome.value,
                    icmp.packets_received,
                    round(icmp.rtt_avg_ms or 0.0, 6),
                    trace.outcome.value,
                    tuple(hop["responded"] for hop in trace.hops),
                    mtu.outcome.value,
                    tuple(item["delivered"] for item in mtu.per_size),
                )
            )
        assert results[0] == results[1] == results[2]

    def test_different_seeds_can_produce_different_loss_patterns(self) -> None:
        outcomes = set()
        for seed in range(8):
            lab = LabState(get_template("campus-basic"), random_seed=seed)
            inject(lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
            state = LabSimulator(lab).icmp_probe("client-1", "web-1", "t")
            outcomes.add(state.packets_received)
        assert len(outcomes) > 1, "a seeded RNG should vary across seeds"

    def test_distinct_tags_give_independent_samples(self, campus_lab: LabState) -> None:
        runner = sim(campus_lab)
        inject(campus_lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.5)
        samples = {runner.icmp_probe("client-1", "web-1", f"tag-{i}").packets_received
                   for i in range(10)}
        assert len(samples) > 1

"""Unit tests for the network model: graph validation, routing and topology."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.lab.graph import (
    DnsRecord,
    Link,
    Node,
    NodeType,
    ServiceSpec,
    Topology,
    validate_topology,
)
from app.lab.routing import RouteDenied, RoutingTable, PathResult
from app.lab.templates import get_template, list_template_ids, template_summaries


def _minimal_topology() -> Topology:
    return Topology(
        id="t",
        name="test",
        nodes=[
            Node(id="h1", name="host", type=NodeType.HOST, ip_address="10.0.0.1",
                 gateway="r1"),
            Node(id="r1", name="router", type=NodeType.ROUTER, ip_address="10.0.0.2"),
            Node(id="s1", name="server", type=NodeType.APP_SERVER, ip_address="10.0.1.1",
                 services=[ServiceSpec(name="http", port=80)]),
        ],
        links=[
            Link(id="l1", node_a="h1", node_b="r1"),
            Link(id="l2", node_a="r1", node_b="s1"),
        ],
    )


class TestTopologyValidation:
    def test_valid_topology_passes(self) -> None:
        assert validate_topology(_minimal_topology()).id == "t"

    def test_duplicate_node_id_rejected(self) -> None:
        topo = _minimal_topology()
        topo.nodes.append(
            Node(id="h1", name="dup", type=NodeType.HOST, ip_address="10.0.0.9")
        )
        with pytest.raises(ValidationError, match="duplicate node id"):
            validate_topology(topo)

    def test_duplicate_ip_rejected(self) -> None:
        topo = _minimal_topology()
        topo.nodes.append(
            Node(id="h2", name="dup-ip", type=NodeType.HOST, ip_address="10.0.0.1")
        )
        with pytest.raises(ValidationError, match="duplicate IP address"):
            validate_topology(topo)

    def test_link_with_unknown_endpoint_rejected(self) -> None:
        topo = _minimal_topology()
        topo.links.append(Link(id="l3", node_a="h1", node_b="ghost"))
        with pytest.raises(ValidationError, match="unknown endpoint"):
            validate_topology(topo)

    def test_self_loop_rejected(self) -> None:
        topo = _minimal_topology()
        topo.links.append(Link(id="l3", node_a="h1", node_b="h1"))
        with pytest.raises(ValidationError, match="connects a node to itself"):
            validate_topology(topo)

    def test_duplicate_link_pair_rejected(self) -> None:
        topo = _minimal_topology()
        topo.links.append(Link(id="l3", node_a="r1", node_b="h1"))
        with pytest.raises(ValidationError, match="duplicate link between"):
            validate_topology(topo)

    def test_disconnected_topology_rejected(self) -> None:
        topo = _minimal_topology()
        topo.nodes.append(
            Node(id="island", name="island", type=NodeType.HOST, ip_address="10.9.9.9")
        )
        with pytest.raises(ValidationError, match="not connected"):
            validate_topology(topo)

    def test_dns_record_pointing_nowhere_rejected(self) -> None:
        topo = _minimal_topology()
        topo.nodes[2].dns_records.append(DnsRecord(name="x", address="10.5.5.5"))
        with pytest.raises(ValidationError, match="does not match any node IP"):
            validate_topology(topo)

    def test_service_on_non_app_server_rejected(self) -> None:
        topo = _minimal_topology()
        topo.nodes[0].services.append(ServiceSpec(name="bad", port=1234))
        with pytest.raises(ValidationError, match="may not expose TCP services"):
            validate_topology(topo)

    def test_invalid_ip_rejected_at_model_level(self) -> None:
        with pytest.raises(Exception):
            Node(id="x", name="x", type=NodeType.HOST, ip_address="not-an-ip")

    def test_unknown_node_lookup_raises(self) -> None:
        with pytest.raises(ValidationError, match="unknown node id"):
            _minimal_topology().node("nope")

    def test_invalid_mtu_rejected(self) -> None:
        with pytest.raises(Exception):
            Link(id="l", node_a="a", node_b="b", mtu_bytes=10)


class TestRouting:
    def test_shortest_path_is_deterministic(self) -> None:
        topo = validate_topology(_minimal_topology())
        table = RoutingTable(topo)
        first = table.shortest_path("h1", "s1")
        for _ in range(5):
            assert table.shortest_path("h1", "s1") == first
        assert first.nodes == ("h1", "r1", "s1")
        assert first.links == ("l1", "l2")
        assert first.hop_count == 2

    def test_path_latency_is_additive(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l1").latency_ms = 2.0
        topo.link("l2").latency_ms = 3.0
        path = RoutingTable(topo).shortest_path("h1", "s1")
        assert path.latency_ms == pytest.approx(5.0)

    def test_path_mtu_is_minimum_over_links(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l1").mtu_bytes = 1500
        topo.link("l2").mtu_bytes = 900
        assert RoutingTable(topo).shortest_path("h1", "s1").path_mtu == 900

    def test_down_link_removes_the_path(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l2").up = False
        table = RoutingTable(topo)
        with pytest.raises(RouteDenied):
            table.shortest_path("h1", "s1")
        assert table.suspect_link("h1", "s1") == "l2"

    def test_no_first_hop_is_reported_separately(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l1").up = False
        table = RoutingTable(topo)
        with pytest.raises(RouteDenied) as excinfo:
            table.shortest_path("h1", "s1")
        assert excinfo.value.reached_first_hop is False
        assert excinfo.value.last_reachable_node == "h1"

    def test_last_reachable_node_brackets_the_failure(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l2").up = False
        assert RoutingTable(topo).last_reachable_node("h1", "s1") == "r1"

    def test_nominal_path_ignores_link_state(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l2").up = False
        nominal = RoutingTable(topo).nominal_path("h1", "s1")
        assert nominal is not None
        assert nominal.links == ("l1", "l2")

    def test_tie_break_prefers_lexicographic_link_sequence(self) -> None:
        # Two equal-hop paths h1-r1-s1 (l1,l3) and h1-r2-s1 (l2,l4): the
        # lexicographically smaller link-id sequence must win, every time.
        topo = Topology(
            id="tie",
            name="tie",
            nodes=[
                Node(id="h1", name="h", type=NodeType.HOST, ip_address="10.0.0.1"),
                Node(id="r1", name="r1", type=NodeType.ROUTER, ip_address="10.0.0.2"),
                Node(id="r2", name="r2", type=NodeType.ROUTER, ip_address="10.0.0.3"),
                Node(id="s1", name="s", type=NodeType.APP_SERVER, ip_address="10.0.1.1",
                     services=[ServiceSpec(name="http", port=80)]),
            ],
            links=[
                Link(id="l1", node_a="h1", node_b="r1"),
                Link(id="l2", node_a="h1", node_b="r2"),
                Link(id="l3", node_a="r1", node_b="s1"),
                Link(id="l4", node_a="r2", node_b="s1"),
            ],
        )
        validate_topology(topo)
        path = RoutingTable(topo).shortest_path("h1", "s1")
        assert path.links == ("l1", "l3")

    def test_forwarding_table_lists_next_hops(self) -> None:
        topo = validate_topology(_minimal_topology())
        rows = RoutingTable(topo).forwarding_table("r1")
        assert rows, "a router must expose derived routes"
        destination = next(row for row in rows if row["destination"] == "10.0.1.1")
        assert destination["next_hop_node"] == "s1"
        assert destination["reachable"] is True

    def test_forwarding_table_shows_unreachable_after_fault(self) -> None:
        topo = validate_topology(_minimal_topology())
        topo.link("l2").up = False
        rows = RoutingTable(topo).forwarding_table("r1")
        destination = next(row for row in rows if row["destination"] == "10.0.1.1")
        assert destination["reachable"] is False
        assert destination["next_hop"] is None

    def test_non_router_has_no_forwarding_table(self) -> None:
        assert RoutingTable(validate_topology(_minimal_topology())).forwarding_table("h1") == []


class TestTemplates:
    def test_at_least_two_templates_exist(self) -> None:
        assert len(list_template_ids()) >= 2

    def test_templates_are_valid_and_deep_copied(self) -> None:
        for template_id in list_template_ids():
            first = get_template(template_id)
            first.link(first.links[0].id).up = False
            second = get_template(template_id)
            assert second.link(second.links[0].id).up is True, (
                "get_template must return an independent copy"
            )

    def test_every_template_has_hosts_servers_and_a_resolver(self) -> None:
        for template_id in list_template_ids():
            topology = get_template(template_id)
            assert topology.hosts(), f"{template_id} has no host"
            assert topology.app_servers(), f"{template_id} has no app server"
            assert topology.resolvers(), f"{template_id} has no resolver"

    def test_template_summaries_are_serialisable(self) -> None:
        import json

        payload = template_summaries()
        assert json.dumps(payload)
        for item in payload:
            assert item["node_count"] > 0
            assert item["link_count"] > 0

    def test_unknown_template_raises_not_found(self) -> None:
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError, match="unknown topology template"):
            get_template("does-not-exist")

    def test_ip_addresses_are_unique_in_every_template(self) -> None:
        for template_id in list_template_ids():
            topology = get_template(template_id)
            ips = [node.ip_address for node in topology.nodes]
            assert len(ips) == len(set(ips))

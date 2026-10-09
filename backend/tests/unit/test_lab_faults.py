"""Unit tests for the fault-injection model: validation, reversibility, targets."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.lab.faults import (
    FaultInjectionError,
    FaultSpec,
    FaultType,
    LabState,
    normalize_fault,
    validate_fault,
)
from app.lab.templates import get_template

from tests.conftest import inject


class TestFaultValidation:
    def test_all_ten_fault_types_are_defined(self) -> None:
        assert len(FaultType) == 10

    def test_link_fault_requires_a_real_link(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="unknown link id"):
            validate_fault(
                FaultSpec(fault_type=FaultType.LINK_DOWN, target_id="nope"), lab.topology
            )

    def test_dns_fault_must_target_a_resolver(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="must target a dns_server"):
            validate_fault(
                FaultSpec(fault_type=FaultType.DNS_FAILURE, target_id="web-1"),
                lab.topology,
            )

    def test_service_target_requires_node_colon_service(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="node_id:service_name"):
            validate_fault(
                FaultSpec(fault_type=FaultType.SERVICE_DOWN, target_id="web-1"),
                lab.topology,
            )

    def test_service_target_with_unknown_service_lists_available(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="available: web, https"):
            validate_fault(
                FaultSpec(fault_type=FaultType.SERVICE_DOWN, target_id="web-1:ssh"),
                lab.topology,
            )

    def test_blackhole_requires_destination_parameter(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="destination_node_id"):
            validate_fault(
                FaultSpec(fault_type=FaultType.ROUTE_BLACKHOLE, target_id="l-campus-edge"),
                lab.topology,
            )

    def test_blackhole_rejects_destination_that_is_a_link_endpoint(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="may not be an endpoint"):
            validate_fault(
                FaultSpec(
                    fault_type=FaultType.ROUTE_BLACKHOLE,
                    target_id="l-campus-edge",
                    parameters={"destination_node_id": "campus-rtr"},
                ),
                lab.topology,
            )

    @pytest.mark.parametrize("rate", [0.0, -0.1, 2.0])
    def test_loss_rate_out_of_range_rejected(self, rate: float) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="loss_rate"):
            validate_fault(
                FaultSpec(
                    fault_type=FaultType.PACKET_LOSS,
                    target_id="l-campus-edge",
                    parameters={"loss_rate": rate},
                ),
                lab.topology,
            )

    def test_loss_rate_must_be_numeric(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="must be a number"):
            validate_fault(
                FaultSpec(
                    fault_type=FaultType.PACKET_LOSS,
                    target_id="l-campus-edge",
                    parameters={"loss_rate": "lots"},
                ),
                lab.topology,
            )

    def test_negative_latency_rejected(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="must be positive"):
            validate_fault(
                FaultSpec(
                    fault_type=FaultType.HIGH_LATENCY,
                    target_id="l-campus-edge",
                    parameters={"added_latency_ms": -5},
                ),
                lab.topology,
            )

    def test_mtu_below_minimum_rejected(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(FaultInjectionError, match="at least"):
            validate_fault(
                FaultSpec(
                    fault_type=FaultType.MTU_BLACK_HOLE,
                    target_id="l-campus-edge",
                    parameters={"mtu_bytes": 10},
                ),
                lab.topology,
            )

    def test_normalize_fills_documented_defaults(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        config = normalize_fault(
            FaultSpec(fault_type=FaultType.PACKET_LOSS, target_id="l-campus-edge"),
            lab.topology,
            "f1",
        )
        assert config.parameters["loss_rate"] == 0.5
        assert config.description, "a fault must carry a human-readable description"

    def test_normalize_auto_description_names_the_component(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        config = normalize_fault(
            FaultSpec(fault_type=FaultType.LINK_DOWN, target_id="l-campus-edge"),
            lab.topology,
            "f1",
        )
        assert "campus-rtr" in config.description
        assert "edge-rtr" in config.description


class TestLabStateReversibility:
    def test_link_down_is_fully_reversible(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        original = lab.topology.link("l-campus-edge").up
        inject(lab, FaultType.LINK_DOWN, "l-campus-edge")
        assert lab.topology.link("l-campus-edge").up is False
        lab.clear_faults()
        assert lab.topology.link("l-campus-edge").up is original

    def test_mtu_fault_restores_the_original_mtu(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        original = lab.topology.link("l-campus-edge").mtu_bytes
        inject(lab, FaultType.MTU_BLACK_HOLE, "l-campus-edge", mtu_bytes=576)
        assert lab.topology.link("l-campus-edge").mtu_bytes == 576
        assert lab.topology.link("l-campus-edge").suppress_frag_needed is True
        lab.clear_faults()
        assert lab.topology.link("l-campus-edge").mtu_bytes == original
        assert lab.topology.link("l-campus-edge").suppress_frag_needed is False

    def test_loss_override_is_reversible(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.PACKET_LOSS, "l-campus-edge", loss_rate=0.42)
        assert lab.link_loss(lab.topology.link("l-campus-edge")) == pytest.approx(0.42)
        lab.clear_faults()
        assert lab.link_loss(lab.topology.link("l-campus-edge")) == 0.0

    def test_latency_override_is_reversible(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.HIGH_LATENCY, "l-campus-edge", added_latency_ms=250.0)
        assert lab.topology.link("l-campus-edge").latency_ms == pytest.approx(250.0)
        lab.clear_faults()
        assert lab.topology.link("l-campus-edge").latency_ms == pytest.approx(3.0)

    def test_port_policy_is_reversible(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        assert lab.port_state("web-1", 80) == "open"
        inject(lab, FaultType.TCP_PORT_BLOCKED, "web-1:web")
        assert lab.port_state("web-1", 80) == "drop"
        lab.clear_faults()
        assert lab.port_state("web-1", 80) == "open"

    def test_service_down_state_is_reversible(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.SERVICE_DOWN, "web-1:web")
        assert lab.port_state("web-1", 80) == "service_down"
        assert lab.port_state("web-1", 443) == "open", "only the targeted port changes"
        lab.clear_faults()
        assert lab.port_state("web-1", 80) == "open"

    def test_toggle_active_flag_restores_state(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.LINK_DOWN, "l-campus-edge")
        fault_id = lab.active_faults[0].id
        lab.set_fault_active(fault_id, False)
        assert lab.topology.link("l-campus-edge").up is True
        assert lab.active_faults == []
        lab.set_fault_active(fault_id, True)
        assert lab.topology.link("l-campus-edge").up is False

    def test_remove_fault_returns_whether_it_existed(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.LINK_DOWN, "l-campus-edge")
        fault_id = lab.faults[0].id
        assert lab.remove_fault(fault_id) is True
        assert lab.remove_fault(fault_id) is False

    def test_set_fault_active_rejects_unknown_id(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        with pytest.raises(ValidationError, match="unknown fault id"):
            lab.set_fault_active("ghost", True)

    def test_blackhole_only_affects_its_destination(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(
            lab, FaultType.ROUTE_BLACKHOLE, "l-campus-edge",
            destination_node_id="web-1",
        )
        assert lab.is_blackholed("client-1", "web-1", "l-campus-edge") is True
        assert lab.is_blackholed("client-1", "db-1", "l-campus-edge") is False

    def test_dns_fault_disables_only_the_resolver(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.DNS_FAILURE, "dns-1")
        assert lab.topology.node("dns-1").resolver_enabled is False
        assert lab.topology.node("web-1").resolver_enabled is True

    def test_gateway_unreachable_helper_finds_the_gateway_link(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        assert lab.gateway_link_of("client-1") == "l-client1-access"

    def test_two_faults_can_be_active_together(self) -> None:
        lab = LabState(get_template("campus-basic"), random_seed=1)
        inject(lab, FaultType.LINK_DOWN, "l-campus-edge")
        inject(lab, FaultType.DNS_FAILURE, "dns-1")
        assert len(lab.active_faults) == 2
        assert lab.topology.link("l-campus-edge").up is False
        assert lab.topology.node("dns-1").resolver_enabled is False

    def test_application_order_is_deterministic(self) -> None:
        first = LabState(get_template("campus-basic"), random_seed=1)
        inject(first, FaultType.LINK_DOWN, "l-campus-edge")
        inject(first, FaultType.PACKET_LOSS, "l-access-campus", loss_rate=0.3)
        second = LabState(get_template("campus-basic"), random_seed=1)
        inject(second, FaultType.PACKET_LOSS, "l-access-campus", loss_rate=0.3)
        inject(second, FaultType.LINK_DOWN, "l-campus-edge")
        assert first.link_loss(first.topology.link("l-access-campus")) == pytest.approx(0.3)
        assert second.link_loss(second.topology.link("l-access-campus")) == pytest.approx(0.3)
        assert first.topology.link("l-campus-edge").up is False
        assert second.topology.link("l-campus-edge").up is False


class TestWorksOnBothTemplates:
    @pytest.mark.parametrize("template_id", ["campus-basic", "multihop-wan"])
    def test_link_fault_works_on_every_template(self, template_id: str) -> None:
        lab = LabState(get_template(template_id), random_seed=1)
        link_id = lab.topology.links[0].id
        inject(lab, FaultType.LINK_DOWN, link_id)
        assert lab.topology.link(link_id).up is False

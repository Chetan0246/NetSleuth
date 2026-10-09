"""Ground-truth scenario catalogue for the evaluation suite (plan.md section 10).

Ground truth lives **here and only here**. The evaluator reads it after a run to
decide whether the engine was right; the diagnostic engine never receives it
(``build_case`` returns the fault spec and the engine gets only the resulting lab
state, which it cannot distinguish from a user-injected fault).

Each case varies the target component and the configuration, so the suite is not a
single memorized example per fault class:

* link faults are injected on different links (near/far from the source),
* loss and latency faults use different magnitudes,
* the MTU fault uses two constrained sizes,
* DNS, port and service faults target different services (the web service and the
  database service are separate targets with different ports),
* both topologies are exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..lab.faults import FaultSpec, FaultType

#: Which hypothesis the injected fault is *expected* to explain. Used by the
#: evaluator to score top-1/top-3 accuracy, and never by the engine.
EXPECTED_HYPOTHESIS: dict[FaultType, str] = {
    FaultType.LINK_DOWN: "LINK_FAILURE",
    FaultType.GATEWAY_UNREACHABLE: "LINK_FAILURE",
    FaultType.ROUTE_BLACKHOLE: "ROUTING_FAILURE",
    FaultType.DNS_FAILURE: "DNS_FAILURE",
    FaultType.PACKET_LOSS: "PACKET_LOSS",
    FaultType.HIGH_LATENCY: "HIGH_LATENCY",
    FaultType.MTU_BLACK_HOLE: "MTU_BLACK_HOLE",
    FaultType.TCP_PORT_BLOCKED: "TCP_FILTER_OR_PORT_FAILURE",
    FaultType.TCP_PORT_REJECTED: "TCP_FILTER_OR_PORT_FAILURE",
    FaultType.SERVICE_DOWN: "APPLICATION_SERVICE_FAILURE",
}

#: Deliberately-accepted confusions, documented rather than hidden. ``A -> B``
#: means "a run whose ground truth is A may legitimately be diagnosed as B" and is
#: counted separately from a plain top-1 hit. Only pairs with a genuine protocol
#: reason belong here; anything else is a real error and must be reported as one.
ACCEPTED_CONFUSIONS: dict[str, frozenset[str]] = {
    # A down link and a withdrawn route are both "packets stop here"; the module
    # docstring of app.diagnosis.likelihoods explains why end-to-end probes can
    # leave them close together.
    "LINK_FAILURE": frozenset({"ROUTING_FAILURE"}),
    "ROUTING_FAILURE": frozenset({"LINK_FAILURE"}),
    # A filtered port and a dead service both refuse/reset the transport.
    "TCP_FILTER_OR_PORT_FAILURE": frozenset({"APPLICATION_SERVICE_FAILURE"}),
    "APPLICATION_SERVICE_FAILURE": frozenset({"TCP_FILTER_OR_PORT_FAILURE"}),
}


@dataclass(frozen=True)
class ExperimentScenario:
    """One reproducible fault case with its ground truth."""

    id: str
    description: str
    template_id: str
    source_node_id: str
    destination_node_id: str
    destination_service: str | None
    fault_type: FaultType | None
    target_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    #: For link/node-scoped faults, the component the localizer should name.
    expected_component_id: str | None = None

    @property
    def expected_hypothesis(self) -> str:
        if self.fault_type is None:
            return "NO_FAULT_DETECTED"
        return EXPECTED_HYPOTHESIS[self.fault_type]

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "template_id": self.template_id,
            "source_node_id": self.source_node_id,
            "destination_node_id": self.destination_node_id,
            "destination_service": self.destination_service,
            "fault_type": self.fault_type.value if self.fault_type else None,
            "target_id": self.target_id,
            "parameters": self.parameters,
            "expected_hypothesis": self.expected_hypothesis,
            "expected_component_id": self.expected_component_id,
        }

    def fault_spec(self) -> FaultSpec | None:
        if self.fault_type is None:
            return None
        return FaultSpec(
            fault_type=self.fault_type, target_id=self.target_id, parameters=self.parameters
        )


def _campus() -> list[ExperimentScenario]:
    return [
        ExperimentScenario(
            id="campus-nofault-web",
            description="Control: healthy campus path to the web service.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=None,
            target_id="",
        ),
        ExperimentScenario(
            id="campus-link-down-near",
            description="Access-to-campus uplink down (near the source).",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.LINK_DOWN,
            target_id="l-access-campus",
            expected_component_id="l-access-campus",
        ),
        ExperimentScenario(
            id="campus-link-down-far",
            description="Server-segment uplink down (near the destination).",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.LINK_DOWN,
            target_id="l-campus-edge",
            expected_component_id="l-campus-edge",
        ),
        ExperimentScenario(
            id="campus-route-blackhole",
            description="Route to the web server withdrawn on the campus uplink.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.ROUTE_BLACKHOLE,
            target_id="l-campus-edge",
            parameters={"destination_node_id": "web-1"},
            expected_component_id="l-campus-edge",
        ),
        ExperimentScenario(
            id="campus-dns-outage",
            description="Campus resolver unavailable.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.DNS_FAILURE,
            target_id="dns-1",
            expected_component_id="dns-1",
        ),
        ExperimentScenario(
            id="campus-packet-loss-moderate",
            description="Moderate packet loss on the campus uplink.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.PACKET_LOSS,
            target_id="l-access-campus",
            parameters={"loss_rate": 0.4},
            expected_component_id="l-access-campus",
        ),
        ExperimentScenario(
            id="campus-packet-loss-severe",
            description="Severe packet loss on the last-hop server link.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.PACKET_LOSS,
            target_id="l-edge-web",
            parameters={"loss_rate": 0.7},
            expected_component_id="l-edge-web",
        ),
        ExperimentScenario(
            id="campus-high-latency",
            description="400 ms added one-way delay on the campus uplink.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.HIGH_LATENCY,
            target_id="l-access-campus",
            parameters={"added_latency_ms": 400.0},
            expected_component_id="l-access-campus",
        ),
        ExperimentScenario(
            id="campus-mtu-576",
            description="Path-MTU black hole at 576 bytes on the campus uplink.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.MTU_BLACK_HOLE,
            target_id="l-access-campus",
            parameters={"mtu_bytes": 576},
            expected_component_id="l-access-campus",
        ),
        ExperimentScenario(
            id="campus-mtu-1000-db",
            description="Path-MTU black hole at 1000 bytes, targeting the database service.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="db-1",
            destination_service="postgres",
            fault_type=FaultType.MTU_BLACK_HOLE,
            target_id="l-campus-edge",
            parameters={"mtu_bytes": 1000},
            expected_component_id="l-campus-edge",
        ),
        ExperimentScenario(
            id="campus-port-blocked-web",
            description="Web port 80 silently dropped by a firewall policy.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.TCP_PORT_BLOCKED,
            target_id="web-1:web",
            expected_component_id="web-1:80",
        ),
        ExperimentScenario(
            id="campus-port-rejected-db",
            description="Database port 5432 actively rejected.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="db-1",
            destination_service="postgres",
            fault_type=FaultType.TCP_PORT_REJECTED,
            target_id="db-1:postgres",
            expected_component_id="db-1:5432",
        ),
        ExperimentScenario(
            id="campus-service-down-web",
            description="Web application process stopped while host and port stay reachable.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.SERVICE_DOWN,
            target_id="web-1:web",
            expected_component_id="web-1:80",
        ),
        ExperimentScenario(
            id="campus-gateway-unreachable",
            description="Client default-gateway link unusable.",
            template_id="campus-basic",
            source_node_id="client-1",
            destination_node_id="web-1",
            destination_service="web",
            fault_type=FaultType.GATEWAY_UNREACHABLE,
            target_id="l-client1-access",
            expected_component_id="l-client1-access",
        ),
    ]


def _multihop() -> list[ExperimentScenario]:
    return [
        ExperimentScenario(
            id="multihop-nofault-api",
            description="Control: healthy multi-hop path to the API service.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=None,
            target_id="",
        ),
        ExperimentScenario(
            id="multihop-link-down-core-branch",
            description="Core-to-branch link down, two hops from the destination.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=FaultType.LINK_DOWN,
            target_id="l-core-branch",
            expected_component_id="l-core-branch",
        ),
        ExperimentScenario(
            id="multihop-route-blackhole",
            description="Route to the API server withdrawn on the edge-to-core link.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=FaultType.ROUTE_BLACKHOLE,
            target_id="l-edge-core",
            parameters={"destination_node_id": "app-1"},
            expected_component_id="l-edge-core",
        ),
        ExperimentScenario(
            id="multihop-dns-outage",
            description="Edge resolver unavailable on the multi-hop topology.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=FaultType.DNS_FAILURE,
            target_id="dns-1",
            expected_component_id="dns-1",
        ),
        ExperimentScenario(
            id="multihop-latency-wan",
            description="900 ms added delay on the long-haul core-to-branch link.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=FaultType.HIGH_LATENCY,
            target_id="l-core-branch",
            parameters={"added_latency_ms": 900.0},
            expected_component_id="l-core-branch",
        ),
        ExperimentScenario(
            id="multihop-mtu-edge-core",
            description="Path-MTU black hole at 1000 bytes on the WAN link.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=FaultType.MTU_BLACK_HOLE,
            target_id="l-edge-core",
            parameters={"mtu_bytes": 1000},
            expected_component_id="l-edge-core",
        ),
        ExperimentScenario(
            id="multihop-port-blocked-admin",
            description="Administrative API port 9090 dropped.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="admin",
            fault_type=FaultType.TCP_PORT_BLOCKED,
            target_id="app-1:admin",
            expected_component_id="app-1:9090",
        ),
        ExperimentScenario(
            id="multihop-service-down-api",
            description="API process stopped on the multi-hop topology.",
            template_id="multihop-wan",
            source_node_id="client-1",
            destination_node_id="app-1",
            destination_service="api",
            fault_type=FaultType.SERVICE_DOWN,
            target_id="app-1:api",
            expected_component_id="app-1:8080",
        ),
    ]


SCENARIOS: list[ExperimentScenario] = _campus() + _multihop()

SCENARIOS_BY_ID: dict[str, ExperimentScenario] = {item.id: item for item in SCENARIOS}


def scenarios_for(
    *,
    template_ids: list[str] | None = None,
    fault_types: list[FaultType] | None = None,
    include_control: bool = True,
) -> list[ExperimentScenario]:
    """Filter the catalogue. Filters are validated, never silently ignored."""
    out = []
    for scenario in SCENARIOS:
        if template_ids and scenario.template_id not in template_ids:
            continue
        if scenario.fault_type is None:
            if not include_control:
                continue
        elif fault_types and scenario.fault_type not in fault_types:
            continue
        out.append(scenario)
    return out


def scenario_catalogue() -> dict[str, Any]:
    return {
        "scenarios": [item.to_public() for item in SCENARIOS],
        "fault_types": sorted({item.fault_type.value for item in SCENARIOS if item.fault_type}),
        "templates": sorted({item.template_id for item in SCENARIOS}),
    }


__all__ = [
    "ACCEPTED_CONFUSIONS",
    "EXPECTED_HYPOTHESIS",
    "SCENARIOS",
    "SCENARIOS_BY_ID",
    "ExperimentScenario",
    "scenario_catalogue",
    "scenarios_for",
]

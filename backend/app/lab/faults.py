"""Fault injection model — nine reversible, structured fault scenarios.

Each fault is a pydantic object with a unique id, a type, a target component,
typed parameters, an active flag and a human-readable description. Applying a
fault mutates the *lab state* (link flags, node flags, service flags). Removing
the fault restores the pristine state captured when the session was created, so
activation is fully reversible and every later probe sees the new state.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..core.config import MTU_MIN, MTU_PROBE_SIZES
from ..core.errors import ValidationError
from .graph import Link, Node, NodeType, ServiceSpec, Topology

#: Largest packet size the MTU ladder probes. A constrained MTU above this could
#: never change a ladder result, so it is rejected as a fault that cannot manifest.
MTU_LADDER_MAX = max(MTU_PROBE_SIZES)

#: Ceiling for an injected delay. `Simulator` durations and the UI both present
#: millisecond values, so an unbounded delay would be meaningless as well as unsafe.
MAX_FAULT_LATENCY_MS = 60_000.0


class FaultType(str, Enum):
    LINK_DOWN = "LINK_DOWN"
    ROUTE_BLACKHOLE = "ROUTE_BLACKHOLE"
    DNS_FAILURE = "DNS_FAILURE"
    PACKET_LOSS = "PACKET_LOSS"
    HIGH_LATENCY = "HIGH_LATENCY"
    MTU_BLACK_HOLE = "MTU_BLACK_HOLE"
    TCP_PORT_BLOCKED = "TCP_PORT_BLOCKED"
    TCP_PORT_REJECTED = "TCP_PORT_REJECTED"
    SERVICE_DOWN = "SERVICE_DOWN"
    GATEWAY_UNREACHABLE = "GATEWAY_UNREACHABLE"


class TargetKind(str, Enum):
    LINK = "link"
    NODE = "node"
    SERVICE = "service"


class FaultConfig(BaseModel):
    """A structured, reversible fault definition."""

    id: str
    fault_type: FaultType
    target_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = False
    description: str = ""

    # ---- helpers ---------------------------------------------------------
    @property
    def target_kind(self) -> TargetKind:
        return TARGET_KIND[self.fault_type]

    @property
    def target_label(self) -> str:
        return f"{self.target_kind.value}:{self.target_id}"

    def parameter_summary(self) -> str:
        if not self.parameters:
            return "no parameters"
        return ", ".join(f"{key}={value}" for key, value in sorted(self.parameters.items()))

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fault_type": self.fault_type.value,
            "target_id": self.target_id,
            "target_kind": self.target_kind.value,
            "parameters": self.parameters,
            "is_active": self.is_active,
            "description": self.description,
            "parameter_summary": self.parameter_summary(),
        }


class FaultSpec(BaseModel):
    """User input for activating one fault."""

    fault_type: FaultType
    target_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> "FaultSpec":
        if not self.target_id.strip():
            raise ValueError("target_id must not be empty")
        return self


#: Which kind of component each fault type targets.
TARGET_KIND: dict[FaultType, TargetKind] = {
    FaultType.LINK_DOWN: TargetKind.LINK,
    FaultType.ROUTE_BLACKHOLE: TargetKind.LINK,
    FaultType.DNS_FAILURE: TargetKind.NODE,
    FaultType.PACKET_LOSS: TargetKind.LINK,
    FaultType.HIGH_LATENCY: TargetKind.LINK,
    FaultType.MTU_BLACK_HOLE: TargetKind.LINK,
    FaultType.TCP_PORT_BLOCKED: TargetKind.SERVICE,
    FaultType.TCP_PORT_REJECTED: TargetKind.SERVICE,
    FaultType.SERVICE_DOWN: TargetKind.SERVICE,
    FaultType.GATEWAY_UNREACHABLE: TargetKind.LINK,
}

#: Fault types that can be injected on a *specific destination flow*. These
#: mutate per-service state and therefore need a service target.
FLOW_SCOPED: set[FaultType] = {
    FaultType.TCP_PORT_BLOCKED,
    FaultType.TCP_PORT_REJECTED,
    FaultType.SERVICE_DOWN,
}

FAULT_DESCRIPTIONS: dict[FaultType, str] = {
    FaultType.LINK_DOWN: "The link is physically down: no traffic traverses it in either direction and other hosts on the segment that only used this link also lose reachability.",
    FaultType.ROUTE_BLACKHOLE: "The link still carries traffic, but the router's derived forwarding entry for the destination is withdrawn (missing route / black hole), so only that destination becomes unreachable.",
    FaultType.DNS_FAILURE: "The resolver stops answering: names cannot be resolved while the IP path stays healthy.",
    FaultType.PACKET_LOSS: "The link drops packets at a configurable probability; small probes are intermittently lost, producing random timeouts rather than consistent failures.",
    FaultType.HIGH_LATENCY: "The link adds a controlled delay, so probes still succeed but RTTs rise well above the baseline.",
    FaultType.MTU_BLACK_HOLE: "The link's MTU is lowered and oversized DF=1 datagrams are silently dropped without an ICMP fragmentation-needed reply, so small probes succeed while large payloads fail.",
    FaultType.TCP_PORT_BLOCKED: "Traffic to the destination port is silently dropped (firewall DROP): the TCP handshake times out.",
    FaultType.TCP_PORT_REJECTED: "Traffic to the destination port is actively refused (firewall REJECT): the TCP handshake fails immediately with a reset.",
    FaultType.SERVICE_DOWN: "The host and port are reachable, but the application process is not serving requests.",
    FaultType.GATEWAY_UNREACHABLE: "The host's default-gateway link is unusable, so the local host cannot reach anything beyond its own subnet.",
}

PARAMETER_REFERENCE: list[dict[str, Any]] = [
    {"fault_type": FaultType.LINK_DOWN.value, "target_kind": "link", "parameters": {}},
    {
        "fault_type": FaultType.ROUTE_BLACKHOLE.value,
        "target_kind": "link",
        "parameters": {
            "destination_node_id": "required — the destination whose route is withdrawn",
        },
    },
    {"fault_type": FaultType.DNS_FAILURE.value, "target_kind": "node", "parameters": {}},
    {
        "fault_type": FaultType.PACKET_LOSS.value,
        "target_kind": "link",
        "parameters": {"loss_rate": "optional float in (0, 1], default 0.5"},
    },
    {
        "fault_type": FaultType.HIGH_LATENCY.value,
        "target_kind": "link",
        "parameters": {"added_latency_ms": "optional float, default 400.0"},
    },
    {
        "fault_type": FaultType.MTU_BLACK_HOLE.value,
        "target_kind": "link",
        "parameters": {"mtu_bytes": f"optional int >= {MTU_MIN}, default 576"},
    },
    {
        "fault_type": FaultType.TCP_PORT_BLOCKED.value,
        "target_kind": "service",
        "parameters": {"destination_node_id": "required — the node exposing the service"},
    },
    {
        "fault_type": FaultType.TCP_PORT_REJECTED.value,
        "target_kind": "service",
        "parameters": {"destination_node_id": "required"},
    },
    {
        "fault_type": FaultType.SERVICE_DOWN.value,
        "target_kind": "service",
        "parameters": {"destination_node_id": "required"},
    },
    {
        "fault_type": FaultType.GATEWAY_UNREACHABLE.value,
        "target_kind": "link",
        "parameters": {"note": "target_id is the default-gateway link of the client host"},
    },
]


class FaultInjectionError(ValidationError):
    pass


# ---------------------------------------------------------------------------
# validation + application
# ---------------------------------------------------------------------------


def _service_target(topology: Topology, target_id: str) -> tuple[Node, ServiceSpec]:
    """Parse a ``node_id:service_name`` service target."""
    if ":" not in target_id:
        raise FaultInjectionError(
            "service targets must use the form 'node_id:service_name'", field="target_id"
        )
    node_id, service_name = target_id.split(":", 1)
    if not topology.has_node(node_id):
        raise FaultInjectionError(f"unknown node id: {node_id}", field="target_id")
    node = topology.node(node_id)
    if node.type is not NodeType.APP_SERVER:
        raise FaultInjectionError(
            f"node {node_id} is a {node.type.value} and exposes no TCP services",
            field="target_id",
        )
    for svc in node.services:
        if svc.name == service_name:
            return node, svc
    available = ", ".join(svc.name for svc in node.services) or "none"
    raise FaultInjectionError(
        f"node {node_id} has no service named {service_name!r} (available: {available})",
        field="target_id",
    )


def validate_fault(spec: FaultSpec, topology: Topology) -> None:
    """Validate a fault request against the current topology."""
    kind = TARGET_KIND[spec.fault_type]
    if kind is TargetKind.LINK:
        if not topology.has_link(spec.target_id):
            raise FaultInjectionError(
                f"unknown link id: {spec.target_id} "
                f"(available: {', '.join(sorted(l.id for l in topology.links))})",
                field="target_id",
            )
    elif kind is TargetKind.NODE:
        if not topology.has_node(spec.target_id):
            raise FaultInjectionError(f"unknown node id: {spec.target_id}", field="target_id")
        node = topology.node(spec.target_id)
        if node.type is not NodeType.DNS_SERVER:
            raise FaultInjectionError(
                f"a DNS failure must target a dns_server node; {spec.target_id} is "
                f"a {node.type.value}",
                field="target_id",
            )
    else:
        _service_target(topology, spec.target_id)

    params = spec.parameters or {}

    if spec.fault_type is FaultType.ROUTE_BLACKHOLE:
        destination = params.get("destination_node_id")
        if not destination:
            raise FaultInjectionError(
                "ROUTE_BLACKHOLE requires the parameter 'destination_node_id'",
                field="parameters.destination_node_id",
            )
        if not topology.has_node(str(destination)):
            raise FaultInjectionError(
                f"unknown destination_node_id: {destination}",
                field="parameters.destination_node_id",
            )
        if str(destination) in topology.link(spec.target_id).endpoints():
            raise FaultInjectionError(
                "the black-holed destination may not be an endpoint of the affected link",
                field="parameters.destination_node_id",
            )

    if spec.fault_type is FaultType.PACKET_LOSS:
        rate = params.get("loss_rate", 0.5)
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            raise FaultInjectionError("loss_rate must be a number", field="parameters.loss_rate")
        if not math.isfinite(float(rate)):
            raise FaultInjectionError(
                "loss_rate must be a finite number", field="parameters.loss_rate"
            )
        if not 0.0 < float(rate) <= 1.0:
            raise FaultInjectionError(
                "loss_rate must be greater than 0 and at most 1", field="parameters.loss_rate"
            )

    if spec.fault_type is FaultType.HIGH_LATENCY:
        added = params.get("added_latency_ms", 400.0)
        if not isinstance(added, (int, float)) or isinstance(added, bool):
            raise FaultInjectionError(
                "added_latency_ms must be a number", field="parameters.added_latency_ms"
            )
        # `inf` and `nan` pass a `<= 0` test but then propagate into every RTT
        # computation and into the JSON payload as a non-serialisable value, so they
        # are rejected explicitly rather than left to a downstream encoder.
        if not math.isfinite(float(added)):
            raise FaultInjectionError(
                "added_latency_ms must be a finite number",
                field="parameters.added_latency_ms",
            )
        if float(added) <= 0:
            raise FaultInjectionError(
                "added_latency_ms must be positive", field="parameters.added_latency_ms"
            )
        if float(added) > MAX_FAULT_LATENCY_MS:
            raise FaultInjectionError(
                f"added_latency_ms must be at most {MAX_FAULT_LATENCY_MS:g}",
                field="parameters.added_latency_ms",
            )

    if spec.fault_type is FaultType.MTU_BLACK_HOLE:
        mtu = params.get("mtu_bytes", 576)
        if not isinstance(mtu, int) or isinstance(mtu, bool):
            raise FaultInjectionError("mtu_bytes must be an integer", field="parameters.mtu_bytes")
        if mtu < MTU_MIN:
            raise FaultInjectionError(
                f"mtu_bytes must be at least {MTU_MIN}", field="parameters.mtu_bytes"
            )
        # A constrained MTU at or above every probed size can never manifest: the
        # packet-size ladder would find every size "fitting" and report
        # FULL_PATH_OK while the fault is active. Bounding it to the largest probed
        # size keeps "injected fault" and "observable effect" equivalent.
        if mtu > MTU_LADDER_MAX:
            raise FaultInjectionError(
                f"mtu_bytes must be at most {MTU_LADDER_MAX} (the largest probed packet "
                "size); a higher value could never affect the packet-size ladder",
                field="parameters.mtu_bytes",
            )

    for flow_fault in (FaultType.TCP_PORT_BLOCKED, FaultType.TCP_PORT_REJECTED,
                       FaultType.SERVICE_DOWN):
        if spec.fault_type is flow_fault:
            destination = params.get("destination_node_id")
            if destination and not topology.has_node(str(destination)):
                raise FaultInjectionError(
                    f"unknown destination_node_id: {destination}",
                    field="parameters.destination_node_id",
                )


def normalize_fault(spec: FaultSpec, topology: Topology, fault_id: str) -> FaultConfig:
    """Validate and fill in defaults so the fault is fully self-describing."""
    validate_fault(spec, topology)
    params = dict(spec.parameters or {})
    if spec.fault_type is FaultType.PACKET_LOSS:
        params["loss_rate"] = float(params.get("loss_rate", 0.5))
    if spec.fault_type is FaultType.HIGH_LATENCY:
        params["added_latency_ms"] = float(params.get("added_latency_ms", 400.0))
    if spec.fault_type is FaultType.MTU_BLACK_HOLE:
        params["mtu_bytes"] = int(params.get("mtu_bytes", 576))
    description = spec.description or _auto_description(spec.fault_type, spec.target_id, params,
                                                        topology)
    return FaultConfig(
        id=fault_id,
        fault_type=spec.fault_type,
        target_id=spec.target_id,
        parameters=params,
        is_active=spec.is_active,
        description=description,
    )


def _auto_description(fault_type: FaultType, target_id: str, params: dict[str, Any],
                      topology: Topology) -> str:
    base = FAULT_DESCRIPTIONS[fault_type]
    label = target_id
    if TARGET_KIND[fault_type] is TargetKind.LINK:
        link = topology.link(target_id)
        label = f"{link.node_a} <-> {link.node_b}"
    elif TARGET_KIND[fault_type] is TargetKind.SERVICE:
        node_id, service = target_id.split(":", 1)
        label = f"{service} on {topology.node(node_id).name}"
    else:
        label = topology.node(target_id).name
    extra = ""
    if fault_type is FaultType.PACKET_LOSS:
        extra = f" Configured loss rate: {params.get('loss_rate')}."
    elif fault_type is FaultType.HIGH_LATENCY:
        extra = f" Added delay: {params.get('added_latency_ms')} ms one-way."
    elif fault_type is FaultType.MTU_BLACK_HOLE:
        extra = f" Constrained MTU: {params.get('mtu_bytes')} bytes."
    elif fault_type is FaultType.ROUTE_BLACKHOLE:
        destination = str(params.get("destination_node_id"))
        name = topology.node(destination).name if topology.has_node(destination) else destination
        extra = f" Black-holed destination: {name}."
    return f"{base} Target: {label}.{extra}"


# ---------------------------------------------------------------------------
# applying / removing faults against a live topology
# ---------------------------------------------------------------------------


class LabState:
    """Mutable simulation state: the topology plus the currently active faults.

    The pristine configuration captured at construction time is used to undo
    faults, which keeps activation strictly reversible.
    """

    def __init__(self, topology: Topology, *, random_seed: int,
                 faults: list[FaultConfig] | None = None) -> None:
        self.topology = topology
        self.random_seed = random_seed
        self.faults: list[FaultConfig] = list(faults or [])
        self._pristine_links = {
            link.id: (
                link.up,
                link.latency_ms,
                link.packet_loss_rate,
                link.mtu_bytes,
                link.suppress_frag_needed,
            )
            for link in topology.links
        }
        self._pristine_nodes = {
            node.id: (node.answers_icmp, node.resolver_enabled) for node in topology.nodes
        }
        self._pristine_services = {
            (node.id, svc.name): (svc.healthy, svc.port) for node, svc in topology.services()
        }
        self.blackholes: set[tuple[str, str]] = set()
        self._link_loss_override: dict[str, float] = {}
        self._link_latency_override: dict[str, float] = {}
        self._link_mtu_override: dict[str, int] = {}
        self._suppress_frag_needed: set[str] = set()
        self._port_drop: set[tuple[str, int]] = set()
        self._port_reject: set[tuple[str, int]] = set()
        self._service_down: set[tuple[str, int]] = set()
        self.refresh()

    # ---- accessors -------------------------------------------------------
    @property
    def active_faults(self) -> list[FaultConfig]:
        return [fault for fault in self.faults if fault.is_active]

    def link_loss(self, link: Link) -> float:
        return self._link_loss_override.get(link.id, link.packet_loss_rate)

    def link_mtu(self, link: Link) -> int:
        return self._link_mtu_override.get(link.id, link.mtu_bytes)

    def link_suppresses_frag_needed(self, link: Link) -> bool:
        return link.id in self._suppress_frag_needed

    def port_state(self, node_id: str, port: int) -> Literal["open", "drop", "reject", "service_down"]:
        """The modelled policy for one (node, port) pair.

        A port only has a *listener* if a declared service actually uses it. Returning
        ``open`` for an arbitrary port made the TCP probe report a completed handshake
        against a port nothing was listening on — a false observation fed straight into
        the Bayesian engine.

        Order matters: an injected fault that targets a declared service port takes
        precedence, so injecting SERVICE_DOWN on a live port is reported as the
        service-state change it is; whereas a port with no declared service is always
        ``service_down`` (the host answers, nothing is listening), which is exactly
        what ``REFUSED_NO_LISTENER`` means. Ports with no listener cannot be *dropped*
        by a firewall fault either, because there is nothing to filter — so the fault
        sets are consulted before the listener check for declared ports only.
        """
        if (node_id, port) in self._port_drop:
            return "drop"
        if (node_id, port) in self._port_reject:
            return "reject"
        if (node_id, port) in self._service_down:
            return "service_down"
        if not self.has_listener(node_id, port):
            return "service_down"
        return "open"

    def has_listener(self, node_id: str, port: int) -> bool:
        """True when a declared service on this node uses this port.

        Presence only: a declared-but-``healthy = False`` service still has something
        bound to the port, so it is *listening* while failing to serve. That is the
        ``DEGRADED_STALL`` case, which is a different outcome from an unbound port
        (``REFUSED_NO_LISTENER``). Conflating the two would erase the distinction
        between a crashed process and a process that answers but does not work.
        """
        if not self.topology.has_node(node_id):
            return False
        return any(service.port == port for service in self.topology.node(node_id).services)

    def declared_ports(self, node_id: str) -> list[int]:
        """Ports this node actually exposes, for validation and UI listing."""
        if not self.topology.has_node(node_id):
            return []
        return sorted({service.port for service in self.topology.node(node_id).services})

    def is_blackholed(self, source: str, destination: str, via_link: str | None) -> bool:
        """True when forwarding from ``source`` to ``destination`` is black-holed.

        ``RUNTIMESCAN`` is not required for this check; the pair (link, destination) is
        enough because the black hole is scoped to one flow's destination.
        """
        if via_link is None:
            return any(dest == destination for _, dest in self.blackholes)
        return (via_link, destination) in self.blackholes

    def blackholed_destinations(self, via_link: str) -> list[str]:
        return sorted(destination for link, destination in self.blackholes if link == via_link)

    # ---- mutation --------------------------------------------------------
    def refresh(self) -> None:
        """Recompute the derived state from the active fault list."""
        # 1. restore pristine state
        for link in self.topology.links:
            up, latency, loss, mtu, suppress = self._pristine_links[link.id]
            link.up = up
            link.latency_ms = latency
            link.packet_loss_rate = loss
            link.mtu_bytes = mtu
            link.suppress_frag_needed = suppress
        for node in self.topology.nodes:
            answers, resolver = self._pristine_nodes[node.id]
            node.answers_icmp = answers
            node.resolver_enabled = resolver
        for node, svc in self.topology.services():
            healthy, port = self._pristine_services[(node.id, svc.name)]
            svc.healthy = healthy
            svc.port = port

        self.blackholes = set()
        self._link_loss_override = {}
        self._link_latency_override = {}
        self._link_mtu_override = {}
        self._suppress_frag_needed = set()
        self._port_drop = set()
        self._port_reject = set()
        self._service_down = set()

        # 2. apply active faults in a deterministic order
        for fault in sorted(self.active_faults, key=lambda item: (item.id, item.fault_type.value)):
            self._apply_one(fault)

        # 3. push overrides into the topology objects so probes and routing see them
        for link in self.topology.links:
            link.packet_loss_rate = self.link_loss(link)
            link.mtu_bytes = self.link_mtu(link)
            link.suppress_frag_needed = self.link_suppresses_frag_needed(link)

    def _apply_one(self, fault: FaultConfig) -> None:
        params = fault.parameters or {}
        if fault.fault_type is FaultType.LINK_DOWN:
            self.topology.link(fault.target_id).up = False
        elif fault.fault_type is FaultType.ROUTE_BLACKHOLE:
            self.blackholes.add((fault.target_id, str(params["destination_node_id"])))
        elif fault.fault_type is FaultType.DNS_FAILURE:
            node = self.topology.node(fault.target_id)
            node.resolver_enabled = False
        elif fault.fault_type is FaultType.PACKET_LOSS:
            self._link_loss_override[fault.target_id] = float(params.get("loss_rate", 0.5))
        elif fault.fault_type is FaultType.HIGH_LATENCY:
            self._link_latency_override[fault.target_id] = float(
                params.get("added_latency_ms", 400.0)
            )
            self.topology.link(fault.target_id).latency_ms = self._link_latency_override[
                fault.target_id
            ]
        elif fault.fault_type is FaultType.MTU_BLACK_HOLE:
            mtu = int(params.get("mtu_bytes", 576))
            self._link_mtu_override[fault.target_id] = mtu
            self._suppress_frag_needed.add(fault.target_id)
        elif fault.fault_type is FaultType.TCP_PORT_BLOCKED:
            node, svc = _service_target(self.topology, fault.target_id)
            self._port_drop.add((node.id, svc.port))
        elif fault.fault_type is FaultType.TCP_PORT_REJECTED:
            node, svc = _service_target(self.topology, fault.target_id)
            self._port_reject.add((node.id, svc.port))
        elif fault.fault_type is FaultType.SERVICE_DOWN:
            node, svc = _service_target(self.topology, fault.target_id)
            self._service_down.add((node.id, svc.port))
        elif fault.fault_type is FaultType.GATEWAY_UNREACHABLE:
            self.topology.link(fault.target_id).up = False

    # ---- fault helpers ---------------------------------------------------
    def add_fault(self, fault: FaultConfig) -> None:
        self.faults = [item for item in self.faults if item.id != fault.id]
        self.faults.append(fault)
        self.refresh()

    def remove_fault(self, fault_id: str) -> bool:
        before = len(self.faults)
        self.faults = [item for item in self.faults if item.id != fault_id]
        self.refresh()
        return len(self.faults) != before

    def clear_faults(self) -> None:
        self.faults = []
        self.refresh()

    def set_fault_active(self, fault_id: str, active: bool) -> FaultConfig:
        for fault in self.faults:
            if fault.id == fault_id:
                fault.is_active = active
                self.refresh()
                return fault
        raise ValidationError(f"unknown fault id: {fault_id}", field="fault_id")

    def gateway_link_of(self, node_id: str) -> str | None:
        node = self.topology.node(node_id)
        if node.gateway is None:
            return None
        for link in self.topology.links_of(node_id):
            if node.gateway in link.endpoints():
                return link.id
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<LabState template={self.topology.id} seed={self.random_seed} "
            f"active_faults={[f.fault_type.value for f in self.active_faults]}>"
        )

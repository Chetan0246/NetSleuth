"""Typed virtual-network model: nodes, links, and a validated topology graph.

The lab is an *explicit model*, not a packet-level emulator. Only the behaviours
listed in docs/networking-concepts.md are simulated:

* hop-by-hop shortest-path forwarding over links that are currently up,
* per-link additive latency (RTT = 2 x path latency + per-hop processing delay),
* per-link independent packet loss (seeded, reproducible),
* path-MTU constraints (path MTU = min over links on the path),
* TCP connect outcomes (connected / refused / timeout / unreachable),
* DNS resolution outcomes against the simulated resolver,
* application-service health outcomes.

Everything the simulator does is deterministic for a fixed (topology, faults,
seed) triple.
"""

from __future__ import annotations

import ipaddress
from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator

from ..core.config import MTU_MAX, MTU_MIN
from ..core.errors import ValidationError


class NodeType(str, Enum):
    HOST = "host"
    ROUTER = "router"
    DNS_SERVER = "dns_server"
    APP_SERVER = "app_server"


class ServiceSpec(BaseModel):
    """A single TCP service exposed by an app_server node."""

    name: str
    port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp"] = "tcp"
    healthy: bool = True
    description: str = ""
    # Simulated payload size of a successful response, used by the MTU probe to
    # decide whether a full application exchange fits in the path MTU.
    response_bytes: int = Field(default=512, ge=0, le=65535)

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("service name must not be empty")
        return value


class DnsRecord(BaseModel):
    name: str
    address: str

    @field_validator("address")
    @classmethod
    def _valid_ip(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"invalid IPv4 address: {value}") from exc
        return value


class Node(BaseModel):
    id: str
    name: str
    type: NodeType
    ip_address: str
    # Layout hints for the topology view (simulation does not depend on them).
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    # Hosts/app servers may declare their default gateway (routers report None).
    gateway: str | None = None
    # Routers/hosts answer simulated ICMP echo requests unless configured not to.
    answers_icmp: bool = True
    services: list[ServiceSpec] = Field(default_factory=list)
    dns_records: list[DnsRecord] = Field(default_factory=list)
    # A resolver can be administratively disabled (simulated outage of the
    # resolver daemon itself, independent of any link state).
    resolver_enabled: bool = True
    description: str = ""

    @field_validator("ip_address")
    @classmethod
    def _valid_ip(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            raise ValueError(f"invalid IPv4 address: {value}") from None
        return value


class Link(BaseModel):
    id: str
    node_a: str
    node_b: str
    up: bool = True
    latency_ms: float = Field(default=1.0, ge=0.0, le=5000.0)
    packet_loss_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    mtu_bytes: int = Field(default=1500, ge=MTU_MIN, le=MTU_MAX)
    # When True the simulated router on this link silently discards oversized
    # DF=1 datagrams instead of emitting ICMP "fragmentation needed". This is how
    # an MTU black hole is modelled; it is never True in a healthy topology.
    suppress_frag_needed: bool = False
    # Display-only metadata: the simulator does not model bandwidth contention.
    bandwidth_mbps: float | None = Field(default=None, ge=0.0)
    description: str = ""

    @field_validator("node_a", "node_b")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("link endpoints must be non-empty")
        return value

    def peer_of(self, node_id: str) -> str:
        if node_id == self.node_a:
            return self.node_b
        if node_id == self.node_b:
            return self.node_a
        raise KeyError(f"{node_id} is not an endpoint of link {self.id}")

    def endpoints(self) -> tuple[str, str]:
        return (self.node_a, self.node_b)


class Topology(BaseModel):
    id: str
    name: str
    description: str = ""
    nodes: list[Node]
    links: list[Link]
    is_template: bool = True

    # ---- indexes ---------------------------------------------------------
    def node_index(self) -> dict[str, Node]:
        return {node.id: node for node in self.nodes}

    def link_index(self) -> dict[str, Link]:
        return {link.id: link for link in self.links}

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise ValidationError(f"unknown node id: {node_id}", field="node_id")

    def link(self, link_id: str) -> Link:
        for link in self.links:
            if link.id == link_id:
                return link
        raise ValidationError(f"unknown link id: {link_id}", field="link_id")

    def has_node(self, node_id: str) -> bool:
        return any(node.id == node_id for node in self.nodes)

    def has_link(self, link_id: str) -> bool:
        return any(link.id == link_id for link in self.links)

    def links_of(self, node_id: str) -> list[Link]:
        return [link for link in self.links if node_id in link.endpoints()]

    def hosts(self) -> list[Node]:
        return [node for node in self.nodes if node.type is NodeType.HOST]

    def resolvers(self) -> list[Node]:
        return [node for node in self.nodes if node.type is NodeType.DNS_SERVER]

    def app_servers(self) -> list[Node]:
        return [node for node in self.nodes if node.type is NodeType.APP_SERVER]

    def services(self) -> list[tuple[Node, ServiceSpec]]:
        return [(node, svc) for node in self.nodes for svc in node.services]

    def find_service(self, node_id: str, service_name: str) -> ServiceSpec:
        node = self.node(node_id)
        for svc in node.services:
            if svc.name == service_name:
                return svc
        raise ValidationError(
            f"node {node_id} does not expose a service named {service_name!r}",
            field="destination_service",
        )

    def resolve_record_owner(self, hostname: str) -> Node | None:
        """Return the app/host node that owns this hostname in any resolver."""
        for node in self.nodes:
            for record in node.dns_records:
                if record.name.lower() == hostname.lower():
                    return node
            if node.name.lower() == hostname.lower():
                return node
        return None

    def hostname_for_ip(self, ip_address: str) -> str | None:
        for node in self.nodes:
            for record in node.dns_records:
                if record.address == ip_address:
                    return record.name
        return None


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate_topology(topology: Topology) -> Topology:
    """Validate structural integrity. Raises ValidationError with a field name."""
    if not topology.nodes:
        raise ValidationError("topology must contain at least one node", field="nodes")

    ids: set[str] = set()
    ips: dict[str, str] = {}
    for node in topology.nodes:
        if node.id in ids:
            raise ValidationError(f"duplicate node id: {node.id}", field="nodes")
        ids.add(node.id)
        if node.ip_address in ips:
            raise ValidationError(
                f"duplicate IP address {node.ip_address} used by both "
                f"{ips[node.ip_address]} and {node.id}",
                field="ip_address",
            )
        ips[node.ip_address] = node.id

    if not topology.hosts():
        raise ValidationError("topology must contain at least one host node", field="nodes")
    if not topology.app_servers():
        raise ValidationError("topology must contain at least one app_server node", field="nodes")

    link_ids: set[str] = set()
    pairs: set[frozenset[str]] = set()
    for link in topology.links:
        if link.id in link_ids:
            raise ValidationError(f"duplicate link id: {link.id}", field="links")
        link_ids.add(link.id)
        missing = [endpoint for endpoint in link.endpoints() if endpoint not in ids]
        if missing:
            raise ValidationError(
                f"link {link.id} references unknown endpoint(s): {', '.join(missing)}",
                field="links",
            )
        if link.node_a == link.node_b:
            raise ValidationError(f"link {link.id} connects a node to itself", field="links")
        pair = frozenset(link.endpoints())
        if pair in pairs:
            raise ValidationError(
                f"duplicate link between {link.node_a} and {link.node_b}", field="links"
            )
        pairs.add(pair)

    # Every node must be reachable from every other node when all links are up,
    # otherwise the topology itself is broken (not a fault we can diagnose).
    adjacency: dict[str, set[str]] = {node.id: set() for node in topology.nodes}
    for link in topology.links:
        adjacency[link.node_a].add(link.node_b)
        adjacency[link.node_b].add(link.node_a)
    seen = {next(iter(adjacency))}
    frontier = list(seen)
    while frontier:
        current = frontier.pop()
        for neighbour in adjacency[current]:
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    if len(seen) != len(adjacency):
        missing = sorted(set(adjacency) - seen)
        raise ValidationError(
            "topology is not connected; unreachable node(s): " + ", ".join(missing),
            field="links",
        )

    for node in topology.nodes:
        if node.gateway is not None and node.gateway not in ids:
            raise ValidationError(
                f"node {node.id} declares unknown gateway {node.gateway}", field="gateway"
            )
        for record in node.dns_records:
            if record.address not in ips:
                raise ValidationError(
                    f"DNS record {record.name} -> {record.address} does not match any node IP",
                    field="dns_records",
                )
        for service in node.services:
            if node.type is not NodeType.APP_SERVER:
                raise ValidationError(
                    f"node {node.id} of type {node.type.value} may not expose TCP services",
                    field="services",
                )
    return topology


def node_position_defaults(nodes: Iterable[Node]) -> list[Node]:
    """Assign a deterministic left-to-right layout when positions are missing."""
    ordered = list(nodes)
    for index, node in enumerate(ordered):
        if node.position == {"x": 0.0, "y": 0.0}:
            node.position = {"x": float(120 + index * 180), "y": float(120 + (index % 2) * 140)}
    return ordered


def topology_to_dict(topology: Topology) -> dict[str, Any]:
    return topology.model_dump(mode="json")

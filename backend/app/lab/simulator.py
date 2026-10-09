"""Deterministic packet/flow simulator used by every simulated probe.

Layering (each layer is a small pure function so probes stay thin and tests can
target one layer at a time):

1. **Forwarding** — is there a usable path at all? (link state + route black hole)
2. **Network (ICMP-style)** — echo success/timeout/unreachable, with seeded loss
   and additive RTT (RTT = 2 x one-way latency + per-hop processing + jitter)
3. **Transport (TCP)** — handshake outcome from the forwarding state plus the
   configured port policy of the destination host
4. **Path MTU** — the packet-size ladder, honouring ``suppress_frag_needed``
5. **Application** — simulated service health once TCP is viable

All randomness comes from :meth:`LabSimulator.rng`, which is seeded from the
session seed plus a caller-supplied tag, so a fixed (topology, faults, seed)
triple always yields identical observations.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from ..core.config import (
    DNS_SLOW_MS,
    DNS_TIMEOUT_MS,
    ICMP_ECHO_COUNT,
    ICMP_HIGH_LATENCY_MS,
    MTU_MIN,
    MTU_PROBE_SIZES,
    MTU_STANDARD,
    TCP_CONNECT_TIMEOUT_MS,
    TRACEROUTE_MAX_TTL,
    TRACEROUTE_HOP_TIMEOUT_MS,
)
from .faults import LabState
from .graph import DnsRecord, Node, NodeType, ServiceSpec, Topology
from .outcomes import (
    DnsOutcome,
    IcmpOutcome,
    MtuOutcome,
    ServiceOutcome,
    TcpOutcome,
    TraceOutcome,
)
from .routing import PathResult, RouteDenied, RoutingTable

#: One-way processing/serialisation cost charged per transit router (ms).
HOP_PROCESSING_MS = 0.7
#: Uniform per-packet RTT jitter magnitude (ms).
JITTER_MS = 0.4
#: A TCP handshake RTT above this value is reported as CONNECTED_SLOW (ms).
TCP_SLOW_MS = 250.0
#: How many times each packet size in the MTU ladder is sent. Repeating a size is
#: what makes a lossy link fail only *some* sizes (so the ladder can tell loss
#: apart from an MTU boundary) while keeping the seeded result reproducible.
MTU_PROBE_ATTEMPTS = 3

BlockReason = Literal[
    "NONE", "NO_FIRST_HOP", "LINK_DOWN_MIDPATH", "LINK_DOWN_LAST_HOP", "BLACKHOLE"
]

#: Small probe sizes used to decide whether an MTU ladder is interpretable. A
#: size in this band that already fails means the symptom is not an MTU symptom.
SMALL_PROBE_MAX = 512


@dataclass
class ForwardingState:
    """Result of layer 1: can the source send anything toward the destination?"""

    source: str
    destination: str
    path: PathResult | None
    block_reason: BlockReason = "NONE"
    last_reachable_node: str | None = None
    suspect_link: str | None = None
    blackholed: bool = False
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.path is not None

    @property
    def path_mtu(self) -> int:
        return self.path.path_mtu if self.path else MTU_MIN

    @property
    def one_way_latency_ms(self) -> float:
        return self.path.latency_ms if self.path else 0.0

    @property
    def links(self) -> tuple[str, ...]:
        return self.path.links if self.path else ()

    @property
    def nodes(self) -> tuple[str, ...]:
        return self.path.nodes if self.path else ()


@dataclass
class IcmpState:
    outcome: IcmpOutcome
    target_node_id: str
    target_ip: str
    packets_sent: int
    packets_received: int
    loss_percent: float
    rtt_min_ms: float | None
    rtt_avg_ms: float | None
    rtt_max_ms: float | None
    forwarding: ForwardingState
    simulated_timeout_ms: float
    detail: str

    def as_details(self) -> dict[str, Any]:
        return {
            "target_node_id": self.target_node_id,
            "target_ip": self.target_ip,
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "loss_percent": round(self.loss_percent, 1),
            "rtt_min_ms": _round(self.rtt_min_ms),
            "rtt_avg_ms": _round(self.rtt_avg_ms),
            "rtt_max_ms": _round(self.rtt_max_ms),
            "path_nodes": list(self.forwarding.nodes),
            "path_links": list(self.forwarding.links),
            "hop_count": max(len(self.forwarding.links), 0),
            "path_mtu_bytes": self.forwarding.path_mtu if self.forwarding.usable else None,
            "block_reason": self.forwarding.block_reason,
            "last_reachable_node": self.forwarding.last_reachable_node,
            "suspect_link": self.forwarding.suspect_link,
            "blackholed": self.forwarding.blackholed,
            "simulated_timeout_ms": self.simulated_timeout_ms,
        }


@dataclass
class DnsState:
    outcome: DnsOutcome
    hostname: str
    resolver_node_id: str | None
    resolver_ip: str | None
    resolved_address: str | None
    resolver_reachable: bool
    rtt_ms: float | None
    forwarding: ForwardingState | None
    detail: str

    def as_details(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "resolver_node_id": self.resolver_node_id,
            "resolver_ip": self.resolver_ip,
            "resolved_address": self.resolved_address,
            "resolver_reachable": self.resolver_reachable,
            "rtt_ms": _round(self.rtt_ms),
            "simulated_timeout_ms": DNS_TIMEOUT_MS,
        }


@dataclass
class TraceState:
    outcome: TraceOutcome
    hops: list[dict[str, Any]]
    destination_reached: bool
    last_responding_hop: int | None
    last_responding_node: str | None
    suspect_link: str | None
    nominal_links: list[str]
    forwarding: ForwardingState
    simulated_timeout_ms: float
    detail: str

    def as_details(self) -> dict[str, Any]:
        return {
            "hops": self.hops,
            "hop_count": len(self.hops),
            "destination_reached": self.destination_reached,
            "last_responding_hop": self.last_responding_hop,
            "last_responding_node": self.last_responding_node,
            "suspect_link": self.suspect_link,
            "nominal_path_links": self.nominal_links,
            "simulated_timeout_ms": self.simulated_timeout_ms,
        }


@dataclass
class TcpState:
    outcome: TcpOutcome
    destination_node_id: str
    destination_ip: str
    port: int
    service_name: str | None
    handshake_rtt_ms: float | None
    port_policy: str
    forwarding: ForwardingState
    simulated_timeout_ms: float
    detail: str

    def as_details(self) -> dict[str, Any]:
        return {
            "destination_node_id": self.destination_node_id,
            "destination_ip": self.destination_ip,
            "port": self.port,
            "service_name": self.service_name,
            "port_policy": self.port_policy,
            "handshake_rtt_ms": _round(self.handshake_rtt_ms),
            "block_reason": self.forwarding.block_reason,
            "path_nodes": list(self.forwarding.nodes),
            "path_links": list(self.forwarding.links),
            "last_reachable_node": self.forwarding.last_reachable_node,
            "suspect_link": self.forwarding.suspect_link,
            "blackholed": self.forwarding.blackholed,
            "simulated_timeout_ms": self.simulated_timeout_ms,
        }


@dataclass
class MtuState:
    outcome: MtuOutcome
    sizes: list[int]
    per_size: list[dict[str, Any]]
    max_success_bytes: int | None
    path_mtu_bytes: int | None
    limiting_link: str | None
    repair_reported: bool
    forwarding: ForwardingState
    detail: str

    @property
    def fitting_sizes(self) -> list[dict[str, Any]]:
        """Probed sizes that fit inside the path MTU (these *should* succeed)."""
        mtu = self.path_mtu_bytes
        if mtu is None:
            return []
        return [item for item in self.per_size if item["size_bytes"] <= mtu]

    @property
    def oversized_sizes(self) -> list[dict[str, Any]]:
        """Probed sizes that exceed the path MTU (these must fail)."""
        mtu = self.path_mtu_bytes
        if mtu is None:
            return []
        return [item for item in self.per_size if item["size_bytes"] > mtu]

    @property
    def fitting_failed(self) -> int:
        return sum(1 for item in self.fitting_sizes if not item["success"])

    @property
    def oversize_failed(self) -> int:
        return sum(1 for item in self.oversized_sizes if not item["success"])

    @property
    def small_sizes_ok(self) -> bool:
        small = [item for item in self.per_size if item["size_bytes"] <= SMALL_PROBE_MAX]
        return bool(small) and all(item["success"] for item in small)

    @property
    def delivery_is_intermittent(self) -> bool:
        """True when a size inside the path MTU succeeded only some attempts."""
        mtu = self.path_mtu_bytes
        if mtu is None:
            return False
        return any(
            0 < item.get("delivered", 0) < item.get("attempts", 1)
            for item in self.per_size
            if item["size_bytes"] <= mtu
        )

    @property
    def has_size_inversion(self) -> bool:
        """True when a larger size passed while a smaller one failed.

        A genuine MTU boundary is monotone, so this pattern rules that boundary out
        and is evidence of packet loss instead. Only sizes inside the path MTU are
        compared, because oversize failures are expected to be unconditional.
        """
        fitting = [item for item in self.fitting_sizes]
        passing = [item["size_bytes"] for item in fitting if item["success"]]
        failing = [item["size_bytes"] for item in fitting if not item["success"]]
        return bool(passing and failing and max(failing) < max(passing))

    def as_details(self) -> dict[str, Any]:
        return {
            "sizes_bytes": self.sizes,
            "per_size": self.per_size,
            "max_success_bytes": self.max_success_bytes,
            "path_mtu_bytes": self.path_mtu_bytes,
            "limiting_link": self.limiting_link,
            "frag_needed_reported": self.repair_reported,
            "small_sizes_ok": self.small_sizes_ok,
            "delivery_is_intermittent": self.delivery_is_intermittent,
            "has_size_inversion": self.has_size_inversion,
            "fitting_sizes_tested": len(self.fitting_sizes),
            "fitting_sizes_failed": self.fitting_failed,
            "oversize_sizes_tested": len(self.oversized_sizes),
            "oversize_sizes_failed": self.oversize_failed,
            "block_reason": self.forwarding.block_reason,
            "last_reachable_node": self.forwarding.last_reachable_node,
            "suspect_link": self.forwarding.suspect_link,
            "blackholed": self.forwarding.blackholed,
        }


@dataclass
class ServiceState:
    outcome: ServiceOutcome
    destination_node_id: str
    destination_ip: str
    port: int
    service_name: str
    declared_healthy: bool
    port_policy: str
    forwarding: ForwardingState
    detail: str

    def as_details(self) -> dict[str, Any]:
        return {
            "destination_node_id": self.destination_node_id,
            "destination_ip": self.destination_ip,
            "port": self.port,
            "service_name": self.service_name,
            "declared_healthy": self.declared_healthy,
            "port_policy": self.port_policy,
            "block_reason": self.forwarding.block_reason,
            "last_reachable_node": self.forwarding.last_reachable_node,
            "suspect_link": self.forwarding.suspect_link,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


@dataclass
class ServiceRef:
    """The resolved destination flow: node, service, port and hostname."""

    node: Node
    service: ServiceSpec | None
    port: int
    service_name: str | None
    hostname: str | None
    ip_address: str

    def as_details(self) -> dict[str, Any]:
        return {
            "destination_node_id": self.node.id,
            "destination_node_type": self.node.type.value,
            "destination_ip": self.ip_address,
            "service_name": self.service_name,
            "port": self.port,
            "hostname": self.hostname,
        }


class LabSimulator:
    """Simulated observations for one lab state."""

    def __init__(self, lab: LabState, *, seed: int | None = None,
                 route: RoutingTable | None = None) -> None:
        self.lab = lab
        self.topology: Topology = lab.topology
        self.seed = lab.random_seed if seed is None else seed
        self.route = route or RoutingTable(lab.topology)

    # ---- deterministic randomness ---------------------------------------
    def rng(self, tag: str) -> random.Random:
        return random.Random(f"netsleuth|{self.seed}|{tag}")

    # ---- target resolution ----------------------------------------------
    def resolve_resolvers(self) -> list[Node]:
        return sorted(self.topology.resolvers(), key=lambda node: node.id)

    def primary_resolver(self) -> Node | None:
        resolvers = self.resolve_resolvers()
        return resolvers[0] if resolvers else None

    def resolve_destination(self, destination_node_id: str,
                            service_name: str | None = None) -> ServiceRef:
        node = self.topology.node(destination_node_id)
        service: ServiceSpec | None = None
        if service_name:
            service = self.topology.find_service(destination_node_id, service_name)
        elif node.services:
            service = node.services[0]
        port = service.port if service else 0
        hostname = None
        for record in node.dns_records:
            hostname = record.name
            break
        if hostname is None:
            for resolver in self.topology.resolvers():
                for record in resolver.dns_records:
                    if record.address == node.ip_address:
                        hostname = record.name
                        break
                if hostname:
                    break
        return ServiceRef(
            node=node,
            service=service,
            port=port,
            service_name=service.name if service else None,
            hostname=hostname,
            ip_address=node.ip_address,
        )

    def resolve_record(self, hostname: str) -> DnsRecord | None:
        """Look up a hostname in the simulated zone (records live on resolvers)."""
        for node in sorted(self.topology.nodes, key=lambda item: item.id):
            for record in node.dns_records:
                if record.name.lower() == hostname.lower():
                    return record
        return None

    def control_destination(self, source: str, destination: str) -> Node:
        """A same-layer alternative target in a different segment.

        The last-link ambiguity (is the destination dead, or the link in front of
        it?) is only resolvable with an independent observation, so the lab offers
        one host that shares the destination's local router but nothing else.
        """
        destination_node = self.topology.node(destination)
        candidates: list[Node] = []
        for link in self.topology.links:
            if link.up and destination_node.id in link.endpoints():
                peer_id = link.peer_of(destination_node.id)
                for peer_link in self.topology.links:
                    if peer_id not in peer_link.endpoints():
                        continue
                    other_id = peer_link.peer_of(peer_id)
                    if other_id in (destination, source):
                        continue
                    candidate = self.topology.node(other_id)
                    if candidate.type in (NodeType.APP_SERVER, NodeType.HOST):
                        candidates.append(candidate)
        if not candidates:
            for node in sorted(self.topology.nodes, key=lambda item: item.id):
                if node.id not in (source, destination) and node.type in (
                    NodeType.APP_SERVER,
                    NodeType.HOST,
                ):
                    candidates.append(node)
        if not candidates:
            return self.topology.node(source)
        # Deterministic choice: closest in IP order.
        return sorted(candidates, key=lambda node: node.ip_address)[0]

    # ---- layer 1: forwarding --------------------------------------------
    def forwarding_state(self, source: str, destination: str) -> ForwardingState:
        if source == destination:
            return ForwardingState(source=source, destination=destination,
                                   path=PathResult((source,), (), 0.0,
                                                   self.route.local_mtu(source), True),
                                   detail="source and destination are the same node")
        try:
            path = self.route.shortest_path(source, destination)
        except RouteDenied as exc:
            nominal = self.route.nominal_path(source, destination)
            suspect = self.route.suspect_link(source, destination)
            reason: BlockReason = "NO_FIRST_HOP" if not exc.reached_first_hop else "LINK_DOWN_MIDPATH"
            if nominal is not None and suspect is not None and nominal.links and (
                nominal.links[-1] == suspect
            ):
                reason = "LINK_DOWN_LAST_HOP"
            return ForwardingState(
                source=source,
                destination=destination,
                path=None,
                block_reason=reason,
                last_reachable_node=exc.last_reachable_node
                or self.route.last_reachable_node(source, destination),
                suspect_link=suspect,
                blackholed=False,
                detail=exc.reason,
            )

        blackhole_link = next(
            (link_id for link_id in path.links if (link_id, destination) in self.lab.blackholes),
            None,
        )
        if blackhole_link is not None:
            nominal = self.route.nominal_path(source, destination)
            nominal_links = list(nominal.links) if nominal else list(path.links)
            index = nominal_links.index(blackhole_link) if blackhole_link in nominal_links else 0
            last_node = nominal.nodes[index] if nominal and index < len(nominal.nodes) else source
            return ForwardingState(
                source=source,
                destination=destination,
                path=None,
                block_reason="BLACKHOLE",
                last_reachable_node=last_node,
                suspect_link=blackhole_link,
                blackholed=True,
                detail=(
                    f"the forwarding entry for {self.topology.node(destination).ip_address} is "
                    f"withdrawn at the router beyond {blackhole_link}; packets are dropped "
                    "silently"
                ),
            )
        return ForwardingState(source=source, destination=destination, path=path,
                               detail="path resolved")

    # ---- layer 2: ICMP-style reachability ------------------------------
    def _rtt_samples(self, forward: ForwardingState, tag: str, count: int,
                     *, extra_hop_rtt_ms: float = 0.0) -> tuple[list[float], int]:
        """Return (rtts of received packets, packets_sent).

        A packet survives only if every link along the path lets it through, so
        the end-to-end delivery probability is the product of the per-link
        survival probabilities — the same geometry a real ping sees.
        """
        if forward.path is None:
            return [], count
        rng = self.rng(tag)
        links = [self.topology.link(link_id) for link_id in forward.path.links]
        hops = max(len(links) - 1, 0)
        base_one_way = sum(link.latency_ms for link in links) + HOP_PROCESSING_MS * hops
        rtts: list[float] = []
        for _ in range(count):
            lost = False
            for link in links:
                loss = self.lab.link_loss(link)
                if loss > 0.0 and rng.random() < loss:
                    lost = True
                    break
            if lost:
                continue
            jitter = rng.uniform(-JITTER_MS, JITTER_MS)
            rtts.append(max(0.05, 2.0 * base_one_way + extra_hop_rtt_ms + jitter))
        return rtts, count

    def icmp_probe(self, source: str, target_node_id: str, tag: str,
                   *, count: int = ICMP_ECHO_COUNT,
                   forward: ForwardingState | None = None) -> IcmpState:
        target = self.topology.node(target_node_id)
        forward = forward or self.forwarding_state(source, target_node_id)

        if forward.path is None:
            # Different forwarding blocks produce different ICMP error semantics:
            #   * no usable first hop / mid-path failure -> network-unreachable,
            #   * a black hole is silent (timeout), which is exactly why it is so
            #     hard to tell apart from a filtered or dead destination,
            #   * a failure on the final link yields host-unreachable.
            if forward.block_reason == "BLACKHOLE":
                outcome = IcmpOutcome.TIMEOUT
            elif forward.block_reason == "LINK_DOWN_LAST_HOP":
                outcome = IcmpOutcome.UNREACHABLE_HOST
            elif forward.block_reason == "NO_FIRST_HOP":
                outcome = IcmpOutcome.UNREACHABLE_NETWORK
            else:
                outcome = IcmpOutcome.UNREACHABLE_NETWORK
            return IcmpState(
                outcome=outcome,
                target_node_id=target.id,
                target_ip=target.ip_address,
                packets_sent=count,
                packets_received=0,
                loss_percent=100.0,
                rtt_min_ms=None,
                rtt_avg_ms=None,
                rtt_max_ms=None,
                forwarding=forward,
                simulated_timeout_ms=ICMP_TIMEOUT_MS_FOR(forward),
                detail=forward.detail,
            )

        if not target.answers_icmp:
            return IcmpState(
                outcome=IcmpOutcome.TIMEOUT,
                target_node_id=target.id,
                target_ip=target.ip_address,
                packets_sent=count,
                packets_received=0,
                loss_percent=100.0,
                rtt_min_ms=None,
                rtt_avg_ms=None,
                rtt_max_ms=None,
                forwarding=forward,
                simulated_timeout_ms=ICMP_TIMEOUT_MS_FOR(forward),
                detail=(
                    "the path is usable but the target does not answer echo requests in this "
                    "model (ICMP may be filtered); this alone does not prove the host is down"
                ),
            )

        rtts, sent = self._rtt_samples(forward, f"icmp:{source}:{target.id}:{tag}", count)
        received = len(rtts)
        loss_percent = 100.0 * (sent - received) / sent if sent else 100.0
        if received == 0:
            outcome = IcmpOutcome.TIMEOUT
        elif received < sent:
            outcome = IcmpOutcome.PARTIAL_LOSS
        elif sum(rtts) / len(rtts) > ICMP_HIGH_LATENCY_MS:
            outcome = IcmpOutcome.REACHABLE_SLOW
        else:
            outcome = IcmpOutcome.REACHABLE
        return IcmpState(
            outcome=outcome,
            target_node_id=target.id,
            target_ip=target.ip_address,
            packets_sent=sent,
            packets_received=received,
            loss_percent=loss_percent,
            rtt_min_ms=min(rtts) if rtts else None,
            rtt_avg_ms=(sum(rtts) / len(rtts)) if rtts else None,
            rtt_max_ms=max(rtts) if rtts else None,
            forwarding=forward,
            simulated_timeout_ms=ICMP_TIMEOUT_MS_FOR(forward),
            detail=(
                f"{received}/{sent} echo replies from {target.ip_address} "
                f"along {len(forward.links)} link(s)"
            ),
        )

    # ---- layer 3: DNS ---------------------------------------------------
    def dns_probe(self, source: str, hostname: str, tag: str) -> DnsState:
        resolvers = self.topology.resolvers()
        if not resolvers:
            return DnsState(
                outcome=DnsOutcome.TIMEOUT_RESOLVER,
                hostname=hostname,
                resolver_node_id=None,
                resolver_ip=None,
                resolved_address=None,
                resolver_reachable=False,
                rtt_ms=None,
                forwarding=None,
                detail="this topology has no simulated DNS resolver attached",
            )

        last_forward: ForwardingState | None = None
        for resolver in sorted(resolvers, key=lambda node: node.id):
            forward = self.forwarding_state(source, resolver.id)
            last_forward = forward
            if not resolver.resolver_enabled:
                continue
            if forward.path is None:
                continue
            rtts, sent = self._rtt_samples(forward, f"dns:{source}:{resolver.id}:{hostname}", 1)
            if not rtts:
                continue
            rtt = rtts[0]
            record = self.resolve_record(hostname)
            if record is None:
                return DnsState(
                    outcome=DnsOutcome.NXDOMAIN,
                    hostname=hostname,
                    resolver_node_id=resolver.id,
                    resolver_ip=resolver.ip_address,
                    resolved_address=None,
                    resolver_reachable=True,
                    rtt_ms=rtt,
                    forwarding=forward,
                    detail=f"resolver {resolver.ip_address} answered NXDOMAIN for {hostname}",
                )
            outcome = DnsOutcome.RESOLVED_SLOW if rtt > DNS_SLOW_MS else DnsOutcome.RESOLVED
            return DnsState(
                outcome=outcome,
                hostname=hostname,
                resolver_node_id=resolver.id,
                resolver_ip=resolver.ip_address,
                resolved_address=record.address,
                resolver_reachable=True,
                rtt_ms=rtt,
                forwarding=forward,
                detail=(
                    f"resolver {resolver.ip_address} returned {hostname} -> {record.address} "
                    f"in {round(rtt, 2)} ms"
                ),
            )

        enabled = [node for node in resolvers if node.resolver_enabled]
        if not enabled:
            detail = "every simulated resolver in this topology is currently unavailable"
        elif last_forward is not None and last_forward.path is None:
            detail = (
                "the resolver host is reachable only through a path that is currently broken: "
                + last_forward.detail
            )
        else:
            detail = "the resolver did not answer the query within the simulated timeout"
        return DnsState(
            outcome=DnsOutcome.TIMEOUT_RESOLVER,
            hostname=hostname,
            resolver_node_id=last_forward.destination if last_forward else None,
            resolver_ip=(
                self.topology.node(last_forward.destination).ip_address if last_forward else None
            ),
            resolved_address=None,
            resolver_reachable=False,
            rtt_ms=None,
            forwarding=last_forward,
            detail=detail,
        )

    # ---- layer 4: traceroute -------------------------------------------
    def traceroute(self, source: str, destination: str, tag: str) -> TraceState:
        nominal = self.route.nominal_path(source, destination)
        forward = self.forwarding_state(source, destination)
        if nominal is None:
            return TraceState(
                outcome=TraceOutcome.NO_FIRST_HOP,
                hops=[],
                destination_reached=False,
                last_responding_hop=None,
                last_responding_node=None,
                suspect_link=None,
                nominal_links=[],
                forwarding=forward,
                simulated_timeout_ms=TRACEROUTE_HOP_TIMEOUT_MS,
                detail="this topology has no path between the two nodes even with all links up",
            )

        nominal_links = list(nominal.links)
        blocked_at: int | None = None
        for index, link_id in enumerate(nominal_links):
            link = self.topology.link(link_id)
            if not link.up or (link_id, destination) in self.lab.blackholes:
                blocked_at = index
                break

        rng = self.rng(f"trace:{source}:{destination}:{tag}")
        hops: list[dict[str, Any]] = []
        cumulative = 0.0
        last_responding_hop: int | None = None
        last_responding_node: str | None = None
        limit = min(len(nominal_links), TRACEROUTE_MAX_TTL)
        for index in range(limit):
            if blocked_at is not None and index > blocked_at:
                break
            link = self.topology.link(nominal_links[index])
            hop_node_id = link.peer_of(nominal.nodes[index])
            hop_node = self.topology.node(hop_node_id)
            cumulative += link.latency_ms
            rtt: float | None = None
            responded = False
            if blocked_at is None or index < blocked_at:
                # Per-hop loss is sampled independently (not as a running product)
                # so one lossy link shows up as that single hop failing rather than
                # silencing every later hop.
                loss = self.lab.link_loss(link)
                if (loss <= 0.0 or rng.random() >= loss) and hop_node.answers_icmp:
                    rtt = max(
                        0.05,
                        2.0 * (cumulative + HOP_PROCESSING_MS * (index + 1))
                        + rng.uniform(-JITTER_MS, JITTER_MS),
                    )
                    responded = True
                    last_responding_hop = index + 1
                    last_responding_node = hop_node_id
            hops.append(
                {
                    "ttl": index + 1,
                    "node_id": hop_node_id,
                    "node_name": hop_node.name,
                    "ip_address": hop_node.ip_address,
                    "rtt_ms": _round(rtt),
                    "responded": responded,
                }
            )

        destination_reached = last_responding_node == destination
        suppressed = [hop for hop in hops if not hop["responded"]]
        if blocked_at is None:
            outcome = TraceOutcome.COMPLETE
        else:
            outcome = TraceOutcome.NO_FIRST_HOP if blocked_at == 0 else TraceOutcome.PARTIAL
        suspect_link = nominal_links[blocked_at] if blocked_at is not None else None
        # A trace that reaches the destination but saw silent intermediate hops is
        # still COMPLETE; the suppression is recorded explicitly in the detail text.
        report_suppressed = bool(suppressed) and outcome is TraceOutcome.COMPLETE
        detail_parts = [f"{len(hops)} probe(s) sent"]
        if destination_reached:
            detail_parts.append(f"destination {destination} answered at hop {last_responding_hop}")
        elif last_responding_hop is not None:
            detail_parts.append(
                f"last responding hop {last_responding_hop} "
                f"({self.topology.node(str(last_responding_node)).ip_address})"
            )
        else:
            detail_parts.append("no hop answered")
        if blocked_at is not None:
            detail_parts.append(f"path breaks after hop {blocked_at}")
        return TraceState(
            outcome=outcome,
            hops=hops,
            destination_reached=destination_reached,
            last_responding_hop=last_responding_hop,
            last_responding_node=last_responding_node,
            suspect_link=suspect_link,
            nominal_links=nominal_links,
            forwarding=forward,
            simulated_timeout_ms=TRACEROUTE_HOP_TIMEOUT_MS,
            detail="; ".join(detail_parts)
            + (
                "; intermediate routers suppressed replies at "
                + ", ".join(f"hop {hop['ttl']}" for hop in suppressed)
                if report_suppressed
                else ""
            ),
        )

    # ---- layer 5: TCP connect ------------------------------------------
    def tcp_probe(self, source: str, destination: str, port: int, tag: str,
                  *, service_name: str | None = None,
                  forward: ForwardingState | None = None) -> TcpState:
        node = self.topology.node(destination)
        forward = forward or self.forwarding_state(source, destination)
        policy = self.lab.port_state(node.id, port)
        service = None
        for svc in node.services:
            if svc.port == port or (service_name is not None and svc.name == service_name):
                service = svc
                break
        if forward.path is None:
            return TcpState(
                outcome=TcpOutcome.UNREACHABLE,
                destination_node_id=node.id,
                destination_ip=node.ip_address,
                port=port,
                service_name=service.name if service else service_name,
                handshake_rtt_ms=None,
                port_policy=policy,
                forwarding=forward,
                simulated_timeout_ms=TCP_CONNECT_TIMEOUT_MS,
                detail=forward.detail,
            )

        rtts, _ = self._rtt_samples(forward, f"tcp:{source}:{node.id}:{port}:{tag}", 1)
        rtt = rtts[0] if rtts else None
        if policy == "drop":
            outcome = TcpOutcome.TIMEOUT_DROP
            detail = (
                f"the SYN to {node.ip_address}:{port} received no answer before the simulated "
                "timeout: the packet is dropped on the path (firewall DROP policy)"
            )
        elif policy == "reject":
            outcome = TcpOutcome.REFUSED_NETWORK_POLICY
            detail = (
                f"the SYN to {node.ip_address}:{port} was answered with a rejection from the "
                "network path before reaching the host (firewall REJECT policy)"
            )
        elif policy == "service_down":
            outcome = TcpOutcome.REFUSED_NO_LISTENER
            detail = (
                f"{node.ip_address} answered immediately with a reset for port {port}: the host "
                "is up but no process is listening on that port"
            )
        else:
            if rtt is None:
                outcome = TcpOutcome.TIMEOUT_DROP
                detail = (
                    f"the SYN to {node.ip_address}:{port} was lost on the path before the "
                    "simulated timeout"
                )
            elif rtt > TCP_SLOW_MS:
                outcome = TcpOutcome.CONNECTED_SLOW
                detail = (
                    f"the three-way handshake with {node.ip_address}:{port} completed in "
                    f"{round(rtt, 2)} ms (well above the lab baseline)"
                )
            else:
                outcome = TcpOutcome.CONNECTED
                detail = (
                    f"the three-way handshake with {node.ip_address}:{port} completed in "
                    f"{round(rtt, 2)} ms"
                )
        return TcpState(
            outcome=outcome,
            destination_node_id=node.id,
            destination_ip=node.ip_address,
            port=port,
            service_name=service.name if service else service_name,
            handshake_rtt_ms=rtt,
            port_policy=policy,
            forwarding=forward,
            simulated_timeout_ms=TCP_CONNECT_TIMEOUT_MS,
            detail=detail,
        )

    # ---- layer 6: path MTU ---------------------------------------------
    def mtu_probe(self, source: str, destination: str, tag: str,
                  *, sizes: Sequence[int] = MTU_PROBE_SIZES,
                  forward: ForwardingState | None = None) -> MtuState:
        forward = forward or self.forwarding_state(source, destination)
        if forward.path is None:
            per_size = [
                {
                    "size_bytes": size,
                    "success": False,
                    "observed": "unreachable",
                    "frag_needed_reported": False,
                }
                for size in sizes
            ]
            return MtuState(
                outcome=MtuOutcome.UNREACHABLE,
                sizes=list(sizes),
                per_size=per_size,
                max_success_bytes=None,
                path_mtu_bytes=None,
                limiting_link=forward.suspect_link,
                repair_reported=False,
                forwarding=forward,
                detail="no forwarding path exists, so the MTU ladder cannot be evaluated",
            )

        path_mtu = forward.path_mtu
        limiting_link = None
        smallest = 10**9
        for link_id in forward.links:
            link = self.topology.link(link_id)
            if link.mtu_bytes < smallest:
                smallest = link.mtu_bytes
                limiting_link = link_id
        suppress = any(
            self.lab.link_suppresses_frag_needed(self.topology.link(link_id))
            and self.topology.link(link_id).mtu_bytes <= path_mtu
            for link_id in forward.links
        )
        per_size: list[dict[str, Any]] = []
        rng = self.rng(f"mtu:{source}:{destination}:{tag}")
        for size in sizes:
            if size <= path_mtu:
                # A fitting datagram still survives only if no link on the path
                # drops it. Each size is therefore sent MTU_PROBE_ATTEMPTS times
                # with the seeded RNG, so a lossy-but-fitting path fails *some*
                # sizes instead of failing deterministically: that is what lets
                # the caller separate an MTU limit from packet loss.
                delivered = 0
                for _ in range(MTU_PROBE_ATTEMPTS):
                    dropped = False
                    for link_id in forward.links:
                        loss = self.lab.link_loss(self.topology.link(link_id))
                        if loss > 0.0 and rng.random() < loss:
                            dropped = True
                            break
                    if not dropped:
                        delivered += 1
                success = delivered > 0
                per_size.append(
                    {
                        "size_bytes": size,
                        "success": success,
                        "observed": "echo-reply" if success else "timeout",
                        "frag_needed_reported": False,
                        "attempts": MTU_PROBE_ATTEMPTS,
                        "delivered": delivered,
                    }
                )
            else:
                # Oversized datagrams are discarded by the constrained hop. Whether
                # the sender is told depends on suppress_frag_needed (MTU black
                # hole) or whether the ICMP error is generated normally.
                per_size.append(
                    {
                        "size_bytes": size,
                        "success": False,
                        "observed": (
                            "timeout (oversized datagram discarded, no ICMP error)"
                            if suppress
                            else "icmp-fragmentation-needed"
                        ),
                        "frag_needed_reported": not suppress,
                        "attempts": MTU_PROBE_ATTEMPTS,
                        "delivered": 0,
                    }
                )

        successes = [item["size_bytes"] for item in per_size if item["success"]]
        failures = [item["size_bytes"] for item in per_size if not item["success"]]
        max_success = max(successes) if successes else None
        fitting = [item for item in per_size if item["size_bytes"] <= path_mtu]
        oversized = [item for item in per_size if item["size_bytes"] > path_mtu]
        fitting_failed = [item for item in fitting if not item["success"]]
        fitting_lossy = [item for item in fitting if 0 < item["delivered"] < item["attempts"]]
        oversize_failed = [item for item in oversized if not item["success"]]
        passing_fitting = [item["size_bytes"] for item in fitting if item["success"]]
        failing_fitting = [item["size_bytes"] for item in fitting if not item["success"]]
        inversion = [
            size
            for size in failing_fitting
            if passing_fitting and size < max(passing_fitting)
        ]
        small_ok = all(item["success"] for item in per_size if item["size_bytes"] <= SMALL_PROBE_MAX)
        any_small = any(item["size_bytes"] <= SMALL_PROBE_MAX for item in per_size)

        if not successes:
            outcome = MtuOutcome.UNREACHABLE
            detail = "no probed packet size completed the round trip"
        elif inversion:
            # A larger size passed while a smaller one failed: an MTU boundary cannot
            # produce this, so the ladder reports packet loss instead of an MTU fault.
            outcome = MtuOutcome.INCONCLUSIVE_NON_MONOTONE
            detail = (
                f"the failure pattern is not an MTU boundary: {max(inversion)} bytes "
                f"completed the round trip while the smaller "
                f"{min(inversion)} bytes failed on every attempt. A path-MTU limit is "
                "monotone (everything up to the limit passes), so this pattern is "
                "evidence of packet loss instead"
            )
        elif fitting_lossy:
            # Some attempts at a size that fits inside the path MTU were lost, so
            # delivery is intermittent at sizes that cannot be an MTU problem. The
            # ladder reports this instead of accepting the successes as proof that
            # the path is clean: an intermittent result is not an MTU signature.
            outcome = MtuOutcome.INCONCLUSIVE_LOSS
            detail = (
                "packet delivery was intermittent at sizes that fit inside the path MTU "
                f"({path_mtu} bytes): "
                + ", ".join(
                    f"{item['size_bytes']} bytes succeeded {item['delivered']}/"
                    f"{item['attempts']}"
                    for item in fitting_lossy
                )
                + ". Because the failures are not aligned with the MTU boundary, packet loss "
                "explains them better than an MTU limit"
            )
        elif fitting_failed:
            # Sizes that fit inside the path MTU failed on every attempt, so the
            # pattern is not an MTU pattern either.
            outcome = MtuOutcome.INCONCLUSIVE_LOSS
            detail = (
                f"{len(fitting_failed)} of {len(fitting)} packet sizes that fit inside the "
                f"path MTU ({path_mtu} bytes) failed on every attempt "
                f"(failed at {', '.join(str(item['size_bytes']) for item in fitting_failed)} "
                "bytes), so the failures do not follow the MTU boundary and packet loss "
                "remains a better explanation"
            )
        elif oversize_failed and max_success is not None and max_success < max(sizes) and small_ok:
            outcome = MtuOutcome.LIMITED_DROP if suppress else MtuOutcome.LIMITED_REPORTED
            detail = (
                f"packets up to {max_success} bytes complete the round trip while every "
                f"larger datagram fails (path MTU {path_mtu} bytes on {limiting_link}); "
                + (
                    "no ICMP fragmentation-needed message was returned, which is consistent "
                    "with a path-MTU black hole"
                    if suppress
                    else "ICMP fragmentation-needed messages are returned, so the sender can "
                    "lower its payload and recover"
                )
            )
        elif not small_ok and any_small:
            outcome = MtuOutcome.INCONCLUSIVE_LOSS
            detail = (
                "packet sizes in the small range already fail, so the ladder cannot separate an "
                "MTU limit from packet loss"
            )
        else:
            outcome = MtuOutcome.FULL_PATH_OK
            detail = (
                f"every probed size up to {max_success} bytes completed the round trip "
                f"(path MTU {path_mtu} bytes)"
                + (f"; failed sizes: {failures}" if failures else "")
            )
        return MtuState(
            outcome=outcome,
            sizes=list(sizes),
            per_size=per_size,
            max_success_bytes=max_success,
            path_mtu_bytes=path_mtu,
            limiting_link=limiting_link,
            repair_reported=any(item["frag_needed_reported"] for item in per_size),
            forwarding=forward,
            detail=detail,
        )

    # ---- layer 7: application service ----------------------------------
    def service_probe(self, source: str, destination: str, tag: str,
                      *, service_name: str | None = None, port: int | None = None,
                      forward: ForwardingState | None = None) -> ServiceState:
        node = self.topology.node(destination)
        ref = self.resolve_destination(destination, service_name)
        effective_port = port if port is not None else ref.port
        forward = forward or self.forwarding_state(source, destination)
        policy = self.lab.port_state(node.id, effective_port)
        service = ref.service
        declared_healthy = service.healthy if service else False

        if forward.path is None:
            outcome = ServiceOutcome.UNREACHABLE
            detail = forward.detail
        elif policy == "drop":
            outcome = ServiceOutcome.UNAVAILABLE_TIMEOUT
            detail = (
                f"the health request to {node.ip_address}:{effective_port} timed out; the port "
                "does not answer at all"
            )
        elif policy == "reject":
            outcome = ServiceOutcome.UNAVAILABLE_REFUSED_POLICY
            detail = (
                f"the health request to {node.ip_address}:{effective_port} was rejected by the "
                "network path policy before it reached the application"
            )
        elif policy == "service_down":
            outcome = ServiceOutcome.UNAVAILABLE_REFUSED_NO_LISTENER
            detail = (
                f"{node.ip_address} is reachable and answered the connection attempt, but no "
                f"process is listening for {ref.service_name or effective_port}: the service "
                "itself is not running"
            )
        elif declared_healthy:
            outcome = ServiceOutcome.HEALTHY
            detail = (
                f"the application on {node.ip_address}:{effective_port} returned a healthy "
                "status response"
            )
        else:
            outcome = ServiceOutcome.DEGRADED_STALL
            detail = (
                f"the application port {effective_port} on {node.ip_address} accepts the "
                "connection but does not return a healthy status response"
            )
        return ServiceState(
            outcome=outcome,
            destination_node_id=node.id,
            destination_ip=node.ip_address,
            port=effective_port,
            service_name=ref.service_name or service_name or "unknown",
            declared_healthy=declared_healthy,
            port_policy=policy,
            forwarding=forward,
            detail=detail,
        )


def ICMP_TIMEOUT_MS_FOR(_: ForwardingState) -> float:
    from ..core.config import ICMP_TIMEOUT_MS

    return ICMP_TIMEOUT_MS


def summarize_fault_effect(lab: LabState) -> list[dict[str, Any]]:
    """Human-readable summary of the currently applied fault state (for the UI)."""
    out: list[dict[str, Any]] = []
    for fault in lab.active_faults:
        out.append(
            {
                "id": fault.id,
                "fault_type": fault.fault_type.value,
                "target_id": fault.target_id,
                "target_kind": fault.target_kind.value,
                "parameters": fault.parameters,
                "description": fault.description,
            }
        )
    return out


__all__ = [
    "DnsState",
    "ForwardingState",
    "IcmpState",
    "LabSimulator",
    "MtuState",
    "ServiceRef",
    "ServiceState",
    "TcpState",
    "TraceState",
    "summarize_fault_effect",
    "MTU_STANDARD",
    "MTU_PROBE_SIZES",
]

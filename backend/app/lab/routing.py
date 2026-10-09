"""Deterministic simulated routing and path computation.

Model (documented, deliberately simplified):

* Links are symmetric and carry an additive one-way ``latency_ms``.
* Routers forward on the *lowest-hop-count* path. Ties are broken by the
  lexicographic order of the link-id sequence, which makes the result fully
  deterministic for a fixed topology instead of depending on dict ordering.
* Only links whose ``up`` flag is True can be traversed, so a down link removes
  the path exactly as a failed physical link would.
* A router's forwarding table is derived from those paths (no dynamic routing
  protocol, no alternative-route failover). This is the main difference from a
  real router OS and is called out in docs/limitations.md.
* Per-hop processing delay is charged by the caller (``probe.EXTRA_HOP_DELAY_MS``)
  so that RTT estimates grow with hop count as they do on a real path.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Iterable

from .graph import Link, NodeType, Topology


@dataclass(frozen=True)
class PathResult:
    """A resolved forwarding path plus its accumulated properties."""

    nodes: tuple[str, ...]
    links: tuple[str, ...]
    #: one-way sum of link latencies (ms)
    latency_ms: float
    #: minimum MTU over the traversed links (bytes)
    path_mtu: int
    #: link ids that are currently able to carry traffic (all of them, by construction)
    up: bool

    @property
    def hop_count(self) -> int:
        """Number of layer-3 hops (routers traversed)."""
        return max(len(self.nodes) - 1, 0)

    def suspect_links(self) -> list[str]:
        return list(self.links)


class RouteDenied(Exception):
    """Raised when no forwarding path exists for a destination."""

    def __init__(self, reason: str, *, last_reachable_node: str | None = None,
                 reached_first_hop: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.last_reachable_node = last_reachable_node
        self.reached_first_hop = reached_first_hop


class RoutingTable:
    """Pre-computed forwarding state derived from the active topology.

    The router's own state is *derived* from link status, so undoing a fault
    automatically restores the original forwarding behaviour (reversible faults).
    """

    def __init__(self, topology: Topology) -> None:
        self.topology = topology
        self._index = topology.node_index()

    # ---- public API ------------------------------------------------------
    def link_up(self, link: Link) -> bool:
        return link.up

    def usable_links(self) -> list[Link]:
        return [link for link in self.topology.links if self.link_up(link)]

    def neighbours(self, node_id: str) -> list[str]:
        out: list[str] = []
        for link in sorted(self.topology.links, key=lambda item: item.id):
            if not self.link_up(link):
                continue
            if node_id in link.endpoints():
                out.append(link.peer_of(node_id))
        return out

    def shortest_path(self, source: str, destination: str) -> PathResult:
        """Return the deterministic lowest-hop path, or raise RouteDenied."""
        if not self.topology.has_node(source):
            raise RouteDenied(f"source {source} is not part of this topology")
        if not self.topology.has_node(destination):
            raise RouteDenied(f"destination {destination} is not part of this topology")
        if source == destination:
            return PathResult((source,), (), 0.0, self.local_mtu(source), True)

        if not self._has_first_hop(source):
            raise RouteDenied(
                f"{source} has no usable first-hop link",
                last_reachable_node=source,
                reached_first_hop=False,
            )

        # Dijkstra over hop count with a (hops, link-id-sequence) ordering key so
        # the result never depends on dictionary ordering.
        best: dict[str, tuple[tuple[int, tuple[str, ...]], tuple[str, ...], tuple[str, ...]]] = {
            source: ((0, ()), (source,), ())
        }
        heap: list[tuple[int, tuple[str, ...], str]] = [(0, (), source)]
        while heap:
            hops, link_seq, node_id = heapq.heappop(heap)
            key = (hops, link_seq)
            if key > best[node_id][0]:
                # A shorter route to this node was already settled.
                continue
            if node_id == destination:
                _, node_path, link_path = best[node_id]
                return self._describe(node_path, link_path)
            parent_nodes = best[node_id][1]
            parent_links = best[node_id][2]
            for link in sorted(self.topology.links, key=lambda item: item.id):
                if not self.link_up(link) or node_id not in link.endpoints():
                    continue
                peer = link.peer_of(node_id)
                candidate: tuple[int, tuple[str, ...]] = (hops + 1, link_seq + (link.id,))
                current = best.get(peer)
                if current is not None and candidate >= current[0]:
                    continue
                best[peer] = (candidate, parent_nodes + (peer,), parent_links + (link.id,))
                heapq.heappush(heap, (candidate[0], candidate[1], peer))

        # No path: find how far along the *nominal* path the tracer can get, so
        # that the trace can report a last reachable node instead of a bare error.
        raise RouteDenied(
            f"no route from {source} to {destination} with current link state",
            last_reachable_node=self.last_reachable_node(source, destination),
        )

    def try_shortest_path(self, source: str, destination: str) -> PathResult | None:
        try:
            return self.shortest_path(source, destination)
        except RouteDenied:
            return None

    def last_reachable_node(self, source: str, destination: str) -> str:
        """Furthest node reachable from ``source`` on the nominal (all-up) path.

        Used both by traceroute and by the diagnostic explanation, so that a
        failed link can be localized to a link id rather than merely reported as
        "destination unreachable".
        """
        nominal = self.nominal_path(source, destination)
        if nominal is None:
            return source
        previous = nominal.nodes[0]
        for link_id in nominal.links:
            link = self.topology.link(link_id)
            if not self.link_up(link):
                return previous
            previous = link.peer_of(previous)
        return previous

    def suspect_link(self, source: str, destination: str) -> str | None:
        """First link on the nominal path that is currently down, if any."""
        nominal = self.nominal_path(source, destination)
        if nominal is None:
            return None
        for link_id in nominal.links:
            if not self.link_up(self.topology.link(link_id)):
                return link_id
        return None

    def nominal_path(self, source: str, destination: str) -> PathResult | None:
        """Path computed as if every link were up (used for localization only).

        Implemented by temporarily forcing :meth:`link_up` to return True rather than
        by flipping ``link.up`` on every link. The previous approach mutated the live
        topology for the duration of the call, which is not safe when two evaluations
        share a topology: a concurrent reader could observe the all-links-up window
        and produce an observation inconsistent with the real link state. Forcing the
        predicate leaves the model untouched, so this is a pure read.
        """
        original = self.link_up
        self.link_up = lambda link: True  # type: ignore[method-assign]
        try:
            return self.shortest_path(source, destination)
        except RouteDenied:
            return None
        finally:
            self.link_up = original  # type: ignore[method-assign]

    # ---- helpers ---------------------------------------------------------
    def local_mtu(self, node_id: str) -> int:
        links = self.topology.links_of(node_id)
        if not links:
            from ..core.config import MTU_STANDARD

            return MTU_STANDARD
        return min(link.mtu_bytes for link in links)

    def forwarding_table(self, router_id: str) -> list[dict[str, object]]:
        """Derived static routes for display: destination node -> next hop."""
        node = self.topology.node(router_id)
        if node.type is not NodeType.ROUTER:
            return []
        rows: list[dict[str, object]] = []
        for target in sorted(self.topology.nodes, key=lambda item: item.ip_address):
            if target.id == router_id:
                continue
            try:
                path = self.shortest_path(router_id, target.id)
            except RouteDenied:
                rows.append(
                    {
                        "destination": target.ip_address,
                        "next_hop": None,
                        "interface": None,
                        "link": None,
                        "hops": None,
                        "reachable": False,
                    }
                )
                continue
            next_hop_id = path.nodes[1] if len(path.nodes) > 1 else router_id
            next_hop = self._index.get(next_hop_id)
            link_id = path.links[0] if path.links else None
            rows.append(
                {
                    "destination": target.ip_address,
                    "next_hop": next_hop.ip_address if next_hop else None,
                    "next_hop_node": next_hop_id,
                    "interface": link_id,
                    "link": link_id,
                    "hops": path.hop_count,
                    "reachable": True,
                }
            )
        return rows

    def _has_first_hop(self, node_id: str) -> bool:
        return bool(self.neighbours(node_id))

    def _describe(self, node_path: Iterable[str], link_path: Iterable[str]) -> PathResult:
        nodes = tuple(node_path)
        links = tuple(link_path)
        latency = 0.0
        mtu: int | None = None
        for link_id in links:
            link = self.topology.link(link_id)
            latency += link.latency_ms
            mtu = link.mtu_bytes if mtu is None else min(mtu, link.mtu_bytes)
        if mtu is None:
            mtu = self.local_mtu(nodes[0])
        return PathResult(nodes=nodes, links=links, latency_ms=latency, path_mtu=int(mtu), up=True)

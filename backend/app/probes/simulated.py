"""Concrete diagnostic probes over the virtual lab (plan.md sections 8 and 15.1).

Every probe:

* implements the same :class:`~app.probes.base.ProbeRunner` contract,
* reads only the *lab state* and the :class:`ProbeRequest` it is given (never the
  injected ground-truth fault label),
* returns one structured :class:`~app.probes.base.ProbeObservation` containing a
  machine-readable outcome code, structured ``details`` and human-readable
  evidence statements.

The outcome code vocabulary lives in :mod:`app.lab.outcomes`; the likelihood
table in :mod:`app.diagnosis.likelihoods` must cover every code produced here.
Those literals are pinned by ``tests/unit/test_outcomes_vocabulary.py``.
"""

from __future__ import annotations

import time
from typing import Any

from ..lab.outcomes import (
    IcmpOutcome,
    IcmpSelector,
    MODE_SIMULATED,
    ProbeType,
)
from ..lab.simulator import LabSimulator
from .base import EvidenceStatement, ProbeObservation, ProbeRequest

#: All outcome codes this module is allowed to emit. Kept explicit so a typo or a
#: probe/model drift is caught by a test rather than silently producing an
#: unknown outcome at runtime.
KNOWN_OUTCOMES: dict[ProbeType, set[str]] = {
    ProbeType.ICMP_REACHABILITY: {
        "REACHABLE",
        "REACHABLE_SLOW",
        "PARTIAL_LOSS",
        "TIMEOUT",
        "UNREACHABLE_NETWORK",
        "UNREACHABLE_HOST",
    },
    ProbeType.DNS_LOOKUP: {"RESOLVED", "RESOLVED_SLOW", "TIMEOUT_RESOLVER", "NXDOMAIN"},
    ProbeType.TRACEROUTE: {"COMPLETE", "COMPLETE_WITH_SUPPRESSED", "PARTIAL", "NO_FIRST_HOP"},
    ProbeType.TCP_CONNECT: {
        "CONNECTED",
        "CONNECTED_SLOW",
        "TIMEOUT_DROP",
        "REFUSED_NETWORK_POLICY",
        "REFUSED_NO_LISTENER",
        "UNREACHABLE",
    },
    ProbeType.MTU_PROBE: {
        "FULL_PATH_OK",
        "LIMITED_DROP",
        "LIMITED_REPORTED",
        "INCONCLUSIVE_LOSS",
        "INCONCLUSIVE_NON_MONOTONE",
        "UNREACHABLE",
    },
    ProbeType.SERVICE_HEALTH: {
        "HEALTHY",
        "DEGRADED_STALL",
        "UNAVAILABLE_REFUSED_POLICY",
        "UNAVAILABLE_REFUSED_NO_LISTENER",
        "UNAVAILABLE_TIMEOUT",
        "UNREACHABLE",
    },
}


class _Timed:
    """Context manager that records the measured wall-clock duration."""

    def __init__(self) -> None:
        self.ms = 0.0

    def __enter__(self) -> "_Timed":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000.0


def _observation(
    request: ProbeRequest,
    *,
    probe_type: ProbeType,
    probe_label: str,
    outcome: str,
    summary: str,
    details: dict[str, Any],
    evidence: list[EvidenceStatement],
    elapsed_ms: float,
    wall_clock_ms: float,
) -> ProbeObservation:
    allowed = KNOWN_OUTCOMES[probe_type]
    if outcome not in allowed:
        raise AssertionError(
            f"probe {probe_type.value} emitted unknown outcome {outcome!r}; "
            f"allowed: {sorted(allowed)}"
        )
    return ProbeObservation(
        probe_key=request.probe_key,
        probe_type=probe_type,
        probe_label=probe_label,
        mode=MODE_SIMULATED,
        source_node_id=request.source_node_id,
        destination_node_id=request.destination_node_id,
        outcome=outcome,
        summary=summary,
        details=details,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
        wall_clock_ms=wall_clock_ms,
        sequence_number=request.sequence_number,
    )


# ---------------------------------------------------------------------------
# 1. ICMP-style reachability
# ---------------------------------------------------------------------------


class IcmpReachabilityProbe:
    """Simulated echo request/reply against the destination, gateway or resolver.

    Four selectors are supported, because the resulting evidence is *different*
    for each: the destination tells you about the whole path, the default gateway
    isolates the first hop, the resolver isolates the DNS path, and the control
    destination disambiguates a dead destination from a dead last-hop link.
    """

    probe_type = ProbeType.ICMP_REACHABILITY

    def __init__(self, simulator: LabSimulator) -> None:
        self.simulator = simulator

    def resolve_target(self, request: ProbeRequest) -> tuple[str, str]:
        """Return ``(node_id, selector)`` for this request."""
        if not request.probe_key.startswith(f"{self.probe_type.value}:"):
            # Tolerate a bare probe key: default to the destination selector.
            return request.destination_node_id, IcmpSelector.DESTINATION.value
        selector = request.probe_key.split(":", 1)[1]
        if selector == IcmpSelector.GATEWAY.value:
            return (request.gateway_node_id or request.source_node_id), selector
        if selector == IcmpSelector.RESOLVER.value:
            return (request.resolver_node_id or request.destination_node_id), selector
        if selector == IcmpSelector.CONTROL_DESTINATION.value:
            return (request.control_node_id or request.destination_node_id), selector
        return request.destination_node_id, IcmpSelector.DESTINATION.value

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:
        del lab  # state is already bound to the simulator
        target_id, selector = self.resolve_target(request)
        with _Timed() as clock:
            state = self.simulator.icmp_probe(
                request.source_node_id, target_id, request.tag
            )
        details = state.as_details()
        details["selector"] = selector
        details["target_role"] = selector
        role_text = {
            IcmpSelector.DESTINATION.value: "the destination",
            IcmpSelector.GATEWAY.value: "the source host's default gateway",
            IcmpSelector.RESOLVER.value: "the DNS resolver",
            IcmpSelector.CONTROL_DESTINATION.value: (
                "a control target in the destination's own segment"
            ),
        }[selector]
        evidence = self._evidence(state, request, role_text)
        return _observation(
            request,
            probe_type=self.probe_type,
            probe_label=(
                "Simulated ICMP echo to " + role_text
            ),
            outcome=state.outcome.value,
            summary=state.detail,
            details=details,
            evidence=evidence,
            elapsed_ms=state.simulated_timeout_ms
            if state.packets_received == 0
            else float(state.rtt_avg_ms or 0.0),
            wall_clock_ms=clock.ms,
        )

    def _evidence(self, state: Any, request: ProbeRequest, role_text: str) -> list[EvidenceStatement]:
        out: list[EvidenceStatement] = []
        outcome = state.outcome
        if outcome is IcmpOutcome.REACHABLE:
            out.append(
                EvidenceStatement(
                    statement=(
                        f"Echo replies were received from {state.target_ip} ({role_text}) "
                        f"with an average RTT of {round(state.rtt_avg_ms or 0.0, 2)} ms, so the "
                        "forward and return paths are usable."
                    ),
                    kind="observation",
                    strength="strong",
                    details={"rtt_avg_ms": state.rtt_avg_ms, "target": state.target_ip},
                )
            )
        elif outcome is IcmpOutcome.REACHABLE_SLOW:
            out.append(
                EvidenceStatement(
                    statement=(
                        f"Echo replies were received from {state.target_ip} ({role_text}) but the "
                        f"average RTT of {round(state.rtt_avg_ms or 0.0, 2)} ms is far above the "
                        "lab baseline."
                    ),
                    kind="observation",
                    strength="strong",
                    details={"rtt_avg_ms": state.rtt_avg_ms},
                )
            )
        elif outcome is IcmpOutcome.PARTIAL_LOSS:
            out.append(
                EvidenceStatement(
                    statement=(
                        f"{state.packets_sent - state.packets_received} of {state.packets_sent} "
                        f"echo requests to {state.target_ip} ({role_text}) were lost "
                        f"({round(state.loss_percent, 1)} % loss) with the remainder succeeding, "
                        "which is characteristic of intermittent packet loss rather than a "
                        "hard link failure."
                    ),
                    kind="observation",
                    strength="strong",
                    details={"loss_percent": state.loss_percent},
                )
            )
        elif outcome in (IcmpOutcome.UNREACHABLE_NETWORK, IcmpOutcome.UNREACHABLE_HOST):
            kind = (
                "network-unreachable"
                if outcome is IcmpOutcome.UNREACHABLE_NETWORK
                else "host-unreachable"
            )
            last = state.forwarding.last_reachable_node
            out.append(
                EvidenceStatement(
                    statement=(
                        f"The simulated router returned an ICMP {kind} error for {role_text}, "
                        "so forwarding failed on the path rather than at an application."
                        + (f" The furthest reachable node was {last}." if last else "")
                    ),
                    kind="observation",
                    strength="strong",
                    details={
                        "icmp_error": kind,
                        "last_reachable_node": last,
                        "suspect_link": state.forwarding.suspect_link,
                    },
                )
            )
            if state.forwarding.suspect_link:
                out.append(
                    EvidenceStatement(
                        statement=(
                            f"The failure is localized to link {state.forwarding.suspect_link} "
                            "on the nominal path."
                        ),
                        kind="reading",
                        strength="moderate",
                        supports=["LINK_FAILURE"],
                        details={"suspect_link": state.forwarding.suspect_link},
                    )
                )
        else:  # TIMEOUT
            if state.forwarding.blackholed:
                out.append(
                    EvidenceStatement(
                        statement=(
                            f"Echo requests to {state.target_ip} ({role_text}) received no reply "
                            "at all, and the simulated forwarding layer reports a withdrawn "
                            "route rather than a downed link."
                        ),
                        kind="observation",
                        strength="strong",
                        details={"blackholed": True, "suspect_link": state.forwarding.suspect_link},
                    )
                )
            elif state.forwarding.path is not None:
                out.append(
                    EvidenceStatement(
                        statement=(
                            f"A forwarding path to {state.target_ip} exists but no echo reply "
                            "arrived, which means the replies may be filtered. A silent ICMP "
                            "result does not prove that the host is down."
                        ),
                        kind="observation",
                        strength="weak",
                        details={"icmp_filtered_possible": True},
                    )
                )
            elif state.packets_received == 0 and state.loss_percent >= 100.0:
                out.append(
                    EvidenceStatement(
                        statement=(
                            f"All {state.packets_sent} echo requests to {state.target_ip} "
                            f"({role_text}) timed out."
                        ),
                        kind="observation",
                        strength="moderate",
                        details={"loss_percent": state.loss_percent},
                    )
                )
        return out


# ---------------------------------------------------------------------------
# 2. DNS lookup
# ---------------------------------------------------------------------------


class DnsLookupProbe:
    probe_type = ProbeType.DNS_LOOKUP

    def __init__(self, simulator: LabSimulator) -> None:
        self.simulator = simulator

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:
        del lab
        hostname = request.hostname or self._default_hostname(request)
        with _Timed() as clock:
            state = self.simulator.dns_probe(request.source_node_id, hostname, request.tag)
        details = state.as_details()
        details["requested_hostname"] = hostname
        evidence: list[EvidenceStatement] = []
        outcome = state.outcome.value
        if state.outcome.value in ("RESOLVED", "RESOLVED_SLOW"):
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"Resolver {state.resolver_ip} answered {hostname} with "
                        f"{state.resolved_address} in {round(state.rtt_ms or 0.0, 2)} ms, so name "
                        "resolution itself is working."
                    ),
                    kind="observation",
                    strength="strong",
                    details={"resolved_address": state.resolved_address},
                )
            )
            if state.outcome.value == "RESOLVED_SLOW":
                evidence.append(
                    EvidenceStatement(
                        statement=(
                            f"The resolution RTT of {round(state.rtt_ms or 0.0, 2)} ms is above "
                            "the lab baseline for name lookups."
                        ),
                        kind="observation",
                        strength="moderate",
                        supports=["HIGH_LATENCY"],
                    )
                )
        elif state.outcome.value == "NXDOMAIN":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"Resolver {state.resolver_ip} replied NXDOMAIN for {hostname}: the "
                        "resolver was reachable and answered, but the name is not in the zone."
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["DNS_FAILURE"],
                    details={"resolver_reachable": True, "resolver_ip": state.resolver_ip},
                )
            )
        else:
            reachable = state.resolver_reachable
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The name lookup for {hostname} got no answer before the simulated "
                        f"timeout. {state.detail}"
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["DNS_FAILURE"],
                    details={
                        "resolver_reachable": reachable,
                        "resolver_ip": state.resolver_ip,
                    },
                )
            )
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "Because only name resolution failed, the transport path must still be "
                        "tested directly against the server IP address before concluding that "
                        "the route is broken."
                    ),
                    kind="limitation",
                    strength="moderate",
                )
            )
        return _observation(
            request,
            probe_type=self.probe_type,
            probe_label="Simulated DNS lookup",
            outcome=outcome,
            summary=state.detail,
            details=details,
            evidence=evidence,
            elapsed_ms=state.rtt_ms or 0.0,
            wall_clock_ms=clock.ms,
        )

    def _default_hostname(self, request: ProbeRequest) -> str:
        ref = self.simulator.resolve_destination(
            request.destination_node_id, request.destination_service
        )
        return ref.hostname or ref.ip_address


# ---------------------------------------------------------------------------
# 3. Traceroute / TTL probe
# ---------------------------------------------------------------------------


class TracerouteProbe:
    probe_type = ProbeType.TRACEROUTE

    def __init__(self, simulator: LabSimulator) -> None:
        self.simulator = simulator

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:
        del lab
        with _Timed() as clock:
            state = self.simulator.traceroute(
                request.source_node_id, request.destination_node_id, request.tag
            )
        details = state.as_details()
        outcome = state.outcome.value
        if state.destination_reached and any(not hop["responded"] for hop in state.hops):
            outcome = "COMPLETE_WITH_SUPPRESSED"
        evidence: list[EvidenceStatement] = []
        if state.destination_reached:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The path trace reached the destination in {len(state.hops)} hop(s), so "
                        "the whole forwarding path is usable."
                    ),
                    kind="observation",
                    strength="strong",
                    details={"hops": len(state.hops)},
                )
            )
        elif state.last_responding_hop is not None:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The trace stops replying after hop {state.last_responding_hop} "
                        f"({state.last_responding_node}), so the fault lies on the link beyond "
                        "that point on the nominal path"
                        + (f" (link {state.suspect_link})." if state.suspect_link else ".")
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["LINK_FAILURE", "ROUTING_FAILURE"],
                    details={
                        "last_responding_hop": state.last_responding_hop,
                        "last_responding_node": state.last_responding_node,
                        "suspect_link": state.suspect_link,
                    },
                )
            )
            if state.suspect_link:
                evidence.append(
                    EvidenceStatement(
                        statement=(
                            f"Localized component: link {state.suspect_link}. A trace localizes a "
                            "fault to a segment; it does not by itself prove whether the link is "
                            "down or the route was withdrawn."
                        ),
                        kind="reading",
                        strength="strong",
                        supports=["LINK_FAILURE", "ROUTING_FAILURE"],
                        details={"suspect_link": state.suspect_link},
                    )
                )
        else:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "No hop answered the TTL-limited probes, so the path could not be walked "
                        "at all."
                    ),
                    kind="observation",
                    strength="moderate",
                    details={"hops": len(state.hops)},
                )
            )
        if any(not hop["responded"] for hop in state.hops) and not state.destination_reached:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "Some probes were answered and later ones were not; routers can suppress "
                        "TTL-expired replies, so a single silent hop is suggestive rather than "
                        "conclusive."
                    ),
                    kind="limitation",
                    strength="weak",
                    details={"suppressed_hops": [
                        hop["ttl"] for hop in state.hops if not hop["responded"]
                    ]},
                )
            )
        return _observation(
            request,
            probe_type=self.probe_type,
            probe_label="Simulated TTL-limited path trace",
            outcome=outcome,
            summary=state.detail,
            details=details,
            evidence=evidence,
            elapsed_ms=state.simulated_timeout_ms * max(len(state.hops), 1),
            wall_clock_ms=clock.ms,
        )


# ---------------------------------------------------------------------------
# 4. TCP connect
# ---------------------------------------------------------------------------


def _service_and_port(simulator: LabSimulator, request: ProbeRequest) -> tuple[str | None, int]:
    ref = simulator.resolve_destination(request.destination_node_id, request.destination_service)
    port = request.port if request.port is not None else ref.port
    return ref.service_name, port


class TcpConnectProbe:
    probe_type = ProbeType.TCP_CONNECT

    def __init__(self, simulator: LabSimulator) -> None:
        self.simulator = simulator

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:
        del lab
        service_name, port = _service_and_port(self.simulator, request)
        with _Timed() as clock:
            state = self.simulator.tcp_probe(
                request.source_node_id,
                request.destination_node_id,
                port,
                request.tag,
                service_name=request.destination_service,
            )
        details = state.as_details()
        evidence: list[EvidenceStatement] = []
        outcome = state.outcome.value
        if state.outcome.value in ("CONNECTED", "CONNECTED_SLOW"):
            slow = state.outcome.value == "CONNECTED_SLOW"
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The TCP handshake with {state.destination_ip}:{port} completed in "
                        f"{round(state.handshake_rtt_ms or 0.0, 2)} ms, so IP reachability, "
                        "routing and the port itself are all working."
                        + (" The handshake RTT is well above the lab baseline." if slow else "")
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["UNKNOWN_OR_MULTIPLE_CAUSES"],
                    details={"handshake_rtt_ms": state.handshake_rtt_ms, "port": port},
                )
            )
            if slow:
                evidence.append(
                    EvidenceStatement(
                        statement=(
                            "A completed but very slow handshake points at added path latency "
                            "rather than a routing or filtering failure."
                        ),
                        kind="reading",
                        strength="moderate",
                        supports=["HIGH_LATENCY"],
                    )
                )
        elif state.outcome.value == "TIMEOUT_DROP":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The SYN to {state.destination_ip}:{port} was not answered before the "
                        "simulated timeout, while the network path to that address is being "
                        "tested separately. Silent drops look the same whether a firewall is "
                        "filtering the port or the packet is lost on the path."
                    ),
                    kind="observation",
                    strength="moderate",
                    supports=["TCP_FILTER_OR_PORT_FAILURE"],
                    details={"port": port, "port_policy": state.port_policy},
                )
            )
        elif state.outcome.value == "REFUSED_NETWORK_POLICY":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The connection attempt to {state.destination_ip}:{port} was actively "
                        "rejected by the network path policy, so something on the path answered "
                        "and refused it."
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["TCP_FILTER_OR_PORT_FAILURE"],
                    details={"port": port, "port_policy": state.port_policy},
                )
            )
        elif state.outcome.value == "REFUSED_NO_LISTENER":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"{state.destination_ip} answered immediately and reset the connection "
                        f"for port {port}: the host is up and reachable but nothing is listening "
                        "on that port."
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["APPLICATION_SERVICE_FAILURE", "TCP_FILTER_OR_PORT_FAILURE"],
                    details={"port": port},
                )
            )
        else:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The TCP attempt to {state.destination_ip}:{port} could not even leave "
                        "the network layer: " + state.detail
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["LINK_FAILURE", "ROUTING_FAILURE"],
                    details={"block_reason": state.forwarding.block_reason},
                )
            )
        return _observation(
            request,
            probe_type=self.probe_type,
            probe_label=f"Simulated TCP connect to port {port}",
            outcome=outcome,
            summary=state.detail,
            details=details,
            evidence=evidence,
            elapsed_ms=state.handshake_rtt_ms or state.simulated_timeout_ms,
            wall_clock_ms=clock.ms,
        )


# ---------------------------------------------------------------------------
# 5. MTU / packet-size ladder
# ---------------------------------------------------------------------------


class MtuProbe:
    probe_type = ProbeType.MTU_PROBE

    def __init__(self, simulator: LabSimulator) -> None:
        self.simulator = simulator

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:
        del lab
        with _Timed() as clock:
            state = self.simulator.mtu_probe(
                request.source_node_id, request.destination_node_id, request.tag
            )
        details = state.as_details()
        evidence: list[EvidenceStatement] = []
        outcome = state.outcome.value
        if state.outcome.value in ("LIMITED_DROP", "LIMITED_REPORTED"):
            black_hole = state.outcome.value == "LIMITED_DROP"
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"Packets up to {state.max_success_bytes} bytes complete the round trip "
                        f"while larger datagrams fail, with the path MTU limited to "
                        f"{state.path_mtu_bytes} bytes"
                        + (f" by link {state.limiting_link}" if state.limiting_link else "")
                        + ". Small probes succeeding while large ones fail is the signature of an "
                        "MTU problem."
                    ),
                    kind="observation",
                    strength="strong" if black_hole else "moderate",
                    supports=["MTU_BLACK_HOLE"],
                    details={
                        "max_success_bytes": state.max_success_bytes,
                        "path_mtu_bytes": state.path_mtu_bytes,
                        "limiting_link": state.limiting_link,
                    },
                )
            )
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "No ICMP fragmentation-needed message was returned, so the sender never "
                        "learns to reduce its payload: this is a path-MTU black hole."
                        if black_hole
                        else "ICMP fragmentation-needed messages were returned, so a real sender "
                        "could lower its payload and recover; the symptom is an MTU limit rather "
                        "than a black hole."
                    ),
                    kind="reading",
                    strength="strong" if black_hole else "moderate",
                    supports=["MTU_BLACK_HOLE"] if black_hole else [],
                    weakens=["MTU_BLACK_HOLE"] if not black_hole else [],
                )
            )
        elif state.outcome.value == "INCONCLUSIVE_NON_MONOTONE":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "A packet size that fits inside the path MTU completed the round trip "
                        "while a *smaller* size failed on every attempt. A path-MTU boundary "
                        "cannot produce that pattern, so this points at packet loss rather "
                        "than at the MTU."
                    ),
                    kind="reading",
                    strength="strong",
                    supports=["PACKET_LOSS"],
                    weakens=["MTU_BLACK_HOLE"],
                    details={
                        "has_size_inversion": True,
                        "failed_sizes": [
                            item["size_bytes"] for item in details.get("per_size", [])
                            if not item["success"]
                        ],
                        "max_success_bytes": state.max_success_bytes,
                    },
                )
            )
        elif state.outcome.value == "INCONCLUSIVE_LOSS":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "Even the smallest probed packet sizes failed, so the ladder cannot "
                        "separate an MTU limit from general packet loss."
                    ),
                    kind="limitation",
                    strength="weak",
                    details={"small_sizes_ok": state.small_sizes_ok},
                )
            )
        elif state.outcome.value == "FULL_PATH_OK":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"Every probed packet size up to {state.max_success_bytes} bytes "
                        f"(path MTU {state.path_mtu_bytes} bytes) completed the round trip, so no "
                        "MTU constraint is blocking normal-sized traffic."
                    ),
                    kind="observation",
                    strength="strong",
                    weakens=["MTU_BLACK_HOLE"],
                    details={"path_mtu_bytes": state.path_mtu_bytes},
                )
            )
        else:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "The packet-size ladder could not run because no forwarding path exists: "
                        + state.detail
                    ),
                    kind="observation",
                    strength="moderate",
                    supports=["LINK_FAILURE", "ROUTING_FAILURE"],
                )
            )
        return _observation(
            request,
            probe_type=self.probe_type,
            probe_label="Simulated MTU / packet-size ladder",
            outcome=outcome,
            summary=state.detail,
            details=details,
            evidence=evidence,
            elapsed_ms=float(len(state.sizes)) * 2.0,
            wall_clock_ms=clock.ms,
        )


# ---------------------------------------------------------------------------
# 6. Service health
# ---------------------------------------------------------------------------


class ServiceHealthProbe:
    probe_type = ProbeType.SERVICE_HEALTH

    def __init__(self, simulator: LabSimulator) -> None:
        self.simulator = simulator

    def run(self, request: ProbeRequest, lab: Any) -> ProbeObservation:
        del lab
        service_name = request.destination_service
        with _Timed() as clock:
            state = self.simulator.service_probe(
                request.source_node_id,
                request.destination_node_id,
                request.tag,
                service_name=service_name,
                port=request.port,
            )
        details = state.as_details()
        evidence: list[EvidenceStatement] = []
        outcome = state.outcome.value
        if state.outcome.value == "HEALTHY":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The application on {state.destination_ip}:{state.port} returned a "
                        "healthy status response, so the whole stack from the client to the "
                        "service is working."
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["UNKNOWN_OR_MULTIPLE_CAUSES"],
                    details={"port": state.port},
                )
            )
        elif state.outcome.value == "DEGRADED_STALL":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The port {state.port} on {state.destination_ip} accepts connections but "
                        "does not return a healthy response: the service accepts but does not "
                        "serve."
                    ),
                    kind="observation",
                    strength="moderate",
                    supports=["APPLICATION_SERVICE_FAILURE"],
                )
            )
        elif state.outcome.value == "UNAVAILABLE_REFUSED_NO_LISTENER":
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"{state.destination_ip} is reachable and reset the connection to port "
                        f"{state.port}: the network and transport layers are healthy while the "
                        "application process is not serving. That combination points at the "
                        "service rather than at the path."
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["APPLICATION_SERVICE_FAILURE"],
                    details={"port": state.port, "port_policy": state.port_policy},
                )
            )
        elif state.outcome.value in (
            "UNAVAILABLE_REFUSED_POLICY",
            "UNAVAILABLE_TIMEOUT",
        ):
            evidence.append(
                EvidenceStatement(
                    statement=(
                        f"The health request to {state.destination_ip}:{state.port} was "
                        + (
                            "rejected by path policy before reaching the application."
                            if state.outcome.value == "UNAVAILABLE_REFUSED_POLICY"
                            else "silently dropped before the application was reached."
                        )
                    ),
                    kind="observation",
                    strength="moderate",
                    supports=["TCP_FILTER_OR_PORT_FAILURE"],
                    details={"port": state.port, "port_policy": state.port_policy},
                )
            )
        else:
            evidence.append(
                EvidenceStatement(
                    statement=(
                        "The service health check could not reach the application because the "
                        "network path failed first: " + state.detail
                    ),
                    kind="observation",
                    strength="strong",
                    supports=["LINK_FAILURE", "ROUTING_FAILURE"],
                )
            )
        return _observation(
            request,
            probe_type=self.probe_type,
            probe_label="Simulated application health check",
            outcome=outcome,
            summary=state.detail,
            details=details,
            evidence=evidence,
            elapsed_ms=0.0,
            wall_clock_ms=clock.ms,
        )


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def build_probe_registry(simulator: LabSimulator) -> dict[ProbeType, Any]:
    """One instance per probe type, all bound to the same simulator."""
    probes = [
        IcmpReachabilityProbe(simulator),
        DnsLookupProbe(simulator),
        TracerouteProbe(simulator),
        TcpConnectProbe(simulator),
        MtuProbe(simulator),
        ServiceHealthProbe(simulator),
    ]
    return {probe.probe_type: probe for probe in probes}


__all__ = [
    "DnsLookupProbe",
    "IcmpReachabilityProbe",
    "KNOWN_OUTCOMES",
    "MtuProbe",
    "ServiceHealthProbe",
    "TcpConnectProbe",
    "TracerouteProbe",
    "build_probe_registry",
]

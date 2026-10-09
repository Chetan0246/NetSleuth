"""The shared outcome vocabulary for simulated probes.

Keeping every outcome code in one module (instead of ad-hoc strings scattered
through the probes) is what makes the likelihood table in
``app/diagnosis/likelihoods.py`` auditable and testable: the table must contain
a weight for every code listed here.
"""

from __future__ import annotations

from enum import Enum


class ProbeType(str, Enum):
    ICMP_REACHABILITY = "ICMP_REACHABILITY"
    DNS_LOOKUP = "DNS_LOOKUP"
    TRACEROUTE = "TRACEROUTE"
    TCP_CONNECT = "TCP_CONNECT"
    MTU_PROBE = "MTU_PROBE"
    SERVICE_HEALTH = "SERVICE_HEALTH"


class IcmpSelector(str, Enum):
    """Which address an ICMP-style probe targets."""

    DESTINATION = "destination"
    GATEWAY = "gateway"
    CONTROL_DESTINATION = "control_destination"
    RESOLVER = "resolver"


class IcmpOutcome(str, Enum):
    REACHABLE = "REACHABLE"
    REACHABLE_SLOW = "REACHABLE_SLOW"
    PARTIAL_LOSS = "PARTIAL_LOSS"
    TIMEOUT = "TIMEOUT"
    UNREACHABLE_NETWORK = "UNREACHABLE_NETWORK"
    UNREACHABLE_HOST = "UNREACHABLE_HOST"


class DnsOutcome(str, Enum):
    RESOLVED = "RESOLVED"
    RESOLVED_SLOW = "RESOLVED_SLOW"
    TIMEOUT_RESOLVER = "TIMEOUT_RESOLVER"
    NXDOMAIN = "NXDOMAIN"


class TraceOutcome(str, Enum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_SUPPRESSED = "COMPLETE_WITH_SUPPRESSED"
    PARTIAL = "PARTIAL"
    NO_FIRST_HOP = "NO_FIRST_HOP"


class TcpOutcome(str, Enum):
    CONNECTED = "CONNECTED"
    CONNECTED_SLOW = "CONNECTED_SLOW"
    TIMEOUT_DROP = "TIMEOUT_DROP"
    REFUSED_NETWORK_POLICY = "REFUSED_NETWORK_POLICY"
    REFUSED_NO_LISTENER = "REFUSED_NO_LISTENER"
    UNREACHABLE = "UNREACHABLE"


class MtuOutcome(str, Enum):
    FULL_PATH_OK = "FULL_PATH_OK"
    LIMITED_DROP = "LIMITED_DROP"
    LIMITED_REPORTED = "LIMITED_REPORTED"
    #: Failures at sizes that fit the path MTU, with the failure pattern *still*
    #: compatible with an MTU boundary (failures concentrated at the top).
    INCONCLUSIVE_LOSS = "INCONCLUSIVE_LOSS"
    #: A packet size that fits the path MTU *succeeded* while a smaller size failed.
    #: A real MTU boundary is monotone (everything up to the MTU passes), so this
    #: pattern cannot be an MTU limit and is direct evidence of packet loss.
    INCONCLUSIVE_NON_MONOTONE = "INCONCLUSIVE_NON_MONOTONE"
    UNREACHABLE = "UNREACHABLE"


class ServiceOutcome(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED_STALL = "DEGRADED_STALL"
    UNAVAILABLE_REFUSED_POLICY = "UNAVAILABLE_REFUSED_POLICY"
    UNAVAILABLE_REFUSED_NO_LISTENER = "UNAVAILABLE_REFUSED_NO_LISTENER"
    UNAVAILABLE_TIMEOUT = "UNAVAILABLE_TIMEOUT"
    UNREACHABLE = "UNREACHABLE"


OUTCOME_ENUM_BY_PROBE: dict[ProbeType, type[Enum]] = {
    ProbeType.ICMP_REACHABILITY: IcmpOutcome,
    ProbeType.DNS_LOOKUP: DnsOutcome,
    ProbeType.TRACEROUTE: TraceOutcome,
    ProbeType.TCP_CONNECT: TcpOutcome,
    ProbeType.MTU_PROBE: MtuOutcome,
    ProbeType.SERVICE_HEALTH: ServiceOutcome,
}


def outcomes_for(probe_type: ProbeType) -> list[str]:
    """Every outcome code one probe type can emit, from its own enum.

    Derived rather than restated: an earlier revision carried a hand-maintained
    duplicate of this list, which is exactly the kind of second source of truth
    that drifts away from the enums without anything noticing.
    """
    return [item.value for item in OUTCOME_ENUM_BY_PROBE[probe_type]]

#: Relative cost of each probe kind, used by the EIG/cost score in the planner.
#: These are *relative engineering weights* (cheap/medium/expensive), not
#: measured wall-clock times.
PROBE_COSTS: dict[ProbeType, float] = {
    ProbeType.ICMP_REACHABILITY: 1.0,
    ProbeType.DNS_LOOKUP: 1.0,
    ProbeType.TCP_CONNECT: 1.0,
    ProbeType.TRACEROUTE: 2.0,
    ProbeType.SERVICE_HEALTH: 2.0,
    ProbeType.MTU_PROBE: 3.0,
}

PROBE_LABELS: dict[ProbeType, str] = {
    ProbeType.ICMP_REACHABILITY: "ICMP-style reachability check",
    ProbeType.DNS_LOOKUP: "DNS lookup",
    ProbeType.TRACEROUTE: "TTL-limited path trace",
    ProbeType.TCP_CONNECT: "TCP connect attempt",
    ProbeType.MTU_PROBE: "MTU / packet-size ladder",
    ProbeType.SERVICE_HEALTH: "Application service health check",
}

MODE_SIMULATED = "SIMULATED LAB"
MODE_LIVE = "LIVE PROBE"


def probe_key(probe_type: ProbeType, selector: str | None = None) -> str:
    """Canonical key of a probe *candidate* (used by the likelihood table)."""
    if probe_type is ProbeType.ICMP_REACHABILITY:
        return f"{probe_type.value}:{selector or IcmpSelector.DESTINATION.value}"
    return probe_type.value


def probe_key_label(key: str) -> str:
    if ":" in key:
        probe_part, selector = key.split(":", 1)
        try:
            label = PROBE_LABELS[ProbeType(probe_part)]
        except ValueError:  # pragma: no cover - defensive
            label = probe_part
        return f"{label} ({selector})"
    try:
        return PROBE_LABELS[ProbeType(key)]
    except ValueError:  # pragma: no cover - defensive
        return key


#: Canonical candidate order. The *fixed-order baseline* uses exactly this
#: sequence, so the adaptive planner and the baseline draw from an identical
#: candidate set and differ only in the order in which candidates are executed.
CANONICAL_CANDIDATE_ORDER: list[tuple[ProbeType, str | None]] = [
    (ProbeType.ICMP_REACHABILITY, IcmpSelector.DESTINATION.value),
    (ProbeType.ICMP_REACHABILITY, IcmpSelector.GATEWAY.value),
    (ProbeType.ICMP_REACHABILITY, IcmpSelector.RESOLVER.value),
    (ProbeType.DNS_LOOKUP, None),
    (ProbeType.TRACEROUTE, None),
    (ProbeType.TCP_CONNECT, None),
    (ProbeType.MTU_PROBE, None),
    (ProbeType.SERVICE_HEALTH, None),
    (ProbeType.ICMP_REACHABILITY, IcmpSelector.CONTROL_DESTINATION.value),
]

REQUIRED_PROBE_TYPE_COUNT = len({item[0] for item in CANONICAL_CANDIDATE_ORDER})

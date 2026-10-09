"""Fault-hypothesis catalogue (plan.md section 9.1).

A hypothesis is a *possible explanation* for the observed symptom, not a claim
about the lab. Each entry documents:

* a stable code used by the belief updater and the likelihood table,
* the TCP/IP layers involved,
* a readable title and explanation,
* the verification / remediation steps a human would take next,
* which component kinds the hypothesis is about (used by the localizer).

The catalogue deliberately contains ``UNKNOWN_OR_MULTIPLE_CAUSES`` so the engine
is allowed to answer "the evidence does not single out one known cause", and
``NO_FAULT_DETECTED`` so a healthy path is not forced into a fault label.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Hypothesis(str, Enum):
    LINK_FAILURE = "LINK_FAILURE"
    ROUTING_FAILURE = "ROUTING_FAILURE"
    DNS_FAILURE = "DNS_FAILURE"
    PACKET_LOSS = "PACKET_LOSS"
    HIGH_LATENCY = "HIGH_LATENCY"
    MTU_BLACK_HOLE = "MTU_BLACK_HOLE"
    TCP_FILTER_OR_PORT_FAILURE = "TCP_FILTER_OR_PORT_FAILURE"
    APPLICATION_SERVICE_FAILURE = "APPLICATION_SERVICE_FAILURE"
    NO_FAULT_DETECTED = "NO_FAULT_DETECTED"
    UNKNOWN_OR_MULTIPLE_CAUSES = "UNKNOWN_OR_MULTIPLE_CAUSES"


ComponentKind = Literal["link", "node", "service", "none"]


@dataclass(frozen=True)
class HypothesisSpec:
    code: Hypothesis
    title: str
    layers: tuple[str, ...]
    summary: str
    verification_steps: tuple[str, ...]
    remediation: tuple[str, ...]
    component_kind: ComponentKind
    #: Fault types this hypothesis is the expected explanation for. Used by the
    #: evaluator only — the diagnostic engine never reads this field.
    expected_fault_types: tuple[str, ...]


HYPOTHESES: dict[Hypothesis, HypothesisSpec] = {
    Hypothesis.LINK_FAILURE: HypothesisSpec(
        code=Hypothesis.LINK_FAILURE,
        title="Link failure (down or physically unusable link)",
        layers=("Link (L2/L1) / Network (L3)",),
        summary=(
            "A link on the forwarding path is down, so traffic stops at the last "
            "hop before that link in both directions."
        ),
        verification_steps=(
            "Check the interface state and error counters on both ends of the suspected link.",
            "Confirm that other destinations behind the same segment are also unreachable.",
            "Compare a trace to the destination with a trace to a host in another segment.",
        ),
        remediation=(
            "Restore or replace the failed link, or reroute traffic over an alternate path.",
        ),
        component_kind="link",
        expected_fault_types=("LINK_DOWN", "GATEWAY_UNREACHABLE"),
    ),
    Hypothesis.ROUTING_FAILURE: HypothesisSpec(
        code=Hypothesis.ROUTING_FAILURE,
        title="Routing failure (missing route / black hole)",
        layers=("Network (L3)",),
        summary=(
            "The packet is forwarded onto the path but the route to the destination "
            "is absent or points at an unusable next hop, so traffic is silently discarded."
        ),
        verification_steps=(
            "Inspect the forwarding table on the router at the last responding hop.",
            "Check whether the link itself is up while a specific prefix is unreachable.",
            "Compare reachability of a co-located host with reachability of the destination.",
        ),
        remediation=(
            "Reinstall or correct the route, or fix the next-hop reachability.",
        ),
        component_kind="link",
        expected_fault_types=("ROUTE_BLACKHOLE",),
    ),
    Hypothesis.DNS_FAILURE: HypothesisSpec(
        code=Hypothesis.DNS_FAILURE,
        title="DNS resolution failure",
        layers=("Application (L7) / DNS",),
        summary=(
            "Name resolution fails while the IP path to the service is usable, so the "
            "connection never reaches the server by name."
        ),
        verification_steps=(
            "Query the resolver directly for the record and check for a timeout or NXDOMAIN.",
            "Test the service by IP address to separate name resolution from reachability.",
            "Check the resolver host's daemon state and whether other names also fail.",
        ),
        remediation=(
            "Restore the resolver service, correct the zone entry, or configure a "
            "working secondary resolver.",
        ),
        component_kind="node",
        expected_fault_types=("DNS_FAILURE",),
    ),
    Hypothesis.PACKET_LOSS: HypothesisSpec(
        code=Hypothesis.PACKET_LOSS,
        title="Packet loss on the path",
        layers=("Network (L3) / Link (L2)",),
        summary=(
            "A link drops a fraction of packets, so some probes succeed and others "
            "time out instead of failing consistently."
        ),
        verification_steps=(
            "Repeat the probe several times and measure the loss ratio rather than a single result.",
            "Look for a hop in the path trace that answers only intermittently.",
            "Check interface error/discard counters on the suspected link.",
        ),
        remediation=(
            "Replace the faulty medium or cable, or reduce congestion on the affected link.",
        ),
        component_kind="link",
        expected_fault_types=("PACKET_LOSS",),
    ),
    Hypothesis.HIGH_LATENCY: HypothesisSpec(
        code=Hypothesis.HIGH_LATENCY,
        title="High latency on the path",
        layers=("Network (L3) / Link (L2)",),
        summary=(
            "Packets are delivered, but the path adds so much delay that round-trip "
            "times and handshakes are far above the established baseline."
        ),
        verification_steps=(
            "Measure RTT to the destination and to intermediate hops to find where the delay appears.",
            "Compare the measured RTT with the baseline measured when the path was known good.",
        ),
        remediation=(
            "Remove the artificial delay or queueing source; if the delay is real, choose a "
            "closer path or endpoint.",
        ),
        component_kind="link",
        expected_fault_types=("HIGH_LATENCY",),
    ),
    Hypothesis.MTU_BLACK_HOLE: HypothesisSpec(
        code=Hypothesis.MTU_BLACK_HOLE,
        title="Path-MTU black hole",
        layers=("Network (L3) / MTU & fragmentation",),
        summary=(
            "The path MTU is smaller than the endpoint's assumed MTU and the oversized "
            "datagrams are dropped without an ICMP fragmentation-needed reply, so small "
            "probes work while full-size traffic disappears."
        ),
        verification_steps=(
            "Probe with a ladder of packet sizes and find the largest size that completes.",
            "Check whether ICMP fragmentation-needed messages are returned or filtered.",
            "Verify the MTU configured on every link of the path.",
        ),
        remediation=(
            "Lower the interface or tunnel MTU, enable PMTUD properly, or allow ICMP type 3 "
            "code 4 through the filtering device.",
        ),
        component_kind="link",
        expected_fault_types=("MTU_BLACK_HOLE",),
    ),
    Hypothesis.TCP_FILTER_OR_PORT_FAILURE: HypothesisSpec(
        code=Hypothesis.TCP_FILTER_OR_PORT_FAILURE,
        title="TCP port blocked or filtered",
        layers=("Transport (L4)",),
        summary=(
            "The host is reachable, but the destination port does not complete a TCP "
            "handshake, either because it is silently dropped or actively rejected."
        ),
        verification_steps=(
            "Compare the handshake outcome for the target port with the outcome for another port.",
            "Check the firewall policy on the path and on the destination host.",
            "Distinguish a timeout (silent drop) from an immediate reset (active rejection).",
        ),
        remediation=(
            "Correct the firewall rule or access-control list so the intended port is permitted.",
        ),
        component_kind="service",
        expected_fault_types=("TCP_PORT_BLOCKED", "TCP_PORT_REJECTED"),
    ),
    Hypothesis.APPLICATION_SERVICE_FAILURE: HypothesisSpec(
        code=Hypothesis.APPLICATION_SERVICE_FAILURE,
        title="Application service failure",
        layers=("Application (L7)",),
        summary=(
            "IP reachability and the transport port are healthy, but the application "
            "process is not serving requests."
        ),
        verification_steps=(
            "Check whether the service process is running and listening on the expected port.",
            "Request the service health endpoint directly and inspect the response.",
            "Review the application log for a crash or a failed dependency.",
        ),
        remediation=(
            "Restart or repair the application service, or fail over to a healthy instance.",
        ),
        component_kind="service",
        expected_fault_types=("SERVICE_DOWN",),
    ),
    Hypothesis.NO_FAULT_DETECTED: HypothesisSpec(
        code=Hypothesis.NO_FAULT_DETECTED,
        title="No fault detected in the probed path",
        layers=("All layers",),
        summary=(
            "Every executed probe reported a healthy result, so the lab is not currently "
            "exhibiting a fault for this source, destination and service."
        ),
        verification_steps=(
            "Confirm the source, destination and service actually match the reported problem.",
            "Re-run the diagnosis if a fault was injected while the diagnosis was running.",
        ),
        remediation=("No action required for the probed path.",),
        component_kind="none",
        expected_fault_types=(),
    ),
    Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES: HypothesisSpec(
        code=Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES,
        title="Inconclusive: unknown or multiple causes",
        layers=("All layers",),
        summary=(
            "The observations do not single out one known single-fault explanation, or "
            "they are consistent with more than one cause at once."
        ),
        verification_steps=(
            "Collect additional evidence of a different class (for example a capture or a "
            "device log) rather than repeating the same probes.",
            "Investigate whether more than one fault is active simultaneously.",
        ),
        remediation=(
            "No single remediation is recommended while the cause is unresolved.",
        ),
        component_kind="none",
        expected_fault_types=(),
    ),
}


#: Documented prior probabilities. Deliberately uniform: the engine has no
#: reliable frequency data for this lab, and inventing unequal priors would put
#: unverifiable assumptions into the result. All values are stored explicitly so
#: an experiment export can identify the prior revision (config.PRIOR_CONFIG_VERSION).
UNIFORM_PRIOR: float = 1.0 / len(HYPOTHESES)

DEFAULT_PRIORS: dict[Hypothesis, float] = {code: UNIFORM_PRIOR for code in HYPOTHESES}


def hypothesis_spec(code: Hypothesis | str) -> HypothesisSpec:
    return HYPOTHESES[Hypothesis(code)]


def hypothesis_public_catalogue() -> list[dict[str, object]]:
    """Serialisable catalogue for the API (used by the UI's hypothesis panel)."""
    return [
        {
            "code": spec.code.value,
            "title": spec.title,
            "layers": list(spec.layers),
            "summary": spec.summary,
            "component_kind": spec.component_kind,
            "verification_steps": list(spec.verification_steps),
            "remediation": list(spec.remediation),
        }
        for spec in HYPOTHESES.values()
    ]


__all__ = [
    "ComponentKind",
    "DEFAULT_PRIORS",
    "HYPOTHESES",
    "UNIFORM_PRIOR",
    "Hypothesis",
    "HypothesisSpec",
    "hypothesis_public_catalogue",
    "hypothesis_spec",
]

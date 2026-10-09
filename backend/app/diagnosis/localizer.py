"""Component localization: turn evidence into "the fault is here".

A diagnosis that only says *what* class of fault is present is less useful than
one that also points at the component and explains how confident it is about the
location. This module extracts a location from the structured observation details
that the probes already record:

* ``suspect_link`` — the first link on the nominal path that could not be
  traversed (traceroute and the forwarding layer both record it).
* ``last_reachable_node`` — the furthest node that answered, which brackets the
  suspect link.
* ``limiting_link`` — the link whose MTU constrained the path.
* ``port_policy`` / ``destination_node_id`` — for service- and port-scoped faults.

Location confidence is reported **separately** from cause confidence, and is
``none``/``weak`` when the evidence does not distinguish a component: for a DNS
outage the component is the resolver (a node), while for packet loss the location
is genuinely ambiguous when several links carry the flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..probes.base import ProbeObservation
from .hypotheses import Hypothesis, hypothesis_spec

LocationConfidence = Literal["none", "weak", "moderate", "strong"]


@dataclass
class Localization:
    """A suspected component with the evidence that points at it."""

    component_kind: Literal["link", "node", "service", "none"]
    component_id: str | None
    confidence: LocationConfidence
    evidence: list[str] = field(default_factory=list)
    bracketing: dict[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.component_id is not None and self.confidence != "none"

    def label(self) -> str:
        if not self.component_id:
            return "no single component identified"
        return f"{self.component_kind} {self.component_id}"

    def to_public(self) -> dict[str, Any]:
        return {
            "component_kind": self.component_kind,
            "component_id": self.component_id,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "bracketing": self.bracketing,
        }


def _first(mapping: list[dict[str, Any]], key: str) -> Any:
    for item in mapping:
        value = item.get(key)
        if value:
            return value
    return None


def localize(
    leader: Hypothesis,
    observations: list[ProbeObservation],
    *,
    port: int | None = None,
) -> Localization:
    """Derive the suspected component from the recorded observations.

    Only observations that *can* carry location information for the leading
    hypothesis are consulted, so a link picked up by an unrelated probe cannot
    contaminate the answer.
    """
    if not observations:
        return Localization("none", None, "none")
    spec = hypothesis_spec(leader)
    by_probe = {observation.probe_key: observation for observation in observations}
    details = [observation.details for observation in observations]

    if spec.component_kind == "none":
        if leader is Hypothesis.NO_FAULT_DETECTED:
            return Localization(
                "none",
                None,
                "none",
                evidence=[
                    "Every probe reported a healthy result, so no component is implicated."
                ],
            )
        return Localization(
            "none",
            None,
            "none",
            evidence=[
                "The cause itself is unresolved, so no component can be named without "
                "speculating."
            ],
        )

    if spec.component_kind == "node":
        # A DNS failure is located at the resolver host that failed to answer.
        resolver = _first(details, "resolver_node_id")
        if resolver is None:
            for observation in observations:
                if observation.probe_key.startswith("ICMP_REACHABILITY:resolver"):
                    resolver = observation.destination_node_id
                    break
        if resolver is None:
            return Localization(
                "node",
                None,
                "none",
                evidence=["No resolver host was identified in the probe details."],
            )
        supporting = [
            observation.probe_label
            for observation in observations
            if observation.details.get("resolver_node_id") == resolver
            or observation.probe_key.startswith("ICMP_REACHABILITY:resolver")
        ]
        return Localization(
            "node",
            resolver,
            "strong" if supporting else "moderate",
            evidence=[
                f"{label} referenced resolver host {resolver}."
                for label in supporting
            ]
            or [f"The resolver host {resolver} is the component the leading cause is about."],
        )

    if spec.component_kind == "service":
        # Service- and port-scoped causes are located at the destination host and
        # port, which the transport and health probes both record.
        node_id = _first(details, "destination_node_id")
        observed_port = _first(details, "port") or port
        if node_id is None:
            return Localization("service", None, "none")
        target = f"{node_id}:{observed_port}" if observed_port else node_id
        port_policy = _first(details, "port_policy")
        evidence = [
            f"{observation.probe_label} reported port policy {observation.details.get('port_policy')} "
            f"for port {observation.details.get('port')}."
            for observation in observations
            if observation.details.get("port_policy") not in (None, "open")
        ]
        return Localization(
            "service",
            target,
            "strong" if port_policy and port_policy != "open" else "moderate",
            evidence=evidence
            or [f"The cause is scoped to the service on {node_id} (port {observed_port})."],
            bracketing={"destination_node_id": node_id, "port": observed_port,
                        "port_policy": port_policy},
        )

    # ---- link-scoped causes -------------------------------------------------
    trace = by_probe.get("TRACEROUTE")
    icmp = by_probe.get("ICMP_REACHABILITY:destination") or by_probe.get(
        "ICMP_REACHABILITY:gateway"
    )
    suspect_link = (
        (trace.details.get("suspect_link") if trace else None)
        or (icmp.details.get("suspect_link") if icmp else None)
        or _first(details, "suspect_link")
    )
    last_node = (
        (trace.details.get("last_responding_node") if trace else None)
        or (icmp.details.get("last_reachable_node") if icmp else None)
        or _first(details, "last_reachable_node")
    )
    evidence: list[str] = []
    confidence: LocationConfidence = "none"

    if suspect_link:
        if trace is not None and trace.outcome in ("PARTIAL", "NO_FIRST_HOP"):
            confidence = "strong"
            evidence.append(
                f"The path trace stopped replying after hop "
                f"{trace.details.get('last_responding_hop')}, localizing the block to link "
                f"{suspect_link}."
            )
        else:
            confidence = "moderate"
            evidence.append(
                f"The forwarding layer reported link {suspect_link} as the first unusable link "
                "on the nominal path."
            )
        if last_node:
            evidence.append(
                f"The furthest reachable node was {last_node}, which brackets the fault between "
                f"that node and the far side of {suspect_link}."
            )
    else:
        # No explicit suspect link: an MTU or latency fault can still name the
        # constraining link, otherwise the location is genuinely ambiguous.
        limiting = _first(details, "limiting_link")
        if leader is Hypothesis.MTU_BLACK_HOLE and limiting:
            return Localization(
                "link",
                limiting,
                "strong",
                evidence=[
                    f"The packet-size ladder identified {limiting} as the link whose MTU "
                    "constrains the path."
                ],
            )
        if leader is Hypothesis.PACKET_LOSS and last_node:
            return Localization(
                "link",
                None,
                "weak",
                evidence=[
                    f"Loss was observed on the flow to the destination (last responding node "
                    f"{last_node}), but link-level loss cannot be attributed to one link from "
                    "end-to-end probes alone."
                ],
            )
        return Localization(
            "link",
            None,
            "none",
            evidence=[
                "No single link could be identified as the suspect from the executed probes."
            ],
        )
    return Localization(
        "link",
        suspect_link,
        confidence,
        evidence=evidence,
        bracketing={"last_reachable_node": last_node},
    )


__all__ = ["Localization", "LocationConfidence", "localize"]

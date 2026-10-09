"""The explicit likelihood model: P(observation | hypothesis, probe).

This module is the single source of truth for how strongly each observation
supports or weakens each hypothesis. Nothing here is learned or fitted to the
evaluation scenarios: every number is a documented protocol-reasoning judgement,
and the whole table is *data* (not code) so it can be reviewed, diffed and tested
(see docs/diagnostic-algorithm.md and docs/experiment-methodology.md).

How to read a row
-----------------
A row answers one question: *if hypothesis H were true, how likely is this
outcome?* The table is organised by probe, then outcome, then hypothesis. Only the
pairs that carry real signal are written out; every unmentioned pair takes
:data:`DEFAULT_LIKELIHOOD`.

============================  ====================================================
weight                        meaning
============================  ====================================================
``0.02 - 0.05``               observed outcome is close to impossible under H: a
                              direct contradiction (e.g. a completed handshake
                              while a port-filter fault is claimed)
``0.15 - 0.5``                strongly disfavoured, but not impossible
``~0.9`` (the default)        neutral — the observation is about a different
                              layer than H and carries almost no information
``2 - 4``                     clear support
``5 - 9``                     the observation is close to the defining signature
                              of H
============================  ====================================================

Why the default is close to 1.0
-------------------------------
A probe observation is informative about *the layer it tests*. A successful echo
request rules out a down link, but it is entirely consistent with a DNS outage, a
blocked port or an MTU black hole. If the default were small (say 0.02), that one
healthy result would make almost every hypothesis look impossible at once and the
diagnosis would be "confident" after a single probe — precisely the failure mode
that makes an automatic diagnosis untrustworthy. With a neutral default, a
*pattern* of observations is what produces confidence, which is also why the
number of probes to reach a decision is a meaningful metric in the evaluation.

Conventions
-----------
* A likelihood of ``1.0`` means "this hypothesis does not change the probability
  of this observation at all".
* A likelihood above the hypothesis's likelihood for other outcomes of the same
  probe makes this observation *raise* that hypothesis.
* The values are relative weights, not calibrated probabilities of a generative
  model; the Bayes update in :mod:`app.diagnosis.bayes` normalises them.

Probe keys are ``PROBE_TYPE`` or ``PROBE_TYPE:selector`` (see
:func:`app.lab.outcomes.probe_key`). Outcome codes are the literals from
:mod:`app.lab.outcomes`; :func:`validate_likelihood_table` asserts that the table
covers every outcome the probes can actually emit.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import ValidationError
from ..lab.outcomes import CANONICAL_CANDIDATE_ORDER, probe_key as make_probe_key
from .hypotheses import Hypothesis

H = Hypothesis

#: Likelihood applied to any pair a row does not explicitly mention. See the
#: module docstring: deliberately near-neutral, so an observation only moves the
#: hypotheses whose layer it actually tests.
DEFAULT_LIKELIHOOD = 0.9

#: likelihood > 1 raised the hypothesis; < 1 lowered it.
#:
#: The coherence adjustment
#: -----------------------
#: ``UNKNOWN_OR_MULTIPLE_CAUSES`` means "the evidence does not point at one known single
#: cause". Without explicit modelling it is *unfalsifiable*: it is consistent with
#: every observation, so it permanently soaks up probability mass and a textbook
#: single fault can never clear the confidence threshold. That is not caution, it is
#: a modelling omission.
#:
#: The correction encodes one observable principle: a probe outcome that is a
#: *coherent, specific* reading makes "something outside the catalogue" less likely,
#: while an *ambiguous* reading makes it more likely. So each outcome carries one
#: multiplier for UNKNOWN and one for NO_FAULT_DETECTED ...
#:
#: * outcomes like ``REACHABLE`` or ``HEALTHY`` are specific and mutually
#:   consistent, so they push probability *toward* a single explanation;
#: * outcomes like ``TIMEOUT`` or ``PARTIAL`` are compatible with several causes at
#:   once, so they push probability *toward* "unknown / multiple".
#:
#: These two numbers per outcome are applied for every probe, which keeps the
#: adjustment auditable in one place instead of scattered across 50 rows. They are
#: documented protocol-reasoning weights and were not fitted to the evaluation
#: scenarios.
_COHERENCE: dict[str, tuple[float, float]] = {
    # outcome: (UNKNOWN_OR_MULTIPLE_CAUSES multiplier, NO_FAULT_DETECTED multiplier)
    "REACHABLE": (0.60, 1.60),
    "REACHABLE_SLOW": (1.30, 0.50),
    "RESOLVED": (0.60, 1.60),
    "RESOLVED_SLOW": (1.30, 0.50),
    "CONNECTED": (0.60, 1.60),
    "CONNECTED_SLOW": (1.30, 0.50),
    "HEALTHY": (0.60, 1.60),
    "COMPLETE": (0.60, 1.60),
    "FULL_PATH_OK": (0.60, 1.60),
    "NXDOMAIN": (0.55, 0.50),
    "LIMITED_DROP": (0.55, 0.50),
    "LIMITED_REPORTED": (0.90, 0.70),
    # Ambiguous readings: more than one cause explains them equally well.
    "PARTIAL_LOSS": (1.30, 0.40),
    "COMPLETE_WITH_SUPPRESSED": (1.30, 0.50),
    "INCONCLUSIVE_LOSS": (1.30, 0.50),
    "INCONCLUSIVE_NON_MONOTONE": (0.50, 0.40),
    "DEGRADED_STALL": (1.10, 0.50),
    "TIMEOUT": (1.20, 0.30),
    "TIMEOUT_DROP": (1.20, 0.30),
    "TIMEOUT_RESOLVER": (1.10, 0.30),
    "PARTIAL": (1.20, 0.30),
    "NO_FIRST_HOP": (1.10, 0.30),
    "UNREACHABLE": (1.20, 0.30),
    "UNREACHABLE_NETWORK": (1.20, 0.30),
    "UNREACHABLE_HOST": (1.20, 0.30),
    "REFUSED_NETWORK_POLICY": (1.10, 0.30),
    "REFUSED_NO_LISTENER": (1.10, 0.30),
    "UNAVAILABLE_TIMEOUT": (1.20, 0.30),
    "UNAVAILABLE_REFUSED_POLICY": (1.10, 0.30),
    "UNAVAILABLE_REFUSED_NO_LISTENER": (1.10, 0.30),
}
#:
#: Two modelling decisions deserve to be stated explicitly, because they are what
#: make the engine's answers honest rather than merely confident:
#:
#: 1. **A probe is only informative about the layer it tests.** Hence the neutral
#:    default: a healthy echo request rules out a down link, but it is fully
#:    consistent with a DNS outage, a blocked port or an MTU black hole. No single
#:    successful probe is allowed to collapse the whole distribution.
#:
#: 2. **``LINK_FAILURE`` and ``ROUTING_FAILURE`` are separated by whether a router
#:    answered with an ICMP error, not by whether a trace stopped.** This mirrors
#:    the real diagnostic problem: a silent drop is consistent with both a dead
#:    link and a withdrawn route, while an ICMP network-unreachable error comes
#:    from a live router and therefore favours the routing hypothesis. The two
#:    hypotheses stay close together on traceroute evidence alone, and the
#:    gateway/control-destination selectors are what break the tie.
_ROWS: dict[str, dict[str, dict[Hypothesis, float]]] = {
    # ------------------------------------------------------------------ ICMP
    "ICMP_REACHABILITY:destination": {
        "REACHABLE": {
            H.LINK_FAILURE: 0.01,
            H.ROUTING_FAILURE: 0.03,
            H.PACKET_LOSS: 0.35,
            H.HIGH_LATENCY: 0.45,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
            H.NO_FAULT_DETECTED: 1.8,
        },
        "REACHABLE_SLOW": {
            H.LINK_FAILURE: 0.01,
            H.ROUTING_FAILURE: 0.03,
            H.PACKET_LOSS: 0.3,
            H.HIGH_LATENCY: 4.5,
            H.NO_FAULT_DETECTED: 0.2,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "PARTIAL_LOSS": {
            H.LINK_FAILURE: 0.15,
            H.ROUTING_FAILURE: 0.1,
            H.PACKET_LOSS: 4.0,
            H.HIGH_LATENCY: 0.5,
            H.MTU_BLACK_HOLE: 1.1,
            H.NO_FAULT_DETECTED: 0.005,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "TIMEOUT": {
            # A silent drop with no ICMP error. Under the lab's documented semantics a
            # withdrawn route drops silently while a down link produces an ICMP
            # error, so silence favours the routing hypothesis. Packet loss and a
            # suppressed-ICMP down link remain live alternatives, so the two stay
            # within a factor of two and the run needs corroborating evidence.
            H.LINK_FAILURE: 0.8,
            H.ROUTING_FAILURE: 2.4,
            H.DNS_FAILURE: 0.4,
            H.PACKET_LOSS: 1.6,
            H.HIGH_LATENCY: 0.05,
            H.MTU_BLACK_HOLE: 0.5,
            H.TCP_FILTER_OR_PORT_FAILURE: 0.6,
            H.APPLICATION_SERVICE_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 0.001,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.9,
        },
        "UNREACHABLE_NETWORK": {
            # An ICMP network-unreachable error is *generated by a router that is
            # alive*: the lab documents that a down link produces an ICMP error
            # while a withdrawn route (black hole) is silent. The error therefore
            # points at the link hypothesis, not at the routing hypothesis.
            H.LINK_FAILURE: 3.6,
            H.ROUTING_FAILURE: 1.2,
            H.PACKET_LOSS: 0.5,
            H.HIGH_LATENCY: 0.05,
            H.MTU_BLACK_HOLE: 0.2,
            H.DNS_FAILURE: 0.3,
            H.NO_FAULT_DETECTED: 0.001,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
        "UNREACHABLE_HOST": {
            # Generated at the final hop: forwarding worked for the whole path except
            # the last link, which is a strong localization signal.
            H.LINK_FAILURE: 3.6,
            H.ROUTING_FAILURE: 0.8,
            H.PACKET_LOSS: 0.4,
            H.HIGH_LATENCY: 0.05,
            H.MTU_BLACK_HOLE: 0.2,
            H.NO_FAULT_DETECTED: 0.001,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
    },
    "ICMP_REACHABILITY:gateway": {
        # Isolates the first hop / local segment.
        "REACHABLE": {
            H.LINK_FAILURE: 0.3,
            H.ROUTING_FAILURE: 0.6,
            H.PACKET_LOSS: 0.7,
            H.HIGH_LATENCY: 0.7,
            H.NO_FAULT_DETECTED: 1.4,
        },
        "REACHABLE_SLOW": {
            H.LINK_FAILURE: 0.3,
            H.ROUTING_FAILURE: 0.6,
            H.PACKET_LOSS: 0.5,
            H.HIGH_LATENCY: 2.4,
            H.NO_FAULT_DETECTED: 0.3,
        },
        "PARTIAL_LOSS": {
            H.LINK_FAILURE: 0.4,
            H.ROUTING_FAILURE: 0.5,
            H.PACKET_LOSS: 3.2,
            H.HIGH_LATENCY: 0.5,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "TIMEOUT": {
            # Nothing on the local segment answered: the source's own connectivity is
            # the prime suspect, and a shared transit fault is ruled out because the
            # gateway is one hop away.
            H.LINK_FAILURE: 4.2,
            H.ROUTING_FAILURE: 0.7,
            H.PACKET_LOSS: 1.6,
            H.HIGH_LATENCY: 0.1,
            H.MTU_BLACK_HOLE: 0.3,
            H.DNS_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
        "UNREACHABLE_NETWORK": {
            H.LINK_FAILURE: 4.5,
            H.ROUTING_FAILURE: 0.7,
            H.PACKET_LOSS: 1.4,
            H.HIGH_LATENCY: 0.1,
            H.DNS_FAILURE: 0.4,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
        "UNREACHABLE_HOST": {
            H.LINK_FAILURE: 4.2,
            H.ROUTING_FAILURE: 0.7,
            H.PACKET_LOSS: 1.4,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
    },
    "ICMP_REACHABILITY:resolver": {
        # Tests the resolver *host and its path*, not the resolver daemon. A host
        # that answers echo tells us nothing about whether its name-server process
        # is running, so a successful ping is deliberately NEUTRAL for
        # DNS_FAILURE — treating it as counter-evidence was a modelling error that
        # rewarded the planner for re-pinging the same host.
        "REACHABLE": {
            H.DNS_FAILURE: 1.0,
            H.LINK_FAILURE: 0.2,
            H.ROUTING_FAILURE: 0.4,
            H.PACKET_LOSS: 0.7,
            H.NO_FAULT_DETECTED: 1.2,
        },
        "REACHABLE_SLOW": {
            H.DNS_FAILURE: 1.0,
            H.HIGH_LATENCY: 2.2,
            H.PACKET_LOSS: 0.6,
            H.NO_FAULT_DETECTED: 0.3,
        },
        "PARTIAL_LOSS": {
            H.DNS_FAILURE: 1.1,
            H.PACKET_LOSS: 3.0,
            H.LINK_FAILURE: 0.4,
            H.ROUTING_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "TIMEOUT": {
            H.DNS_FAILURE: 2.6,
            H.LINK_FAILURE: 2.0,
            H.ROUTING_FAILURE: 2.0,
            H.PACKET_LOSS: 1.2,
            H.HIGH_LATENCY: 0.1,
            H.MTU_BLACK_HOLE: 0.3,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
        },
        "UNREACHABLE_NETWORK": {
            H.DNS_FAILURE: 2.4,
            H.LINK_FAILURE: 2.0,
            H.ROUTING_FAILURE: 2.2,
            H.PACKET_LOSS: 1.1,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
        },
        "UNREACHABLE_HOST": {
            H.DNS_FAILURE: 2.4,
            H.LINK_FAILURE: 2.6,
            H.ROUTING_FAILURE: 1.4,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
        },
    },
    "ICMP_REACHABILITY:control_destination": {
        # An independent target in another segment: answering means the fault is
        # destination-specific, failing means it is shared/near the source.
        "REACHABLE": {
            H.LINK_FAILURE: 0.1,
            H.ROUTING_FAILURE: 0.2,
            H.PACKET_LOSS: 0.4,
            H.HIGH_LATENCY: 0.6,
            H.NO_FAULT_DETECTED: 1.3,
        },
        "REACHABLE_SLOW": {
            H.HIGH_LATENCY: 2.2,
            H.LINK_FAILURE: 0.1,
            H.ROUTING_FAILURE: 0.2,
            H.PACKET_LOSS: 0.4,
            H.NO_FAULT_DETECTED: 0.3,
        },
        "PARTIAL_LOSS": {
            H.PACKET_LOSS: 3.0,
            H.LINK_FAILURE: 0.3,
            H.ROUTING_FAILURE: 0.3,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "TIMEOUT": {
            H.LINK_FAILURE: 3.6,
            H.ROUTING_FAILURE: 1.0,
            H.PACKET_LOSS: 1.4,
            H.HIGH_LATENCY: 0.1,
            H.NO_FAULT_DETECTED: 0.005,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
        },
        "UNREACHABLE_NETWORK": {
            H.LINK_FAILURE: 3.8,
            H.ROUTING_FAILURE: 1.1,
            H.PACKET_LOSS: 1.3,
            H.NO_FAULT_DETECTED: 0.005,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
        },
        "UNREACHABLE_HOST": {
            H.LINK_FAILURE: 3.6,
            H.ROUTING_FAILURE: 1.0,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.8,
        },
    },
    # ------------------------------------------------------------------- DNS
    "DNS_LOOKUP": {
        "RESOLVED": {
            H.DNS_FAILURE: 0.005,
            H.LINK_FAILURE: 0.3,
            H.ROUTING_FAILURE: 0.4,
            H.PACKET_LOSS: 0.6,
            H.HIGH_LATENCY: 0.5,
            H.NO_FAULT_DETECTED: 1.5,
        },
        "RESOLVED_SLOW": {
            H.DNS_FAILURE: 0.02,
            H.HIGH_LATENCY: 2.2,
            H.PACKET_LOSS: 0.5,
            H.NO_FAULT_DETECTED: 0.25,
        },
        "TIMEOUT_RESOLVER": {
            H.DNS_FAILURE: 5.0,
            H.LINK_FAILURE: 0.8,
            H.ROUTING_FAILURE: 0.8,
            H.PACKET_LOSS: 1.2,
            H.HIGH_LATENCY: 0.1,
            H.MTU_BLACK_HOLE: 0.3,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
        "NXDOMAIN": {
            H.DNS_FAILURE: 7.0,
            H.LINK_FAILURE: 0.1,
            H.ROUTING_FAILURE: 0.1,
            H.PACKET_LOSS: 0.2,
            H.NO_FAULT_DETECTED: 0.005,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.5,
        },
    },
    # ------------------------------------------------------------ traceroute
    "TRACEROUTE": {
        "COMPLETE": {
            H.LINK_FAILURE: 0.03,
            H.ROUTING_FAILURE: 0.06,
            H.PACKET_LOSS: 0.5,
            H.HIGH_LATENCY: 0.8,
            H.NO_FAULT_DETECTED: 1.8,
        },
        "COMPLETE_WITH_SUPPRESSED": {
            H.LINK_FAILURE: 0.12,
            H.ROUTING_FAILURE: 0.1,
            H.PACKET_LOSS: 2.6,
            H.HIGH_LATENCY: 0.9,
            H.NO_FAULT_DETECTED: 0.2,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
        "PARTIAL": {
            # The trail stops after the last responding hop. This localizes the
            # problem but does not by itself separate a dead link from a withdrawn
            # route, so both hypotheses stay high and close together.
            H.LINK_FAILURE: 3.4,
            H.ROUTING_FAILURE: 3.2,
            H.PACKET_LOSS: 1.3,
            H.HIGH_LATENCY: 0.15,
            H.MTU_BLACK_HOLE: 0.4,
            H.DNS_FAILURE: 0.6,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
        "NO_FIRST_HOP": {
            H.LINK_FAILURE: 4.4,
            H.ROUTING_FAILURE: 0.8,
            H.PACKET_LOSS: 1.2,
            H.HIGH_LATENCY: 0.1,
            H.DNS_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
    },
    # ------------------------------------------------------------ tcp connect
    "TCP_CONNECT": {
        "CONNECTED": {
            H.TCP_FILTER_OR_PORT_FAILURE: 0.005,
            H.LINK_FAILURE: 0.02,
            H.ROUTING_FAILURE: 0.03,
            H.PACKET_LOSS: 0.4,
            H.HIGH_LATENCY: 0.35,
            H.APPLICATION_SERVICE_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 1.5,
        },
        "CONNECTED_SLOW": {
            H.TCP_FILTER_OR_PORT_FAILURE: 0.03,
            H.LINK_FAILURE: 0.01,
            H.ROUTING_FAILURE: 0.03,
            H.PACKET_LOSS: 0.4,
            H.HIGH_LATENCY: 3.4,
            H.APPLICATION_SERVICE_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 0.12,
        },
        "TIMEOUT_DROP": {
            H.TCP_FILTER_OR_PORT_FAILURE: 3.6,
            H.PACKET_LOSS: 1.4,
            H.LINK_FAILURE: 0.7,
            H.ROUTING_FAILURE: 1.0,
            H.APPLICATION_SERVICE_FAILURE: 0.8,
            H.HIGH_LATENCY: 0.05,
            H.MTU_BLACK_HOLE: 0.5,
            H.DNS_FAILURE: 0.6,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.9,
        },
        "REFUSED_NETWORK_POLICY": {
            H.TCP_FILTER_OR_PORT_FAILURE: 5.0,
            H.APPLICATION_SERVICE_FAILURE: 0.8,
            H.PACKET_LOSS: 0.2,
            H.LINK_FAILURE: 0.2,
            H.ROUTING_FAILURE: 0.1,
            H.NO_FAULT_DETECTED: 0.005,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.5,
        },
        "REFUSED_NO_LISTENER": {
            # The destination host reset the connection, so the host and its route
            # are healthy while nothing is listening. The service hypothesis predicts
            # this directly; the port-filter hypothesis only predicts it through a
            # service-down policy, so it is weighted lower.
            H.APPLICATION_SERVICE_FAILURE: 4.5,
            H.TCP_FILTER_OR_PORT_FAILURE: 1.2,
            H.LINK_FAILURE: 0.05,
            H.ROUTING_FAILURE: 0.05,
            H.PACKET_LOSS: 0.2,
            H.HIGH_LATENCY: 0.2,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "UNREACHABLE": {
            H.LINK_FAILURE: 2.6,
            H.ROUTING_FAILURE: 2.8,
            H.PACKET_LOSS: 0.9,
            H.HIGH_LATENCY: 0.1,
            H.MTU_BLACK_HOLE: 0.5,
            H.DNS_FAILURE: 0.5,
            H.TCP_FILTER_OR_PORT_FAILURE: 0.5,
            H.APPLICATION_SERVICE_FAILURE: 0.4,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
    },
    # -------------------------------------------------------------- mtu probe
    "MTU_PROBE": {
        "FULL_PATH_OK": {
            H.MTU_BLACK_HOLE: 0.02,
            H.PACKET_LOSS: 0.3,
            H.LINK_FAILURE: 0.2,
            H.ROUTING_FAILURE: 0.3,
            H.NO_FAULT_DETECTED: 1.5,
        },
        "LIMITED_DROP": {
            H.MTU_BLACK_HOLE: 9.0,
            H.PACKET_LOSS: 0.3,
            H.LINK_FAILURE: 0.05,
            H.ROUTING_FAILURE: 0.1,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.4,
        },
        "LIMITED_REPORTED": {
            H.MTU_BLACK_HOLE: 2.4,
            H.PACKET_LOSS: 0.4,
            H.NO_FAULT_DETECTED: 0.05,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "INCONCLUSIVE_LOSS": {
            H.PACKET_LOSS: 4.0,
            H.MTU_BLACK_HOLE: 0.5,
            H.LINK_FAILURE: 0.9,
            H.ROUTING_FAILURE: 0.9,
            H.HIGH_LATENCY: 0.5,
            H.NO_FAULT_DETECTED: 0.02,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 1.3,
        },
        "INCONCLUSIVE_NON_MONOTONE": {
            # A size inside the path MTU passed while a smaller one failed on every
            # attempt. An MTU boundary is monotone by definition, so this observation
            # is *direct* evidence of loss and strongly weakens the MTU hypothesis.
            # This is the row that separates a lossy link from a real path-MTU black
            # hole on the same packet-size ladder.
            H.PACKET_LOSS: 6.0,
            H.MTU_BLACK_HOLE: 0.12,
            H.LINK_FAILURE: 0.6,
            H.ROUTING_FAILURE: 0.6,
            H.HIGH_LATENCY: 0.4,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.4,
        },
        "UNREACHABLE": {
            H.LINK_FAILURE: 1.8,
            H.ROUTING_FAILURE: 2.0,
            H.PACKET_LOSS: 1.0,
            H.MTU_BLACK_HOLE: 0.05,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
    },
    # --------------------------------------------------------- service health
    "SERVICE_HEALTH": {
        "HEALTHY": {
            H.APPLICATION_SERVICE_FAILURE: 0.005,
            H.TCP_FILTER_OR_PORT_FAILURE: 0.02,
            H.LINK_FAILURE: 0.02,
            H.ROUTING_FAILURE: 0.03,
            H.PACKET_LOSS: 0.4,
            H.HIGH_LATENCY: 0.6,
            H.MTU_BLACK_HOLE: 0.7,
            H.DNS_FAILURE: 0.7,
            H.NO_FAULT_DETECTED: 2.0,
        },
        "DEGRADED_STALL": {
            H.APPLICATION_SERVICE_FAILURE: 4.0,
            H.TCP_FILTER_OR_PORT_FAILURE: 0.9,
            H.HIGH_LATENCY: 0.5,
            H.PACKET_LOSS: 0.5,
            H.LINK_FAILURE: 0.1,
            H.ROUTING_FAILURE: 0.15,
            H.NO_FAULT_DETECTED: 0.02,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "UNAVAILABLE_REFUSED_NO_LISTENER": {
            # The host answered the health request and reset it: the service is not
            # running. This is the *service* hypothesis's defining signature while
            # the port-filter hypothesis only predicts it when the filter policy is
            # the service-down policy, so the service hypothesis is weighted much
            # more strongly here. (The port hypothesis is separately penalised by the
            # recorded port policy in the TCP_CONNECT row.)
            H.APPLICATION_SERVICE_FAILURE: 8.0,
            H.TCP_FILTER_OR_PORT_FAILURE: 1.2,
            H.LINK_FAILURE: 0.05,
            H.ROUTING_FAILURE: 0.05,
            H.PACKET_LOSS: 0.2,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.5,
        },
        "UNAVAILABLE_REFUSED_POLICY": {
            H.TCP_FILTER_OR_PORT_FAILURE: 5.0,
            H.APPLICATION_SERVICE_FAILURE: 0.7,
            H.LINK_FAILURE: 0.1,
            H.ROUTING_FAILURE: 0.15,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.6,
        },
        "UNAVAILABLE_TIMEOUT": {
            H.TCP_FILTER_OR_PORT_FAILURE: 3.2,
            H.PACKET_LOSS: 1.6,
            H.APPLICATION_SERVICE_FAILURE: 0.9,
            H.LINK_FAILURE: 0.9,
            H.ROUTING_FAILURE: 1.2,
            H.HIGH_LATENCY: 0.1,
            H.NO_FAULT_DETECTED: 0.01,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.9,
        },
        "UNREACHABLE": {
            H.LINK_FAILURE: 2.2,
            H.ROUTING_FAILURE: 2.6,
            H.PACKET_LOSS: 0.9,
            H.APPLICATION_SERVICE_FAILURE: 0.3,
            H.TCP_FILTER_OR_PORT_FAILURE: 0.5,
            H.NO_FAULT_DETECTED: 0.002,
            H.UNKNOWN_OR_MULTIPLE_CAUSES: 0.7,
        },
    },
}


def _expand(row: dict[Hypothesis, float], outcome: str) -> dict[str, float]:
    """Fill a sparse row with :data:`DEFAULT_LIKELIHOOD` for unmentioned hypotheses.

    The outcome's coherence multipliers (see :data:`_COHERENCE`) are applied to
    ``UNKNOWN_OR_MULTIPLE_CAUSES`` and ``NO_FAULT_DETECTED`` *unless* the row sets
    them explicitly, so a row can always override the default reasoning.
    """
    full = {code.value: DEFAULT_LIKELIHOOD for code in Hypothesis}
    unknown_factor, no_fault_factor = _COHERENCE.get(outcome, (1.0, 1.0))
    full[Hypothesis.UNKNOWN_OR_MULTIPLE_CAUSES.value] = DEFAULT_LIKELIHOOD * unknown_factor
    full[Hypothesis.NO_FAULT_DETECTED.value] = DEFAULT_LIKELIHOOD * no_fault_factor
    for code, value in row.items():
        full[code.value] = float(value)
    return full


LIKELIHOODS: dict[str, dict[str, dict[str, float]]] = {
    probe_key: {outcome: _expand(row, outcome) for outcome, row in outcomes.items()}
    for probe_key, outcomes in _ROWS.items()
}


@dataclass(frozen=True)
class LikelihoodTable:
    """Read-only accessor over :data:`LIKELIHOODS`."""

    table: dict[str, dict[str, dict[str, float]]]

    def outcomes(self, probe_key: str) -> list[str]:
        return sorted(self.table.get(probe_key, {}))

    def covers(self, probe_key: str, outcome: str) -> bool:
        return outcome in self.table.get(probe_key, {})

    def likelihood(self, probe_key: str, outcome: str, hypothesis: Hypothesis | str) -> float:
        """P(outcome | hypothesis, probe), raising on an unknown observation.

        Raising (rather than silently returning a default) is deliberate: a probe
        emitting an outcome the model does not know about would otherwise produce a
        confident but meaningless diagnosis.
        """
        code = Hypothesis(hypothesis).value
        row = self.table.get(probe_key)
        if row is None:
            raise ValidationError(
                f"the likelihood model has no entry for probe {probe_key!r}",
                field="probe_key",
            )
        if outcome not in row:
            raise ValidationError(
                f"the likelihood model has no entry for {probe_key}:{outcome}",
                field="outcome",
            )
        return row[outcome][code]

    def outcome_distribution(
        self, probe_key: str, beliefs: dict[Hypothesis, float]
    ) -> dict[str, float]:
        """P(outcome | probe) = sum_h P(outcome | h, probe) * P(h).

        Normalised, so the planner's entropy calculation always works on a valid
        distribution.
        """
        raw: dict[str, float] = {}
        for outcome in self.outcomes(probe_key):
            raw[outcome] = sum(
                self.likelihood(probe_key, outcome, code) * probability
                for code, probability in beliefs.items()
            )
        total = sum(raw.values())
        if total <= 0.0:
            uniform = 1.0 / len(raw) if raw else 0.0
            return {outcome: uniform for outcome in raw}
        return {outcome: value / total for outcome, value in raw.items()}

    def posterior_given(
        self,
        probe_key: str,
        outcome: str,
        beliefs: dict[Hypothesis, float],
        *,
        floor: float,
    ) -> dict[Hypothesis, float]:
        """Hypothetical posterior for one observation, using the same floor as Bayes."""
        weighted = {
            code: probability * max(self.likelihood(probe_key, outcome, code), floor)
            for code, probability in beliefs.items()
        }
        total = sum(weighted.values())
        if total <= 0.0:
            uniform = 1.0 / len(weighted) if weighted else 0.0
            return {code: uniform for code in weighted}
        return {code: value / total for code, value in weighted.items()}


_TABLE = LikelihoodTable(table=LIKELIHOODS)


def likelihood_table() -> LikelihoodTable:
    return _TABLE


def validate_likelihood_table() -> None:
    """Assert the table is well formed and complete.

    Checks that:

    * every probe key the planner can propose has a row,
    * every outcome each probe can emit has a likelihood entry,
    * every stored weight is strictly positive and finite.
    """
    expected_keys = {
        make_probe_key(probe_type, selector)
        for probe_type, selector in CANONICAL_CANDIDATE_ORDER
    }
    missing_keys = sorted(key for key in expected_keys if key not in LIKELIHOODS)
    if missing_keys:
        raise ValidationError(
            "likelihood model is missing probe key(s): " + ", ".join(missing_keys),
            field="probe_key",
        )
    from ..probes.simulated import KNOWN_OUTCOMES  # local import: avoids a cycle

    problems: list[str] = []
    for probe_type, outcomes in KNOWN_OUTCOMES.items():
        selector = next(
            (sel for pt, sel in CANONICAL_CANDIDATE_ORDER if pt is probe_type), None
        )
        key = make_probe_key(probe_type, selector)
        row = LIKELIHOODS.get(key)
        if row is None:
            problems.append(f"{key}: no row")
            continue
        for outcome in outcomes:
            if outcome not in row:
                problems.append(f"{key}:{outcome}: missing")
                continue
            for code, weight in row[outcome].items():
                if not _is_positive_finite(weight):
                    problems.append(f"{key}:{outcome}:{code}: invalid weight {weight!r}")
    if problems:
        raise ValidationError(
            "likelihood model is incomplete: " + "; ".join(sorted(problems)),
            field="likelihoods",
        )


def _is_positive_finite(value: float) -> bool:
    import math

    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0


__all__ = [
    "DEFAULT_LIKELIHOOD",
    "LIKELIHOODS",
    "LikelihoodTable",
    "likelihood_table",
    "validate_likelihood_table",
]
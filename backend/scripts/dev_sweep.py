"""Ad-hoc accuracy sweep over the ten faults (development aid, not a test).

Run:  python3 scripts/dev_sweep.py
This is the tool used while calibrating the likelihood table. It reports top-1
accuracy per fault for both strategies so a model change can be judged quickly.
The authoritative, reproducible evaluation is the experiment runner and the test
suite; this script exists only to make iteration fast and is not part of any
reported result.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.diagnosis.runner import run_diagnosis  # noqa: E402
from app.lab.faults import FaultSpec, FaultType, LabState, normalize_fault  # noqa: E402
from app.lab.templates import get_template  # noqa: E402

CASES: list[tuple[str, FaultType | None, str, dict[str, object]]] = [
    ("NO_FAULT_DETECTED", None, "", {}),
    ("LINK_FAILURE", FaultType.LINK_DOWN, "l-campus-edge", {}),
    ("ROUTING_FAILURE", FaultType.ROUTE_BLACKHOLE, "l-campus-edge",
     {"destination_node_id": "web-1"}),
    ("DNS_FAILURE", FaultType.DNS_FAILURE, "dns-1", {}),
    ("PACKET_LOSS", FaultType.PACKET_LOSS, "l-campus-edge", {"loss_rate": 0.6}),
    ("HIGH_LATENCY", FaultType.HIGH_LATENCY, "l-campus-edge", {"added_latency_ms": 400}),
    ("MTU_BLACK_HOLE", FaultType.MTU_BLACK_HOLE, "l-campus-edge", {"mtu_bytes": 576}),
    ("TCP_FILTER_OR_PORT_FAILURE", FaultType.TCP_PORT_BLOCKED, "web-1:web", {}),
    ("TCP_FILTER_OR_PORT_FAILURE", FaultType.TCP_PORT_REJECTED, "web-1:web", {}),
    ("APPLICATION_SERVICE_FAILURE", FaultType.SERVICE_DOWN, "web-1:web", {}),
    ("LINK_FAILURE", FaultType.GATEWAY_UNREACHABLE, "l-client1-access", {}),
    # multi-hop topology
    ("LINK_FAILURE", FaultType.LINK_DOWN, "l-core-branch", {}),
    ("ROUTING_FAILURE", FaultType.ROUTE_BLACKHOLE, "l-core-branch",
     {"destination_node_id": "app-1"}),
    ("DNS_FAILURE", FaultType.DNS_FAILURE, "dns-1", {}),
    ("MTU_BLACK_HOLE", FaultType.MTU_BLACK_HOLE, "l-edge-core", {"mtu_bytes": 1000}),
]


def main() -> int:
    totals = {"adaptive": 0, "baseline": 0}
    correct = {"adaptive": 0, "baseline": 0}
    inconclusive = {"adaptive": 0, "baseline": 0}
    probes = {"adaptive": 0, "baseline": 0}
    for template_id, dst, svc, cases in (
        ("campus-basic", "web-1", "web", CASES[:11]),
        ("multihop-wan", "app-1", "api", CASES[11:]),
    ):
        print(f"\n=== {template_id} -> {dst} / {svc}")
        for expected, fault_type, target, params in cases:
            topo = get_template(template_id)
            lab = LabState(topo, random_seed=20261009)
            if fault_type is not None:
                lab.add_fault(
                    normalize_fault(
                        FaultSpec(fault_type=fault_type, target_id=target, parameters=params),
                        topo,
                        "f0",
                    )
                )
            line = [f"  {expected:30s}"]
            for strategy in ("adaptive", "baseline"):
                run = run_diagnosis(
                    diagnosis_id=f"dev-{strategy}",
                    session_id="s",
                    lab=lab,
                    topology=topo,
                    source_node_id="client-1",
                    destination_node_id=dst,
                    destination_service=svc,
                    strategy=strategy,
                    max_probes=8,
                )
                lead = run.beliefs()["ranked"][0]
                totals[strategy] += 1
                probes[strategy] += run.probes_used
                if run.status != "confident":
                    inconclusive[strategy] += 1
                if lead["code"] == expected:
                    correct[strategy] += 1
                line.append(
                    f"{strategy[:4]}: {lead['code'][:24]:24s} "
                    f"{lead['probability']:.2f} n={run.probes_used} {run.status[:12]:12s}"
                )
            print(" | ".join(line))
    print()
    for strategy in ("adaptive", "baseline"):
        print(
            f"{strategy:9s} top1={correct[strategy]}/{totals[strategy]} "
            f"({correct[strategy] / totals[strategy]:.0%}) "
            f"mean_probes={probes[strategy] / totals[strategy]:.2f} "
            f"inconclusive={inconclusive[strategy]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

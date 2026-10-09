#!/usr/bin/env python3
"""End-to-end smoke test against a *real* running server (plan.md section 17.5).

This boots uvicorn as a subprocess, waits for the health endpoint, then walks the
documented happy path over plain HTTP:

1.  health check,
2.  create the campus topology,
3.  inject a DNS fault,
4.  run an adaptive diagnosis to conclusion and check the DNS conclusion,
5.  run the fixed-order baseline comparison,
6.  export the Markdown report,
7.  run a small experiment suite and read the metrics,
8.  a second path: route failure,
9.  a third path: TCP/service failure,
10. a control path: healthy lab reports NO_FAULT_DETECTED,
11. reset the lab and confirm the faults are gone.

Exits non-zero on the first failed assertion, printing what failed.

Usage:
    python3 scripts/smoke_test.py                # boots its own server
    python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def request(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, object]:
    """Perform one HTTP request and return (status, parsed body or raw text)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        status = error.code
    # Always try to parse JSON (error envelopes are JSON too) and fall back to the
    # raw text for the Markdown/CSV export endpoints.
    try:
        return status, json.loads(body) if body else None
    except json.JSONDecodeError:
        return status, body


def wait_for_health(base_url: str, attempts: int = 80) -> bool:
    for _ in range(attempts):
        try:
            status, body = request(base_url, "GET", "/api/v1/health")
            if status == 200 and isinstance(body, dict) and body.get("status") == "healthy":
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def start_server(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND),
        env=env,
    )


def diagnose(base_url: str, session_id: str, destination: str, service: str) -> dict:
    status, body = request(
        base_url,
        "POST",
        "/api/v1/diagnoses",
        {
            "session_id": session_id,
            "source_node_id": "client-1",
            "destination_node_id": destination,
            "destination_service": service,
            "run_to_completion": True,
        },
    )
    assert status == 201, f"diagnosis creation failed: {status} {body}"
    return body  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    server: subprocess.Popen | None = None
    if args.base_url:
        base_url = args.base_url.rstrip("/")
        print(f"using an existing server at {base_url}")
    else:
        base_url = f"http://127.0.0.1:{args.port}"
        print(f"starting the backend on {base_url}")
        server = start_server(args.port)

    try:
        check("backend health endpoint reports healthy", wait_for_health(base_url))

        _, health = request(base_url, "GET", "/api/v1/health")
        assert isinstance(health, dict)
        check(
            "health payload identifies the simulation mode",
            "SIMULATED" in str(health.get("simulation_mode", "")),
            str(health.get("simulation_mode")),
        )
        check(
            "live probing is reported as disabled",
            health.get("live_probe_enabled") is False,
        )

        # --- 1. templates and session ------------------------------------
        _, templates = request(base_url, "GET", "/api/v1/lab/templates")
        assert isinstance(templates, dict)
        template_ids = {item["id"] for item in templates["templates"]}
        check(
            "at least two topology templates are offered",
            len(template_ids) >= 2,
            ", ".join(sorted(template_ids)),
        )

        status, session = request(
            base_url, "POST", "/api/v1/lab/sessions", {"template_id": "campus-basic"}
        )
        assert status == 201 and isinstance(session, dict)
        session_id = session["id"]
        check(
            "campus session created with a topology and injectable faults",
            len(session["topology"]["nodes"]) > 0 and len(session["available_faults"]) >= 8,
            f"{len(session['topology']['nodes'])} nodes, "
            f"{len(session['available_faults'])} injectable faults",
        )
        check(
            "every fault class is injectable on this topology",
            len({item["fault_type"] for item in session["available_faults"]}) == 10,
            ", ".join(sorted({item["fault_type"] for item in session["available_faults"]})),
        )

        # --- 2. control run: healthy lab ---------------------------------
        healthy = diagnose(base_url, session_id, "web-1", "web")
        top = healthy["beliefs"]["ranked"][0]["code"]
        check(
            "a healthy lab is diagnosed as NO_FAULT_DETECTED",
            top == "NO_FAULT_DETECTED" and healthy["status"] == "confident",
            f"{top} at {healthy['beliefs']['ranked'][0]['probability']:.2f}, "
            f"{healthy['status']}, {healthy['probes_used']} probes",
        )

        # --- 3. DNS failure ----------------------------------------------
        status, session = request(
            base_url,
            "PUT",
            f"/api/v1/lab/sessions/{session_id}/faults",
            {"fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"}},
        )
        check("DNS fault injected", status == 200 and len(session["active_faults"]) == 1)

        dns = diagnose(base_url, session_id, "web-1", "web")
        dns_top = dns["beliefs"]["ranked"][0]
        check(
            "DNS failure is diagnosed as DNS_FAILURE",
            dns_top["code"] == "DNS_FAILURE",
            f"{dns_top['code']} at {dns_top['probability']:.2f} in {dns['probes_used']} probes",
        )
        check(
            "the DNS diagnosis is confident",
            dns["status"] == "confident",
            dns["stopping_reason"][:100],
        )
        check(
            "the diagnosed component is the resolver, with location confidence",
            dns["suspected_component"]["component_id"] == "dns-1"
            and dns["suspected_component"]["confidence"] in ("moderate", "strong"),
            f"{dns['suspected_component']['component_kind']} "
            f"{dns['suspected_component']['component_id']} "
            f"({dns['suspected_component']['confidence']})",
        )
        check(
            "every observation is labelled SIMULATED LAB",
            all(step["mode"] == "SIMULATED LAB" for step in dns["steps"]),
        )
        check(
            "the planner's reason is recorded for every probe",
            all(step["selected_reason"] for step in dns["steps"]) and len(dns["steps"]) > 0,
        )
        check(
            "evidence statements are attached to the observations",
            all(step["evidence"] for step in dns["steps"]),
        )
        check(
            "the report exposes supporting evidence and alternatives",
            bool(dns["report"]["supporting_evidence"])
            and len(dns["beliefs"]["ranked"]) >= 3,
        )
        probes_adaptive = dns["probes_used"]
        order_adaptive = [step["probe_key"] for step in dns["steps"]]

        # --- 4. baseline comparison --------------------------------------
        status, comparison = request(
            base_url, "POST", f"/api/v1/diagnoses/{dns['id']}/baseline"
        )
        check("baseline comparison executed", status == 200)
        check(
            "baseline used a different probe order from the adaptive run",
            comparison["baseline"]["probe_sequence"] != order_adaptive,
            f"adaptive: {order_adaptive} | baseline: {comparison['baseline']['probe_sequence']}",
        )
        check(
            "both strategies reached the same DNS conclusion",
            comparison["baseline"]["top_hypothesis"] == "DNS_FAILURE",
            f"baseline: {comparison['baseline']['top_hypothesis']} "
            f"in {comparison['baseline']['probes_used']} probes",
        )
        check(
            "the comparison documents that only the order differed",
            "Only the probe selection order differs" in comparison["same_conditions"]["difference"],
        )

        # --- 5. report export -------------------------------------------
        status, markdown = request(
            base_url, "GET", f"/api/v1/diagnoses/{dns['id']}/report?format=markdown"
        )
        assert isinstance(markdown, str)
        check(
            "Markdown report exports with the conclusion and caveats",
            status == 200
            and "NetSleuth diagnostic report" in markdown
            and "DNS_FAILURE" in markdown
            and "SIMULATED LAB" in markdown,
            f"{len(markdown)} bytes",
        )
        status, csv_or_json = request(
            base_url, "GET", f"/api/v1/diagnoses/{dns['id']}/report?format=json"
        )
        check("JSON report exports", status == 200)

        # --- 6. reset ----------------------------------------------------
        status, reset = request(
            base_url, "POST", f"/api/v1/lab/sessions/{session_id}/reset"
        )
        check(
            "reset removes every active fault",
            status == 200 and reset["active_faults"] == [],
        )

        # --- 7. route failure (black hole) -------------------------------
        status, session = request(
            base_url,
            "PUT",
            f"/api/v1/lab/sessions/{session_id}/faults",
            {
                "fault": {
                    "fault_type": "ROUTE_BLACKHOLE",
                    "target_id": "l-campus-edge",
                    "parameters": {"destination_node_id": "web-1"},
                }
            },
        )
        check("route black hole injected", status == 200)
        route = diagnose(base_url, session_id, "web-1", "web")
        route_top = route["beliefs"]["ranked"][0]["code"]
        check(
            "route black hole is diagnosed as ROUTING_FAILURE",
            route_top == "ROUTING_FAILURE",
            f"{route_top} at {route['beliefs']['ranked'][0]['probability']:.2f} "
            f"({route['status']})",
        )
        check(
            "the black hole is localized to the affected link",
            route["suspected_component"]["component_id"] == "l-campus-edge",
            f"{route['suspected_component']['component_kind']} "
            f"{route['suspected_component']['component_id']}",
        )
        request(base_url, "POST", f"/api/v1/lab/sessions/{session_id}/reset")

        # --- 8. TCP / service failure ------------------------------------
        status, session = request(
            base_url,
            "PUT",
            f"/api/v1/lab/sessions/{session_id}/faults",
            {"fault": {"fault_type": "SERVICE_DOWN", "target_id": "web-1:web"}},
        )
        check("service-down fault injected", status == 200)
        service = diagnose(base_url, session_id, "web-1", "web")
        service_top = service["beliefs"]["ranked"][0]["code"]
        check(
            "a dead service is diagnosed as APPLICATION_SERVICE_FAILURE",
            service_top == "APPLICATION_SERVICE_FAILURE",
            f"{service_top} at {service['beliefs']['ranked'][0]['probability']:.2f} "
            f"({service['status']})",
        )
        check(
            "the service fault is localized to the host and port",
            service["suspected_component"]["component_id"] == "web-1:80",
            str(service["suspected_component"]["component_id"]),
        )
        request(base_url, "POST", f"/api/v1/lab/sessions/{session_id}/reset")

        # --- 9. experiment suite -----------------------------------------
        status, estimate = request(
            base_url,
            "POST",
            "/api/v1/experiments/estimate",
            {"runs_per_scenario": 1, "template_ids": ["campus-basic"]},
        )
        check(
            "the experiment estimate reports the run count",
            status == 200 and estimate["total_runs"] > 0,
            f"{estimate['total_runs']} runs",
        )

        status, summary = request(
            base_url,
            "POST",
            "/api/v1/experiments/run",
            {
                "runs_per_scenario": 1,
                "seed": 20261009,
                "template_ids": ["campus-basic"],
                "strategies": ["adaptive", "baseline"],
            },
        )
        assert status == 201 and isinstance(summary, dict)
        metrics = summary["metrics"]
        check(
            "the experiment executed every planned run",
            summary["completed_runs"] == summary["plan"]["total_runs"],
            f"{summary['completed_runs']} runs in {summary['wall_clock_ms']:.0f} ms",
        )
        adaptive = metrics["by_strategy"]["adaptive"]
        baseline = metrics["by_strategy"]["baseline"]
        check(
            "adaptive top-1 accuracy is at least the baseline's",
            adaptive["top1_correct"] >= baseline["top1_correct"],
            f"adaptive {adaptive['top1_accuracy']:.0%} "
            f"({adaptive['top1_correct']}/{adaptive['runs']}) vs "
            f"baseline {baseline['top1_accuracy']:.0%} "
            f"({baseline['top1_correct']}/{baseline['runs']})",
        )
        check(
            "adaptive used no more probes on average",
            adaptive["mean_probes_all"] <= baseline["mean_probes_all"],
            f"adaptive {adaptive['mean_probes_all']:.2f} vs "
            f"baseline {baseline['mean_probes_all']:.2f}",
        )
        check(
            "the confusion matrix covers every run",
            sum(sum(row.values()) for row in metrics["confusion_matrix"]["matrix"].values())
            == metrics["run_count"],
        )
        check(
            "per-class metrics are reported for every injected class",
            len(metrics["by_fault_class"]) >= 9,
            ", ".join(sorted(metrics["by_fault_class"])),
        )

        status, csv_text = request(
            base_url,
            "GET",
            f"/api/v1/experiments/{summary['experiment_id']}/export?format=csv",
        )
        assert isinstance(csv_text, str)
        check(
            "CSV export contains one row per run",
            status == 200 and len(csv_text.strip().split("\n")) == summary["completed_runs"] + 1,
            f"{len(csv_text.strip().splitlines())} lines",
        )

        status, markdown_summary = request(
            base_url,
            "GET",
            f"/api/v1/experiments/{summary['experiment_id']}/export?format=markdown",
        )
        assert isinstance(markdown_summary, str)
        check(
            "Markdown experiment report contains the metric tables",
            status == 200
            and "Top-1" in markdown_summary
            and "Confusion matrix" in markdown_summary,
        )

        # --- 10. error handling ------------------------------------------
        status, error_body = request(base_url, "GET", "/api/v1/lab/sessions/ghost")
        check(
            "an unknown session returns a structured 404",
            status == 404
            and isinstance(error_body, dict)
            and error_body["error"]["code"] == "not_found",
        )
        status, _ = request(
            base_url, "POST", "/api/v1/lab/sessions", {"template_id": "not-a-template"}
        )
        check("an unknown template is rejected", status == 404)

        print()
        passed = sum(1 for _, ok, _ in CHECKS if ok)
        print(f"smoke test complete: {passed}/{len(CHECKS)} checks passed")
        print("All simulated evidence was labelled SIMULATED LAB; no live traffic was generated.")
        return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())

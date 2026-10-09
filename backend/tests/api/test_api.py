"""API tests: every documented endpoint, its error contract and its limits.

These tests run the real FastAPI app against an in-memory database, so they cover
validation, error responses, persistence and the experiment endpoints end to end.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import API_PREFIX, APP_VERSION, MODEL_VERSION


class TestHealthAndCatalogue:
    def test_health_reports_healthy(self, client) -> None:
        response = client.get(f"{API_PREFIX}/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["version"] == APP_VERSION
        assert body["model_version"] == MODEL_VERSION
        assert "SIMULATED" in body["simulation_mode"]

    def test_live_probe_mode_is_reported_as_not_implemented(self, client) -> None:
        body = client.get(f"{API_PREFIX}/health").json()
        assert body["live_probe_enabled"] is False

    def test_catalogue_lists_hypotheses_probes_and_faults(self, client) -> None:
        body = client.get(f"{API_PREFIX}/catalogue").json()
        assert len(body["hypotheses"]) >= 9
        assert len(body["probes"]) >= 6
        assert len(body["fault_types"]) == 10
        assert body["model_version"]
        for probe in body["probes"]:
            assert probe["cost"] > 0

    def test_overview_is_empty_on_a_fresh_install(self, client) -> None:
        body = client.get(f"{API_PREFIX}/overview").json()
        assert body["stats"]["sessions"] == 0
        assert body["stats"]["diagnoses"] == 0
        assert body["recent_sessions"] == []
        assert body["recent_diagnoses"] == []
        assert body["latest_experiment"] is None
        assert body["mode"] == "SIMULATED LAB"

    def test_openapi_schema_is_served(self, client) -> None:
        assert client.get("/openapi.json").status_code == 200


class TestLabEndpoints:
    def test_templates_are_listed(self, client) -> None:
        body = client.get(f"{API_PREFIX}/lab/templates").json()
        assert len(body["templates"]) >= 2
        ids = {item["id"] for item in body["templates"]}
        assert {"campus-basic", "multihop-wan"} <= ids

    def test_session_creation_returns_a_full_topology(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["mode"] == "simulated"
        assert body["topology"]["nodes"]
        assert body["topology"]["links"]
        assert body["available_faults"]
        assert body["parameter_reference"]
        assert body["random_seed"] > 0

    def test_unknown_template_is_rejected(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "ghost"}
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_blank_template_id_is_rejected(self, client) -> None:
        response = client.post(f"{API_PREFIX}/lab/sessions", json={"template_id": "   "})
        assert response.status_code == 422
        assert "error" in response.json()

    def test_missing_template_id_is_rejected(self, client) -> None:
        response = client.post(f"{API_PREFIX}/lab/sessions", json={})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_validation_error"

    def test_session_can_be_retrieved_and_listed(self, client) -> None:
        created = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        fetched = client.get(f"{API_PREFIX}/lab/sessions/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == created["id"]
        listing = client.get(f"{API_PREFIX}/lab/sessions").json()
        assert listing["total"] == 1
        assert listing["sessions"][0]["node_count"] > 0

    def test_unknown_session_returns_structured_404(self, client) -> None:
        response = client.get(f"{API_PREFIX}/lab/sessions/ghost")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["field"] == "session_id"

    def test_inject_valid_fault(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"}},
        )
        assert response.status_code == 200
        faults = response.json()["active_faults"]
        assert len(faults) == 1
        assert faults[0]["fault_type"] == "DNS_FAILURE"
        assert faults[0]["target_kind"] == "node"
        assert faults[0]["description"]

    def test_inject_fault_with_unknown_target_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "LINK_DOWN", "target_id": "nope"}},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_inject_fault_with_invalid_parameters_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={
                "fault": {
                    "fault_type": "PACKET_LOSS",
                    "target_id": "l-campus-edge",
                    "parameters": {"loss_rate": 5.0},
                }
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "parameters.loss_rate"

    def test_inject_unknown_fault_type_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "MADE_UP", "target_id": "x"}},
        )
        assert response.status_code == 422

    def test_all_advertised_faults_can_actually_be_injected(self, client) -> None:
        """Every fault the API offers must be accepted.

        The UI builds its fault selector from ``available_faults``, so a listing that
        the injector rejects would be a broken button.
        """
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        available = session["available_faults"]
        assert len(available) >= 8
        for index, item in enumerate(available):
            response = client.put(
                f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
                json={
                    "fault": {
                        "fault_type": item["fault_type"],
                        "target_id": item["target_id"],
                        "parameters": item["parameters"],
                    },
                    "fault_id": f"f{index}",
                },
            )
            assert response.status_code == 200, (
                f"{item['fault_type']} on {item['target_id']} was offered but rejected: "
                f"{response.text}"
            )
        # Every fault type in the catalogue is represented.
        offered = {item["fault_type"] for item in available}
        catalogue = {item["fault_type"] for item in client.get(f"{API_PREFIX}/catalogue").json()["fault_types"]}
        assert offered == catalogue

    def test_fault_can_be_toggled_and_removed(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        applied = client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "LINK_DOWN", "target_id": "l-campus-edge"}},
        ).json()
        fault_id = applied["active_faults"][0]["id"]
        toggled = client.patch(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults/{fault_id}",
            json={"is_active": False},
        )
        assert toggled.status_code == 200
        assert toggled.json()["active_faults"][0]["is_active"] is False
        removed = client.delete(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults/{fault_id}"
        )
        assert removed.status_code == 200
        assert removed.json()["active_faults"] == []

    def test_toggling_an_unknown_fault_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.patch(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults/ghost",
            json={"is_active": True},
        )
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "fault_id"

    def test_replacing_a_fault_id_with_a_different_type_conflicts(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={
                "fault": {"fault_type": "LINK_DOWN", "target_id": "l-campus-edge"},
                "fault_id": "same-id",
            },
        )
        response = client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={
                "fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"},
                "fault_id": "same-id",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_reset_clears_all_faults(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        for fault_type, target in (
            ("LINK_DOWN", "l-campus-edge"),
            ("DNS_FAILURE", "dns-1"),
        ):
            client.put(
                f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
                json={"fault": {"fault_type": fault_type, "target_id": target}},
            )
        reset = client.post(f"{API_PREFIX}/lab/sessions/{session['id']}/reset")
        assert reset.status_code == 200
        assert reset.json()["active_faults"] == []

    def test_reset_with_new_seed_is_honoured(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        reset = client.post(
            f"{API_PREFIX}/lab/sessions/{session['id']}/reset", params={"random_seed": 5}
        )
        assert reset.json()["random_seed"] == 5

    def test_session_deletion_removes_it(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        assert client.delete(f"{API_PREFIX}/lab/sessions/{session['id']}").status_code == 204
        assert client.get(f"{API_PREFIX}/lab/sessions/{session['id']}").status_code == 404

    def test_deleting_an_unknown_session_is_a_404(self, client) -> None:
        assert client.delete(f"{API_PREFIX}/lab/sessions/ghost").status_code == 404

    def test_forwarding_tables_are_exposed_for_routers(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        assert session["forwarding_tables"]
        assert "access-rtr" in session["forwarding_tables"]
        rows = session["forwarding_tables"]["access-rtr"]
        assert rows and rows[0]["destination"]


class TestDiagnosisEndpoints:
    def _session_with_dns_fault(self, client) -> str:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        client.put(
            f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
            json={"fault": {"fault_type": "DNS_FAILURE", "target_id": "dns-1"}},
        )
        return session["id"]

    def test_create_diagnosis_returns_the_planner_decision(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "running"
        assert body["mode"] == "SIMULATED LAB"
        assert body["probes_used"] == 0
        assert body["next_probe"] is not None
        assert body["next_probe"]["reason"]
        assert body["beliefs"]["ranked"]
        assert body["considered_alternatives"]

    def test_step_executes_one_probe_and_returns_evidence(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        ).json()
        response = client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/step")
        assert response.status_code == 200
        body = response.json()
        assert body["probes_used"] == 1
        step = body["steps"][0]
        assert step["outcome"]
        assert step["evidence"]
        assert step["selected_reason"]
        assert step["belief_after"]
        assert step["mode"] == "SIMULATED LAB"

    def test_run_to_completion_returns_a_terminal_state(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        ).json()
        response = client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/run")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("confident", "inconclusive", "budget_exhausted")
        assert body["stopping_reason"]
        assert body["report"]
        assert body["completed_at"]

    def test_run_to_completion_flag_on_creation(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        body = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        ).json()
        assert body["status"] != "running"
        assert body["probes_used"] > 0

    def test_stepping_a_finished_diagnosis_conflicts(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        ).json()
        response = client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/step")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_unknown_diagnosis_is_a_404(self, client) -> None:
        response = client.get(f"{API_PREFIX}/diagnoses/ghost")
        assert response.status_code == 404
        assert response.json()["error"]["field"] == "diagnosis_id"

    def test_invalid_source_and_destination_are_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "ghost",
                "destination_node_id": "web-1",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "source_node_id"

    def test_identical_source_and_destination_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "web-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        )
        assert response.status_code == 422

    def test_unknown_service_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "ghost",
            },
        )
        assert response.status_code == 422

    def test_probe_budget_ceiling_is_enforced(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "max_probes": 9999,
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "max_probes"

    def test_zero_probe_budget_is_rejected(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "max_probes": 0,
            },
        )
        assert response.status_code == 422

    def test_baseline_comparison_runs_both_strategies(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        ).json()
        response = client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/baseline")
        assert response.status_code == 200
        body = response.json()
        assert body["adaptive"]["strategy"] == "adaptive"
        assert body["baseline"]["strategy"] == "baseline"
        assert body["baseline"]["probe_sequence"]
        assert body["same_conditions"]["difference"]
        assert body["baseline"]["probes_used"] > 0

    def test_baseline_comparison_flag_can_be_overridden(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        ).json()
        body = client.post(
            f"{API_PREFIX}/diagnoses/{diagnosis['id']}/baseline",
            json={"max_probes": 3},
        ).json()
        assert body["baseline"]["max_probes"] == 3
        assert body["baseline"]["probes_used"] <= 3

    def test_baseline_can_be_run_before_the_adaptive_run(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        ).json()
        assert diagnosis["probes_used"] == 0
        response = client.post(f"{API_PREFIX}/diagnoses/{diagnosis['id']}/baseline")
        assert response.status_code == 200
        assert response.json()["baseline"]["probes_used"] > 0

    def test_report_exports_markdown_and_json(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        ).json()
        markdown = client.get(
            f"{API_PREFIX}/diagnoses/{diagnosis['id']}/report", params={"format": "markdown"}
        )
        assert markdown.status_code == 200
        assert "NetSleuth diagnostic report" in markdown.text
        assert "attachment" in markdown.headers["content-disposition"]
        payload = client.get(
            f"{API_PREFIX}/diagnoses/{diagnosis['id']}/report", params={"format": "json"}
        )
        assert payload.status_code == 200
        assert json.loads(payload.text)["id"] == diagnosis["id"]

    def test_invalid_report_format_is_rejected(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        diagnosis = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
            },
        ).json()
        assert client.get(
            f"{API_PREFIX}/diagnoses/{diagnosis['id']}/report", params={"format": "pdf"}
        ).status_code == 422

    def test_diagnoses_are_listed_per_session(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        )
        listing = client.get(
            f"{API_PREFIX}/diagnoses", params={"session_id": session_id}
        ).json()
        assert listing["total"] == 1
        assert listing["diagnoses"][0]["top_hypothesis"] == "DNS_FAILURE"

    def test_overview_reflects_real_activity(self, client) -> None:
        session_id = self._session_with_dns_fault(client)
        client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session_id,
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        )
        body = client.get(f"{API_PREFIX}/overview").json()
        assert body["stats"]["sessions"] == 1
        assert body["stats"]["diagnoses"] == 1
        assert body["stats"]["observations"] > 0
        assert body["recent_diagnoses"]

    def test_hypothesis_catalogue_endpoint(self, client) -> None:
        body = client.get(f"{API_PREFIX}/diagnosis/hypotheses").json()
        assert len(body["hypotheses"]) >= 9
        codes = {item["code"] for item in body["hypotheses"]}
        assert "UNKNOWN_OR_MULTIPLE_CAUSES" in codes

    def test_multi_fault_lab_is_still_diagnosable(self, client) -> None:
        session = client.post(
            f"{API_PREFIX}/lab/sessions", json={"template_id": "campus-basic"}
        ).json()
        for fault_type, target in (
            ("LINK_DOWN", "l-campus-edge"),
            ("DNS_FAILURE", "dns-1"),
        ):
            client.put(
                f"{API_PREFIX}/lab/sessions/{session['id']}/faults",
                json={"fault": {"fault_type": fault_type, "target_id": target}},
            )
        response = client.post(
            f"{API_PREFIX}/diagnoses",
            json={
                "session_id": session["id"],
                "source_node_id": "client-1",
                "destination_node_id": "web-1",
                "destination_service": "web",
                "run_to_completion": True,
            },
        )
        assert response.status_code == 201
        assert response.json()["probes_used"] > 0


class TestExperimentEndpoints:
    def test_scenario_catalogue_matches_the_injectable_faults(self, client) -> None:
        body = client.get(f"{API_PREFIX}/experiments/scenarios").json()
        assert len(body["scenarios"]) >= 20
        assert len(body["fault_types"]) == 10
        assert body["strategies"] == ["adaptive", "baseline"]
        assert body["baseline_sequence"]
        assert body["defaults"]["max_total_runs"] > 0
        for scenario in body["scenarios"]:
            assert scenario["expected_hypothesis"]

    def test_estimate_counts_runs_without_executing_them(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/experiments/estimate",
            json={"runs_per_scenario": 2, "template_ids": ["campus-basic"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total_runs"] > 0
        assert body["exceeds_limit"] is False
        assert client.get(f"{API_PREFIX}/overview").json()["stats"]["experiments"] == 0

    def test_oversized_experiment_is_refused_before_it_runs(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 200, "strategies": ["adaptive", "baseline"]},
        )
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["field"] == "runs_per_scenario"
        assert "exceeds the configured limit" in body["detail"]

    def test_empty_strategy_list_is_rejected(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "strategies": []},
        )
        assert response.status_code == 422

    def test_zero_runs_is_rejected(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/experiments/run", json={"runs_per_scenario": 0}
        )
        assert response.status_code == 422

    def test_filter_matching_nothing_is_an_error(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "template_ids": ["does-not-exist"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "fault_types"

    def test_small_experiment_runs_and_reports_metrics(self, client) -> None:
        response = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "template_ids": ["campus-basic"]},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["completed_runs"] > 0
        assert body["wall_clock_ms"] >= 0
        metrics = body["metrics"]
        for strategy in ("adaptive", "baseline"):
            block = metrics["by_strategy"][strategy]
            assert block["runs"] > 0
            assert 0.0 <= block["top1_accuracy"] <= 1.0
            assert block["mean_probes_all"] > 0
        assert metrics["confusion_matrix"]["labels"]
        assert metrics["per_scenario"]
        assert metrics["notes"]

    def test_adaptive_beats_or_matches_the_baseline_on_a_small_suite(self, client) -> None:
        body = client.post(
            f"{API_PREFIX}/experiments/run",
            json={
                "runs_per_scenario": 1,
                "seed": 4321,
                "template_ids": ["campus-basic", "multihop-wan"],
            },
        ).json()
        adaptive = body["metrics"]["by_strategy"]["adaptive"]
        baseline = body["metrics"]["by_strategy"]["baseline"]
        assert adaptive["top1_correct"] >= baseline["top1_correct"], (
            "the adaptive planner under-performed the fixed order on the default suite: "
            f"adaptive {adaptive['top1_accuracy']} vs baseline {baseline['top1_accuracy']}"
        )
        assert adaptive["mean_probes_all"] <= baseline["mean_probes_all"]

    def test_experiment_can_be_retrieved_with_stored_runs(self, client) -> None:
        created = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "template_ids": ["campus-basic"]},
        ).json()
        response = client.get(f"{API_PREFIX}/experiments/{created['experiment_id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["completed_runs"] == created["completed_runs"]
        assert body["stored_run_records"] == created["completed_runs"]
        assert body["variation"]
        assert body["config"]["seed"]

    def test_unknown_experiment_is_a_404(self, client) -> None:
        assert client.get(f"{API_PREFIX}/experiments/ghost").status_code == 404

    def test_experiments_are_listed(self, client) -> None:
        client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "template_ids": ["campus-basic"]},
        )
        listing = client.get(f"{API_PREFIX}/experiments").json()
        assert listing["total"] == 1
        assert listing["experiments"][0]["config"]

    def test_export_as_json_csv_and_markdown(self, client) -> None:
        created = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "template_ids": ["campus-basic"]},
        ).json()
        experiment_id = created["experiment_id"]
        payload = client.get(
            f"{API_PREFIX}/experiments/{experiment_id}/export", params={"format": "json"}
        )
        assert payload.status_code == 200
        assert json.loads(payload.text)["experiment_id"] == experiment_id
        csv_response = client.get(
            f"{API_PREFIX}/experiments/{experiment_id}/export", params={"format": "csv"}
        )
        assert csv_response.status_code == 200
        lines = csv_response.text.strip().split("\n")
        assert len(lines) == created["completed_runs"] + 1
        assert "actual_fault_type" in lines[0]
        markdown = client.get(
            f"{API_PREFIX}/experiments/{experiment_id}/export", params={"format": "markdown"}
        )
        assert markdown.status_code == 200
        assert "Top-1" in markdown.text
        assert "Confusion matrix" in markdown.text

    def test_metrics_can_be_recomputed_from_the_stored_records(self, client) -> None:
        """The API must not be the only place the numbers exist."""
        from app.experiments.metrics import compute_metrics

        created = client.post(
            f"{API_PREFIX}/experiments/run",
            json={"runs_per_scenario": 1, "template_ids": ["campus-basic"]},
        ).json()
        database = client.app.state.database
        runs = database.list_experiment_runs(created["experiment_id"])
        recomputed = compute_metrics(runs, config=created["config"])
        assert recomputed["run_count"] == created["metrics"]["run_count"]
        for strategy, block in created["metrics"]["by_strategy"].items():
            assert recomputed["by_strategy"][strategy]["top1_correct"] == block["top1_correct"]

    def test_filtering_by_fault_type_works(self, client) -> None:
        body = client.post(
            f"{API_PREFIX}/experiments/run",
            json={
                "runs_per_scenario": 1,
                "fault_types": ["DNS_FAILURE"],
                "include_control": False,
            },
        ).json()
        assert body["metrics"]["by_fault_class"]
        assert set(body["metrics"]["by_fault_class"]) == {"DNS_FAILURE"}

    def test_single_strategy_run_is_allowed(self, client) -> None:
        body = client.post(
            f"{API_PREFIX}/experiments/run",
            json={
                "runs_per_scenario": 1,
                "template_ids": ["campus-basic"],
                "strategies": ["adaptive"],
            },
        ).json()
        assert set(body["metrics"]["by_strategy"]) == {"adaptive"}


class TestErrorContract:
    def test_unexpected_errors_do_not_leak_internals(self) -> None:
        """A 500 must not expose a stack trace or an internal file path.

        A dedicated client with ``raise_server_exceptions=False`` is used so the
        installed catch-all handler is exercised the way a browser would see it.
        """
        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(":memory:")

        @app.get(f"{API_PREFIX}/boom", include_in_schema=False)
        def _boom() -> None:
            raise RuntimeError("/home/secret/internal/path.py line 42 is broken")

        with TestClient(app, raise_server_exceptions=False) as tolerant:
            response = tolerant.get(f"{API_PREFIX}/boom")
        app.state.database.close()

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert body["error"]["context"] == {"exception_type": "RuntimeError"}
        assert "/home/secret" not in response.text
        assert "Traceback" not in response.text
        assert "path.py" not in response.text

    def test_error_responses_share_one_shape(self, client) -> None:
        responses = [
            client.get(f"{API_PREFIX}/lab/sessions/ghost"),
            client.post(f"{API_PREFIX}/lab/sessions", json={"template_id": "ghost"}),
            client.post(f"{API_PREFIX}/lab/sessions", json={}),
        ]
        for response in responses:
            body = response.json()
            assert "error" in body
            assert "code" in body["error"]
            assert "detail" in body["error"]
            assert isinstance(body["error"]["detail"], str)

    def test_net_sleuth_error_shape(self) -> None:
        from app.core.errors import ConflictError

        error = ConflictError("already finished", field="diagnosis_id")
        payload = error.payload()
        assert payload["error"]["code"] == "conflict"
        assert payload["error"]["field"] == "diagnosis_id"
        assert error.status_code == 409

    def test_unknown_route_returns_a_structured_error(self, client) -> None:
        response = client.get(f"{API_PREFIX}/not-a-route")
        assert response.status_code == 404
        assert "error" in response.json()


class TestCors:
    def test_frontend_origin_is_allowed(self, client) -> None:
        response = client.options(
            f"{API_PREFIX}/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code in (200, 204)
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_an_arbitrary_origin_is_not_allowed(self, client) -> None:
        response = client.options(
            f"{API_PREFIX}/health",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "http://evil.example"


class TestCorsPatchMethod:
    """Bug: `allow_methods` omitted PATCH, which the fault-toggle endpoint uses.

    The Vite proxy makes the app same-origin in development, so the omission was
    invisible there and would have surfaced first in a cross-origin deployment.
    """

    def test_patch_preflight_is_allowed(self, client) -> None:
        response = client.options(
            f"{API_PREFIX}/lab/sessions/anything/faults/anything",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code in (200, 204)
        allowed = response.headers.get("access-control-allow-methods", "")
        assert "PATCH" in allowed, f"PATCH is not allowed cross-origin: {allowed!r}"

    def test_every_method_the_client_uses_is_allowed(self, client) -> None:
        """The advertised allow-list must cover the routes the UI actually calls."""
        from app.api import routes_diagnosis, routes_experiments, routes_health, routes_lab

        used: set[str] = set()
        for module in (routes_health, routes_lab, routes_diagnosis, routes_experiments):
            for route in module.router.routes:
                used |= set(getattr(route, "methods", set()) or set())
        used.discard("HEAD")
        assert used <= {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}, used

        response = client.options(
            f"{API_PREFIX}/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        allowed = set(
            part.strip()
            for part in response.headers.get("access-control-allow-methods", "").split(",")
        )
        assert used <= allowed, f"routes use methods that CORS does not allow: {used - allowed}"

"""Lab-session and diagnosis services: the bridge between HTTP and the engine.

``SessionService`` owns the mapping from a persisted session row to a live
:class:`~app.lab.faults.LabState`. Rebuilding state from the stored fault *list*
(rather than caching mutated topology objects) is what guarantees that a restart
reproduces exactly the same lab, and it means "reset" is simply "drop the fault
list".

``DiagnosisService`` keeps running diagnoses in memory (they carry a simulator and
a seeded RNG) while persisting every step, so a diagnosis is inspectable after the
fact without keeping the whole object graph alive.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..core.config import DEFAULT_RANDOM_SEED
from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..diagnosis.runner import DiagnosisRun, fault_signature, run_diagnosis
from ..lab.faults import (
    FAULT_DESCRIPTIONS,
    PARAMETER_REFERENCE,
    FaultConfig,
    FaultSpec,
    FaultType,
    LabState,
    TARGET_KIND,
    normalize_fault,
    validate_fault,
)
from ..lab.graph import Topology, topology_to_dict
from ..lab.routing import RoutingTable
from ..lab.templates import get_template, list_template_ids, template_summaries
from ..storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class SessionService:
    """Create, load, mutate and reset lab sessions."""

    def __init__(self, database: Database) -> None:
        self.db = database
        #: Live lab state per session id. Rebuilt from storage on demand.
        self._labs: dict[str, LabState] = {}

    # ---- construction ----------------------------------------------------
    def create_session(
        self,
        template_id: str,
        *,
        name: str | None = None,
        random_seed: int | None = None,
    ) -> dict[str, Any]:
        topology = get_template(template_id)
        seed = DEFAULT_RANDOM_SEED if random_seed is None else random_seed
        session_id = _new_id("sess")
        record = {
            "id": session_id,
            "name": name or f"{topology.name} session",
            "template_id": topology.id,
            "template_name": topology.name,
            "mode": "simulated",
            "topology": topology_to_dict(topology),
            "active_faults": [],
            "random_seed": seed,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.db.save_session(record)
        self._labs[session_id] = LabState(topology, random_seed=seed)
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        record = self.db.get_session(session_id)
        return self._serialize(record, self._lab_for(record))

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for record in self.db.list_sessions(limit=limit):
            lab = self._lab_for(record)
            out.append(
                {
                    "id": record["id"],
                    "name": record["name"],
                    "template_id": record["template_id"],
                    "template_name": record["template_name"],
                    "mode": record["mode"],
                    "active_fault_count": len(lab.active_faults),
                    "active_fault_types": sorted(
                        fault.fault_type.value for fault in lab.active_faults
                    ),
                    "node_count": len(record["topology"]["nodes"]),
                    "link_count": len(record["topology"]["links"]),
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                }
            )
        return out

    # ---- fault mutation --------------------------------------------------
    def apply_fault(self, session_id: str, spec: FaultSpec, fault_id: str | None = None) -> dict[str, Any]:
        record = self.db.get_session(session_id)
        lab = self._lab_for(record)
        validate_fault(spec, lab.topology)
        existing_ids = {fault.id for fault in lab.faults}
        if fault_id and fault_id in existing_ids:
            # Replacing an existing fault id is allowed, but only with the same
            # fault type — otherwise "replace" would silently change what the
            # stored id means for anything referencing it.
            current = next(f for f in lab.faults if f.id == fault_id)
            if current.fault_type is not spec.fault_type:
                raise ConflictError(
                    f"fault id {fault_id} already exists with type "
                    f"{current.fault_type.value}; remove it before adding a "
                    f"{spec.fault_type.value} fault",
                    field="fault_id",
                )
            normalized = normalize_fault(spec, lab.topology, fault_id)
        else:
            normalized = normalize_fault(
                spec,
                lab.topology,
                fault_id or _new_id(f"fault-{spec.fault_type.value.lower()}"),
            )
        lab.add_fault(normalized)
        self._persist_faults(record, lab)
        return self.get_session(session_id)

    def set_fault_active(self, session_id: str, fault_id: str, active: bool) -> dict[str, Any]:
        record = self.db.get_session(session_id)
        lab = self._lab_for(record)
        lab.set_fault_active(fault_id, active)
        self._persist_faults(record, lab)
        return self.get_session(session_id)

    def remove_fault(self, session_id: str, fault_id: str) -> dict[str, Any]:
        record = self.db.get_session(session_id)
        lab = self._lab_for(record)
        if not lab.remove_fault(fault_id):
            raise NotFoundError(f"unknown fault id: {fault_id}", field="fault_id")
        self._persist_faults(record, lab)
        return self.get_session(session_id)

    def reset_session(self, session_id: str, *, random_seed: int | None = None) -> dict[str, Any]:
        record = self.db.get_session(session_id)
        # Validate before mutating: a rejected request must not leave the cached lab
        # with its faults already cleared while storage still lists them.
        if random_seed is not None:
            if not isinstance(random_seed, int) or isinstance(random_seed, bool):
                raise ValidationError(
                    "random_seed must be an integer", field="random_seed"
                )
            if random_seed < 0:
                raise ValidationError(
                    "random_seed must be a non-negative integer", field="random_seed"
                )
        lab = self._lab_for(record)
        lab.clear_faults()
        if random_seed is not None:
            lab.random_seed = random_seed
            # The seed lives on the session record, not only on the in-memory lab:
            # a session must reproduce the same packet-loss stream after a restart.
            record["random_seed"] = int(random_seed)
        record["updated_at"] = _now()
        self._persist_faults(record, lab)
        return self.get_session(session_id)

    def list_faults(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        return {
            "session_id": session_id,
            "active_faults": session["active_faults"],
            "available_faults": session["available_faults"],
        }

    # ---- internals -------------------------------------------------------
    def lab_for(self, session_id: str) -> LabState:
        """Live lab state for a session (rebuilt from storage when needed)."""
        return self._lab_for(self.db.get_session(session_id))

    def topology_for(self, session_id: str) -> Topology:
        return self.lab_for(session_id).topology

    def _lab_for(self, record: dict[str, Any]) -> LabState:
        cached = self._labs.get(record["id"])
        if cached is not None and cached.random_seed == record["random_seed"]:
            # Re-apply the persisted fault list so an out-of-band change (or a
            # second process) cannot leave the cache stale.
            desired = json_faults(record["active_faults"])
            if _fault_signature(cached.faults) != _fault_signature(desired):
                cached.faults = desired
                cached.refresh()
            return cached
        topology = Topology.model_validate(record["topology"])
        lab = LabState(
            topology,
            random_seed=record["random_seed"],
            faults=json_faults(record["active_faults"]),
        )
        self._labs[record["id"]] = lab
        return lab

    def _persist_faults(self, record: dict[str, Any], lab: LabState) -> None:
        public = [fault.to_public() for fault in lab.faults]
        record["active_faults"] = public
        record["updated_at"] = _now()
        self.db.save_session(record)

    def _serialize(self, record: dict[str, Any], lab: LabState) -> dict[str, Any]:
        topology = lab.topology
        routing = RoutingTable(topology)
        forwarding_tables = {
            node.id: routing.forwarding_table(node.id)
            for node in topology.nodes
            if node.services or node.type.value == "router"
        }
        return {
            "id": record["id"],
            "name": record["name"],
            "template_id": record["template_id"],
            "template_name": record["template_name"],
            "mode": record["mode"],
            "random_seed": lab.random_seed,
            "topology": topology_to_dict(topology),
            "active_faults": [fault.to_public() for fault in lab.faults],
            "available_faults": available_faults(topology),
            "parameter_reference": PARAMETER_REFERENCE,
            "forwarding_tables": forwarding_tables,
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }


def json_faults(payload: list[dict[str, Any]]) -> list[FaultConfig]:
    return [FaultConfig.model_validate(item) for item in payload]


def _fault_signature(faults: list[FaultConfig]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        sorted(
            (
                fault.id,
                fault.fault_type.value,
                fault.target_id,
                fault.is_active,
                tuple(sorted((str(k), str(v)) for k, v in (fault.parameters or {}).items())),
            )
            for fault in faults
        )
    )


def available_faults(topology: Topology) -> list[dict[str, Any]]:
    """Concrete, validated fault definitions that can be injected right now.

    Only *valid* targets are offered, so the UI can present a real choice instead
    of a free-text field that mostly produces 422s. The parameters the injector
    would default to are included so the UI can preview them before applying.
    """
    out: list[dict[str, Any]] = []
    links = sorted(topology.links, key=lambda item: item.id)
    app_servers = sorted(topology.app_servers(), key=lambda item: item.id)
    resolvers = sorted(topology.resolvers(), key=lambda item: item.id)

    def add(fault_type: FaultType, target_id: str, params: dict[str, Any], label: str) -> None:
        spec = FaultSpec(fault_type=fault_type, target_id=target_id, parameters=params)
        try:
            validate_fault(spec, topology)
        except ValidationError:
            return
        out.append(
            {
                "fault_type": fault_type.value,
                "target_id": target_id,
                "target_kind": TARGET_KIND[fault_type].value,
                "target_label": label,
                "parameters": params,
                "description": FAULT_DESCRIPTIONS[fault_type],
            }
        )

    for link in links:
        label = f"{topology.node(link.node_a).name} <-> {topology.node(link.node_b).name}"
        link_label = f"{label} ({link.id})"
        add(FaultType.LINK_DOWN, link.id, {}, link_label)
        add(FaultType.PACKET_LOSS, link.id, {"loss_rate": 0.5}, link_label)
        add(FaultType.HIGH_LATENCY, link.id, {"added_latency_ms": 400.0}, link_label)
        add(FaultType.MTU_BLACK_HOLE, link.id, {"mtu_bytes": 576}, link_label)
        # A black hole is scoped to one destination, so offer one entry per
        # server-side destination that is not an endpoint of this link.
        for destination in app_servers:
            if destination.id in link.endpoints():
                continue
            add(
                FaultType.ROUTE_BLACKHOLE,
                link.id,
                {"destination_node_id": destination.id},
                f"{link_label} -> black-hole route to {destination.name}",
            )
    for resolver in resolvers:
        add(FaultType.DNS_FAILURE, resolver.id, {}, f"{resolver.name} ({resolver.id})")
    for node in app_servers:
        for service in sorted(node.services, key=lambda item: item.name):
            target = f"{node.id}:{service.name}"
            label = f"{service.name} on {node.name} (port {service.port})"
            add(FaultType.TCP_PORT_BLOCKED, target, {}, f"{label} — firewall DROP")
            add(FaultType.TCP_PORT_REJECTED, target, {}, f"{label} — firewall REJECT")
            add(FaultType.SERVICE_DOWN, target, {}, f"{label} — process not serving")
    for host in sorted(topology.hosts(), key=lambda item: item.id):
        gateway = host.gateway
        if not gateway:
            continue
        for link in topology.links_of(host.id):
            if gateway in link.endpoints():
                add(
                    FaultType.GATEWAY_UNREACHABLE,
                    link.id,
                    {},
                    f"{host.name} default-gateway link ({link.id})",
                )
                break
    return out


class DiagnosisService:
    """Owns running diagnoses and their persistence."""

    def __init__(self, database: Database, sessions: SessionService) -> None:
        self.db = database
        self.sessions = sessions
        self._runs: dict[str, DiagnosisRun] = {}

    # ---- lifecycle -------------------------------------------------------
    def create(
        self,
        *,
        session_id: str,
        source_node_id: str,
        destination_node_id: str,
        destination_service: str | None = None,
        port: int | None = None,
        max_probes: int = 8,
        strategy: str = "adaptive",
        run_to_completion: bool = False,
    ) -> DiagnosisRun:
        record = self.db.get_session(session_id)
        lab = self.sessions._lab_for(record)
        topology = lab.topology
        if not topology.has_node(source_node_id):
            raise ValidationError(
                f"unknown source node id: {source_node_id}", field="source_node_id"
            )
        if not topology.has_node(destination_node_id):
            raise ValidationError(
                f"unknown destination node id: {destination_node_id}",
                field="destination_node_id",
            )
        if source_node_id == destination_node_id:
            raise ValidationError(
                "a diagnosis needs two different nodes as source and destination",
                field="destination_node_id",
            )
        if destination_service:
            topology.find_service(destination_node_id, destination_service)
        elif port is None and not topology.node(destination_node_id).services:
            raise ValidationError(
                f"node {destination_node_id} exposes no TCP service, so a "
                "destination_service or port is required",
                field="destination_service",
            )

        run = run_diagnosis(
            diagnosis_id=_new_id("diag"),
            session_id=session_id,
            lab=lab,
            topology=topology,
            source_node_id=source_node_id,
            destination_node_id=destination_node_id,
            destination_service=destination_service,
            port=port,
            strategy=strategy,
            max_probes=max_probes,
            to_completion=run_to_completion,
        )
        self._runs[run.diagnosis_id] = run
        self._persist(run)
        return run

    def get(self, diagnosis_id: str) -> DiagnosisRun:
        run = self._runs.get(diagnosis_id)
        if run is not None:
            return run
        record = self.db.get_diagnosis(diagnosis_id)
        stored = record.get("result_json")
        if not stored:
            raise NotFoundError(
                f"diagnosis {diagnosis_id} has no stored result; it may have been "
                "started in a previous process",
                field="diagnosis_id",
            )
        raise NotFoundError(
            f"diagnosis {diagnosis_id} is not loaded in this process; use the stored "
            "record or re-run it",
            field="diagnosis_id",
        )

    def step(self, diagnosis_id: str) -> DiagnosisRun:
        run = self.get(diagnosis_id)
        if run.status != "running":
            raise ConflictError(
                f"diagnosis {diagnosis_id} already finished with status {run.status}",
                field="diagnosis_id",
            )
        self._require_unchanged_lab(run)
        run.step()
        self._persist(run)
        return run

    def run(self, diagnosis_id: str) -> DiagnosisRun:
        run = self.get(diagnosis_id)
        self._require_unchanged_lab(run)
        run.run_to_completion()
        self._persist(run)
        return run

    def _require_unchanged_lab(self, run: DiagnosisRun) -> None:
        """Refuse to continue a run whose session faults have changed.

        A diagnosis freezes the lab state it was created with (see
        :class:`~app.diagnosis.runner.DiagnosisRun`). If the session's faults have
        been injected, toggled, removed or reset since then, continuing would mix
        observations from two different network states into one posterior — a
        confident answer computed from inconsistent evidence. Instead the run is
        stopped with an actionable error and the caller starts a fresh diagnosis.
        """
        record = self.db.get_session(run.session_id)
        current = fault_signature(json_faults(record["active_faults"]))
        if current != run.frozen_fault_signature:
            raise ConflictError(
                f"the lab state changed after diagnosis {run.diagnosis_id} started "
                f"(it was created with {len(run.frozen_fault_signature)} fault(s) and the "
                f"session now has {len(current)}). Continuing would mix evidence from two "
                "different network states. Start a new diagnosis against the current lab.",
                field="session_id",
                frozen_faults=len(run.frozen_fault_signature),
                current_faults=len(current),
            )

    def baseline_for(self, diagnosis_id: str, *, max_probes: int | None = None) -> DiagnosisRun:
        """Run (or reuse) an equivalent baseline diagnosis for comparison.

        The baseline runs on the same session, so it sees the same topology and the
        same active faults. It uses the same budget, priors, likelihood table and
        stopping rule; only the probe order differs (plan.md section 10).
        """
        source = self.get(diagnosis_id)
        return self.create(
            session_id=source.session_id,
            source_node_id=source.source_node_id,
            destination_node_id=source.destination_node_id,
            destination_service=source.destination_service,
            port=source.port,
            max_probes=max_probes or source.max_probes,
            strategy="baseline",
            run_to_completion=True,
        )

    def list_for_session(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return [
            self._summary(row)
            for row in self.db.list_diagnoses(session_id=session_id, limit=limit)
        ]

    def list_all(self, limit: int = 20) -> list[dict[str, Any]]:
        """Summaries across every session, newest first."""
        return [self._summary(row) for row in self.db.list_diagnoses(limit=limit)]

    def _summary(self, row: dict[str, Any]) -> dict[str, Any]:
        """One list entry for a stored diagnosis.

        A run still loaded in this process reports its live belief; a run from a
        previous process reports the belief frozen into its stored result. Both
        paths produce the same shape, so a client never has to branch on which
        one it received.
        """
        run = self._runs.get(row["id"])
        if run is not None:
            beliefs = run.beliefs()
            ranked = beliefs["ranked"]
            return {
                "id": row["id"],
                "session_id": row["session_id"],
                "status": run.status,
                "strategy": run.strategy,
                "source_node_id": run.source_node_id,
                "destination_node_id": run.destination_node_id,
                "destination_service": run.destination_service,
                "port": run.port,
                "probes_used": run.probes_used,
                "max_probes": run.max_probes,
                "top_hypothesis": ranked[0]["code"],
                "top_probability": ranked[0]["probability"],
                "entropy_bits": beliefs["entropy_bits"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
            }

        stored = row.get("result_json")
        parsed = json.loads(stored) if stored else {}
        beliefs = parsed.get("beliefs") or {}
        ranked = beliefs.get("ranked") or [{}]
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "strategy": row["strategy"],
            "source_node_id": row["source_node_id"],
            "destination_node_id": row["destination_node_id"],
            "destination_service": row["destination_service"],
            "port": row["port"],
            "probes_used": row["probes_used"],
            "max_probes": row["max_probes"],
            "top_hypothesis": ranked[0].get("code"),
            "top_probability": ranked[0].get("probability"),
            "entropy_bits": beliefs.get("entropy_bits"),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    def comparison(self, diagnosis_id: str) -> dict[str, Any]:
        """Adaptive-vs-baseline comparison for one diagnosis, from real runs."""
        run = self.get(diagnosis_id)
        session = self.db.get_session(run.session_id)
        records = [
            row
            for row in self.db.list_diagnoses(session_id=run.session_id, limit=100)
            if row["source_node_id"] == run.source_node_id
            and row["destination_node_id"] == run.destination_node_id
            and row["destination_service"] == run.destination_service
        ]
        adaptive = [row for row in records if row["strategy"] == "adaptive"]
        baseline = [row for row in records if row["strategy"] == "baseline"]
        return {
            "diagnosis_id": diagnosis_id,
            "session_id": run.session_id,
            "adaptive_runs": len(adaptive),
            "baseline_runs": len(baseline),
            "current_strategy": run.strategy,
            "current": _diagnosis_summary(run),
            "latest_baseline": (
                self._runs[baseline[0]["id"]].to_public()
                if baseline and baseline[0]["id"] in self._runs
                else None
            ),
            "note": (
                "Both strategies share the probe implementations, priors, likelihood "
                "model and stopping rule; only the selection order differs. The "
                "authoritative comparison is the Experiment Studio suite, which runs "
                "each scenario with fixed seeds."
            ),
            "session_name": session["name"],
        }

    # ---- persistence -----------------------------------------------------
    def _persist(self, run: DiagnosisRun) -> None:
        payload = run.to_public()
        self.db.save_diagnosis(
            {
                "id": run.diagnosis_id,
                "session_id": run.session_id,
                "source_node_id": run.source_node_id,
                "destination_node_id": run.destination_node_id,
                "destination_service": run.destination_service,
                "port": run.port,
                "strategy": run.strategy,
                "status": run.status,
                "max_probes": run.max_probes,
                "probes_used": run.probes_used,
                "random_seed": run.random_seed,
                "hostname": run.hostname,
                "gateway_node_id": run.gateway_node_id,
                "resolver_node_id": run.resolver_node_id,
                "control_node_id": run.control_node_id,
                "result": payload,
                "stopping_reason": run.stopping_reason,
                "started_at": payload["started_at"],
                "completed_at": payload["completed_at"],
            }
        )
        # Observations are append-only; re-persisting the whole list keeps the
        # storage layer stateless with respect to how far the run has progressed.
        self.db.delete_diagnosis_observations(run.diagnosis_id)
        for step in run.steps:
            public_step = step.to_public()
            public_step["details"].setdefault("source_node_id", run.source_node_id)
            public_step["details"].setdefault("destination_node_id", run.destination_node_id)
            observation_id = self.db.save_observation(run.diagnosis_id, public_step)
            self.db.save_belief_snapshot(
                run.diagnosis_id,
                step.sequence_number,
                step.to_public()["belief_after"],
                step.update.floored_likelihoods,
                step.entropy_before,
                step.entropy_after,
                observation_id=observation_id,
            )


def _diagnosis_summary(run: DiagnosisRun) -> dict[str, Any]:
    beliefs = run.beliefs()
    return {
        "id": run.diagnosis_id,
        "strategy": run.strategy,
        "status": run.status,
        "probes_used": run.probes_used,
        "top_hypothesis": beliefs["ranked"][0]["code"],
        "top_probability": beliefs["ranked"][0]["probability"],
        "entropy_bits": beliefs["entropy_bits"],
        "stopping_reason": run.stopping_reason,
    }


def template_listing() -> dict[str, Any]:
    return {
        "templates": template_summaries(),
        "template_ids": list_template_ids(),
    }


__all__ = [
    "DiagnosisService",
    "SessionService",
    "available_faults",
    "template_listing",
]

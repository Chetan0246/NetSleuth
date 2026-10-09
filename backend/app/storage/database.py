"""SQLite persistence for sessions, diagnoses, observations and experiment runs.

Deliberately plain ``sqlite3`` with hand-written SQL rather than an ORM: the schema
is small, the access patterns are simple, and keeping the SQL visible makes the
stored evidence easy to audit — which matters for a project whose whole point is
that its conclusions are traceable to recorded observations.

Ground-truth fault information lives in the experiment tables only. The diagnostic
tables (``diagnoses``, ``observations``, ``belief_snapshots``) never store an
injected fault type, so a diagnosis record cannot leak the answer back into the
engine.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.config import MODEL_VERSION, PRIOR_CONFIG_VERSION, default_db_path
from ..core.errors import NotFoundError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lab_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'simulated',
    topology_json TEXT NOT NULL,
    active_faults_json TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES lab_sessions(id) ON DELETE CASCADE,
    source_node_id TEXT NOT NULL,
    destination_node_id TEXT NOT NULL,
    destination_service TEXT,
    port INTEGER,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    max_probes INTEGER NOT NULL,
    probes_used INTEGER NOT NULL DEFAULT 0,
    prior_config_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    hostname TEXT,
    gateway_node_id TEXT,
    resolver_node_id TEXT,
    control_node_id TEXT,
    result_json TEXT,
    stopping_reason TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS probe_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id TEXT NOT NULL REFERENCES diagnoses(id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    probe_key TEXT NOT NULL,
    probe_type TEXT NOT NULL,
    probe_label TEXT NOT NULL,
    mode TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    destination_node_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    selected_reason TEXT,
    information_gain REAL,
    modelled_elapsed_ms REAL NOT NULL DEFAULT 0,
    measured_wall_clock_ms REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS belief_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id TEXT NOT NULL REFERENCES diagnoses(id) ON DELETE CASCADE,
    observation_id INTEGER,
    sequence_number INTEGER NOT NULL,
    ranked_hypotheses_json TEXT NOT NULL,
    likelihoods_json TEXT NOT NULL,
    entropy_before REAL NOT NULL,
    entropy_after REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    total_runs INTEGER NOT NULL DEFAULT 0,
    completed_runs INTEGER NOT NULL DEFAULT 0,
    metrics_json TEXT,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL,
    seed INTEGER NOT NULL,
    template_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    destination_node_id TEXT NOT NULL,
    destination_service TEXT,
    strategy TEXT NOT NULL,
    actual_fault_type TEXT,
    actual_target_id TEXT,
    predicted_fault_type TEXT,
    predicted_probability REAL,
    predicted_target_id TEXT,
    localized_target_id TEXT,
    localization_confidence TEXT,
    is_correct_top1 INTEGER NOT NULL DEFAULT 0,
    is_correct_top3 INTEGER NOT NULL DEFAULT 0,
    is_localization_correct INTEGER,
    is_inconclusive INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    probes_used INTEGER NOT NULL,
    elapsed_ms REAL NOT NULL DEFAULT 0,
    ranked_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_diagnosis
    ON probe_observations(diagnosis_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_snapshots_diagnosis
    ON belief_snapshots(diagnosis_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_experiment_runs_experiment
    ON experiment_runs(experiment_id, strategy, scenario_id);
CREATE INDEX IF NOT EXISTS idx_diagnoses_session
    ON diagnoses(session_id, started_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite access.

    ``check_same_thread=False`` plus an explicit lock is used because FastAPI runs
    synchronous route handlers in a thread pool; serialising writes keeps the
    single-file database consistent without pulling in a connection pool for a
    lab-scale workload.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._lock = threading.RLock()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.execute(
                "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                yield cursor
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            finally:
                cursor.close()

    # ---- sessions --------------------------------------------------------
    def save_session(self, record: dict[str, Any]) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lab_sessions(
                    id, name, template_id, template_name, mode, topology_json,
                    active_faults_json, random_seed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    topology_json = excluded.topology_json,
                    active_faults_json = excluded.active_faults_json,
                    random_seed = excluded.random_seed,
                    updated_at = excluded.updated_at
                """,
                (
                    record["id"],
                    record["name"],
                    record["template_id"],
                    record["template_name"],
                    record.get("mode", "simulated"),
                    json.dumps(record["topology"]),
                    json.dumps(record.get("active_faults", [])),
                    record["random_seed"],
                    record["created_at"],
                    record["updated_at"],
                ),
            )

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM lab_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"unknown session id: {session_id}", field="session_id")
        return _session_row(row)

    def session_exists(self, session_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM lab_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row is not None

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM lab_sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_session_row(row) for row in rows]

    def count_sessions(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS n FROM lab_sessions").fetchone()
        return int(row["n"])

    def delete_session(self, session_id: str) -> bool:
        with self._cursor() as cursor:
            cursor.execute("DELETE FROM lab_sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    # ---- diagnoses -------------------------------------------------------
    def save_diagnosis(self, record: dict[str, Any]) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO diagnoses(
                    id, session_id, source_node_id, destination_node_id, destination_service,
                    port, strategy, status, max_probes, probes_used, prior_config_version,
                    model_version, random_seed, hostname, gateway_node_id, resolver_node_id,
                    control_node_id, result_json, stopping_reason, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    probes_used = excluded.probes_used,
                    result_json = excluded.result_json,
                    stopping_reason = excluded.stopping_reason,
                    completed_at = excluded.completed_at
                """,
                (
                    record["id"],
                    record["session_id"],
                    record["source_node_id"],
                    record["destination_node_id"],
                    record.get("destination_service"),
                    record.get("port"),
                    record["strategy"],
                    record["status"],
                    record["max_probes"],
                    record.get("probes_used", 0),
                    PRIOR_CONFIG_VERSION,
                    MODEL_VERSION,
                    record["random_seed"],
                    record.get("hostname"),
                    record.get("gateway_node_id"),
                    record.get("resolver_node_id"),
                    record.get("control_node_id"),
                    json.dumps(record.get("result")),
                    record.get("stopping_reason"),
                    record["started_at"],
                    record.get("completed_at"),
                ),
            )

    def get_diagnosis(self, diagnosis_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM diagnoses WHERE id = ?", (diagnosis_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"unknown diagnosis id: {diagnosis_id}", field="diagnosis_id")
        return dict(row)

    def list_diagnoses(self, session_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            if session_id:
                rows = self._connection.execute(
                    "SELECT * FROM diagnoses WHERE session_id = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM diagnoses ORDER BY started_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

    def count_diagnoses(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS n FROM diagnoses").fetchone()
        return int(row["n"])

    def save_observation(
        self,
        diagnosis_id: str,
        step: dict[str, Any],
        observation_id: int | None = None,
    ) -> int:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO probe_observations(
                    diagnosis_id, sequence_number, probe_key, probe_type, probe_label, mode,
                    source_node_id, destination_node_id, outcome, summary, details_json,
                    evidence_json, selected_reason, information_gain, modelled_elapsed_ms,
                    measured_wall_clock_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diagnosis_id,
                    step["sequence_number"],
                    step["probe_key"],
                    step["probe_type"],
                    step["probe_label"],
                    step["mode"],
                    step["details"].get("source_node_id", ""),
                    step["details"].get("destination_node_id", ""),
                    step["outcome"],
                    step["summary"],
                    json.dumps(step["details"]),
                    json.dumps(step["evidence"]),
                    step.get("selected_reason"),
                    step.get("planned_information_gain_bits"),
                    step.get("modelled_elapsed_ms", 0.0),
                    step.get("measured_wall_clock_ms", 0.0),
                    step["created_at"],
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_observations(self, diagnosis_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM probe_observations WHERE diagnosis_id = ? "
                "ORDER BY sequence_number",
                (diagnosis_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_belief_snapshot(
        self,
        diagnosis_id: str,
        sequence_number: int,
        ranked: list[dict[str, Any]],
        likelihoods: dict[str, Any],
        entropy_before: float,
        entropy_after: float,
        observation_id: int | None = None,
    ) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO belief_snapshots(
                    diagnosis_id, observation_id, sequence_number, ranked_hypotheses_json,
                    likelihoods_json, entropy_before, entropy_after, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diagnosis_id,
                    observation_id,
                    sequence_number,
                    json.dumps(ranked),
                    json.dumps(likelihoods),
                    entropy_before,
                    entropy_after,
                    _now(),
                ),
            )

    def list_belief_snapshots(self, diagnosis_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM belief_snapshots WHERE diagnosis_id = ? "
                "ORDER BY sequence_number",
                (diagnosis_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_diagnosis_observations(self, diagnosis_id: str) -> None:
        """Drop the stored observations/snapshots of one diagnosis.

        Used before re-persisting a diagnosis that has advanced by another step:
        storage stays a faithful projection of the in-memory run instead of
        accumulating duplicated rows.
        """
        with self._cursor() as cursor:
            cursor.execute(
                "DELETE FROM belief_snapshots WHERE diagnosis_id = ?", (diagnosis_id,)
            )
            cursor.execute(
                "DELETE FROM probe_observations WHERE diagnosis_id = ?", (diagnosis_id,)
            )

    # ---- experiments -----------------------------------------------------
    def save_experiment(self, record: dict[str, Any]) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO experiments(
                    id, name, config_json, status, total_runs, completed_runs,
                    metrics_json, summary_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    completed_runs = excluded.completed_runs,
                    metrics_json = excluded.metrics_json,
                    summary_json = excluded.summary_json,
                    completed_at = excluded.completed_at
                """,
                (
                    record["id"],
                    record["name"],
                    json.dumps(record["config"]),
                    record["status"],
                    record.get("total_runs", 0),
                    record.get("completed_runs", 0),
                    json.dumps(record.get("metrics")) if record.get("metrics") else None,
                    json.dumps(record.get("summary")) if record.get("summary") else None,
                    record["created_at"],
                    record.get("completed_at"),
                ),
            )

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"unknown experiment id: {experiment_id}", field="experiment_id")
        return dict(row)

    def list_experiments(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def save_experiment_run(self, experiment_id: str, run: dict[str, Any]) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO experiment_runs(
                    experiment_id, scenario_id, seed, template_id, source_node_id,
                    destination_node_id, destination_service, strategy, actual_fault_type,
                    actual_target_id, predicted_fault_type, predicted_probability,
                    predicted_target_id, localized_target_id, localization_confidence,
                    is_correct_top1, is_correct_top3, is_localization_correct, is_inconclusive,
                    status, probes_used, elapsed_ms, ranked_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    run["scenario_id"],
                    run["seed"],
                    run["template_id"],
                    run["source_node_id"],
                    run["destination_node_id"],
                    run.get("destination_service"),
                    run["strategy"],
                    run.get("actual_fault_type"),
                    run.get("actual_target_id"),
                    run.get("predicted_fault_type"),
                    run.get("predicted_probability"),
                    run.get("predicted_target_id"),
                    run.get("localized_target_id"),
                    run.get("localization_confidence"),
                    1 if run.get("is_correct_top1") else 0,
                    1 if run.get("is_correct_top3") else 0,
                    (
                        None
                        if run.get("is_localization_correct") is None
                        else (1 if run["is_localization_correct"] else 0)
                    ),
                    1 if run.get("is_inconclusive") else 0,
                    run["status"],
                    run["probes_used"],
                    run.get("elapsed_ms", 0.0),
                    json.dumps(run.get("ranked", [])),
                    run["created_at"],
                ),
            )

    def list_experiment_runs(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM experiment_runs WHERE experiment_id = ? "
                "ORDER BY strategy, scenario_id, id",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_experiment_runs(self, experiment_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n FROM experiment_runs WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        return int(row["n"])

    # ---- diagnostics -----------------------------------------------------
    def stats(self) -> dict[str, int]:
        with self._lock:
            sessions = self._connection.execute(
                "SELECT COUNT(*) AS n FROM lab_sessions"
            ).fetchone()["n"]
            diagnoses = self._connection.execute(
                "SELECT COUNT(*) AS n FROM diagnoses"
            ).fetchone()["n"]
            observations = self._connection.execute(
                "SELECT COUNT(*) AS n FROM probe_observations"
            ).fetchone()["n"]
            experiments = self._connection.execute(
                "SELECT COUNT(*) AS n FROM experiments"
            ).fetchone()["n"]
            runs = self._connection.execute(
                "SELECT COUNT(*) AS n FROM experiment_runs"
            ).fetchone()["n"]
        return {
            "sessions": int(sessions),
            "diagnoses": int(diagnoses),
            "observations": int(observations),
            "experiments": int(experiments),
            "experiment_runs": int(runs),
        }


def _session_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "template_id": row["template_id"],
        "template_name": row["template_name"],
        "mode": row["mode"],
        "topology": json.loads(row["topology_json"]),
        "active_faults": json.loads(row["active_faults_json"]),
        "random_seed": row["random_seed"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


__all__ = ["Database", "SCHEMA_VERSION"]

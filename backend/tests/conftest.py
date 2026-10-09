"""Shared pytest fixtures.

Every test runs against an in-memory SQLite database and a temporary directory, so
the suite never reads or writes a developer's real data directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lab.faults import (  # noqa: E402
    FaultSpec,
    FaultType,
    LabState,
    normalize_fault,
)
from app.lab.simulator import LabSimulator  # noqa: E402
from app.lab.templates import get_template  # noqa: E402

DEFAULT_SEED = 20261009


@pytest.fixture()
def campus_lab() -> LabState:
    return LabState(get_template("campus-basic"), random_seed=DEFAULT_SEED)


@pytest.fixture()
def multihop_lab() -> LabState:
    return LabState(get_template("multihop-wan"), random_seed=DEFAULT_SEED)


@pytest.fixture()
def campus_sim(campus_lab: LabState) -> LabSimulator:
    return LabSimulator(campus_lab)


def inject(
    lab: LabState, fault_type: FaultType, target_id: str, **parameters: object
) -> None:
    """Inject one fault into a lab, using the real validation path."""
    spec = FaultSpec(
        fault_type=fault_type, target_id=target_id, parameters=dict(parameters)
    )
    lab.add_fault(normalize_fault(spec, lab.topology, f"test-{fault_type.value.lower()}"))


@pytest.fixture()
def client():
    """FastAPI test client backed by an in-memory database."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(":memory:")
    with TestClient(app) as test_client:
        yield test_client
    app.state.database.close()

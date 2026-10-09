"""FastAPI application factory, dependencies and route registration.

The app is built by :func:`create_app` so tests can instantiate it with an
in-memory database and an isolated working directory instead of touching the
developer's ``data/`` directory.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import routes_diagnosis, routes_experiments, routes_health, routes_lab
from .api.deps import get_database, get_diagnoses, get_sessions
from .core.config import (
    API_PREFIX,
    APP_NAME,
    APP_VERSION,
    MODEL_VERSION,
    PRIOR_CONFIG_VERSION,
)
from .core.errors import install_error_handlers
from .storage.database import Database
from .storage.repository import DiagnosisService, SessionService

logger = logging.getLogger("netsleuth")


def create_app(database_path: Path | str | None = None, *, cors_origins: list[str] | None = None) -> FastAPI:
    database = Database(database_path)
    sessions = SessionService(database)
    diagnoses = DiagnosisService(database, sessions)

    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=(
            "Evidence-guided network fault localization in a deterministic virtual lab. "
            "Simulated observations are labelled SIMULATED LAB; optional live probes are "
            "labelled LIVE PROBE and are never mixed into a simulated timeline."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.database = database
    app.state.sessions = sessions
    app.state.diagnoses = diagnoses

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:5173", "http://127.0.0.1:5173",
                                       "http://localhost:4173", "http://127.0.0.1:4173"],
        allow_credentials=False,
        # PATCH is required: the fault-enable/disable endpoint uses it
        # (`PATCH /lab/sessions/{id}/faults/{fault_id}`). Omitting it only breaks
        # cross-origin use, which the Vite proxy normally hides — so the omission
        # would have surfaced first in a deployment that serves the frontend from a
        # different origin than the API.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    install_error_handlers(app)
    app.include_router(routes_health.router, prefix=API_PREFIX)
    app.include_router(routes_lab.router, prefix=API_PREFIX)
    app.include_router(routes_diagnosis.router, prefix=API_PREFIX)
    app.include_router(routes_experiments.router, prefix=API_PREFIX)

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover - lifecycle hook
        database.close()

    @app.get("/", include_in_schema=False)
    def _root() -> dict[str, str]:
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "api": API_PREFIX,
            "docs": "/docs",
            "model_version": MODEL_VERSION,
            "prior_config_version": PRIOR_CONFIG_VERSION,
        }

    return app


#: Module-level ASGI application for ``uvicorn app.main:app``.
#:
#: The factory above exists so tests can inject an in-memory database; this
#: instance is the deployment entry point and uses the default on-disk database
#: under ``backend/data/``. ``NETSLEUTH_DB`` overrides that location.
app = create_app()


__all__ = [
    "app",
    "create_app",
    "get_database",
    "get_diagnoses",
    "get_sessions",
]

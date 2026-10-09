"""Shared FastAPI dependencies.

Kept in their own module (rather than in ``app.main``) so routers can depend on
them without importing the application factory, which would create a circular
import.
"""

from __future__ import annotations

from fastapi import Request

from ..storage.database import Database
from ..storage.repository import DiagnosisService, SessionService


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_sessions(request: Request) -> SessionService:
    return request.app.state.sessions


def get_diagnoses(request: Request) -> DiagnosisService:
    return request.app.state.diagnoses


__all__ = ["get_database", "get_diagnoses", "get_sessions"]

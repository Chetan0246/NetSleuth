"""Virtual-lab endpoints: templates, sessions, faults and reset."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .deps import get_database, get_sessions
from ..models.schemas import (
    FaultListResponse,
    FaultRequest,
    FaultToggleRequest,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
    TemplateListResponse,
)
from ..storage.database import Database
from ..storage.repository import SessionService, template_listing

router = APIRouter(tags=["lab"])


@router.get("/lab/templates", response_model=TemplateListResponse)
def list_templates() -> TemplateListResponse:
    return TemplateListResponse(**template_listing())


@router.post("/lab/sessions", response_model=SessionResponse, status_code=201)
def create_session(
    payload: SessionCreateRequest,
    sessions: SessionService = Depends(get_sessions),
) -> SessionResponse:
    return SessionResponse(**sessions.create_session(
        payload.template_id, name=payload.name, random_seed=payload.random_seed
    ))


@router.get("/lab/sessions", response_model=SessionListResponse)
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    sessions: SessionService = Depends(get_sessions),
) -> SessionListResponse:
    items = sessions.list_sessions(limit=limit)
    return SessionListResponse(sessions=items, total=len(items))


@router.get("/lab/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str, sessions: SessionService = Depends(get_sessions)
) -> SessionResponse:
    return SessionResponse(**sessions.get_session(session_id))


@router.put("/lab/sessions/{session_id}/faults", response_model=SessionResponse)
def put_fault(
    session_id: str,
    payload: FaultRequest,
    sessions: SessionService = Depends(get_sessions),
) -> SessionResponse:
    return SessionResponse(
        **sessions.apply_fault(session_id, payload.fault, fault_id=payload.fault_id)
    )


@router.get("/lab/sessions/{session_id}/faults", response_model=FaultListResponse)
def get_faults(
    session_id: str, sessions: SessionService = Depends(get_sessions)
) -> FaultListResponse:
    return FaultListResponse(**sessions.list_faults(session_id))


@router.patch("/lab/sessions/{session_id}/faults/{fault_id}", response_model=SessionResponse)
def toggle_fault(
    session_id: str,
    fault_id: str,
    payload: FaultToggleRequest,
    sessions: SessionService = Depends(get_sessions),
) -> SessionResponse:
    return SessionResponse(
        **sessions.set_fault_active(session_id, fault_id, payload.is_active)
    )


@router.delete("/lab/sessions/{session_id}/faults/{fault_id}", response_model=SessionResponse)
def delete_fault(
    session_id: str,
    fault_id: str,
    sessions: SessionService = Depends(get_sessions),
) -> SessionResponse:
    return SessionResponse(**sessions.remove_fault(session_id, fault_id))


@router.post("/lab/sessions/{session_id}/reset", response_model=SessionResponse)
def reset_session(
    session_id: str,
    random_seed: int | None = Query(default=None, ge=0, le=2**31 - 1),
    sessions: SessionService = Depends(get_sessions),
) -> SessionResponse:
    return SessionResponse(**sessions.reset_session(session_id, random_seed=random_seed))


@router.delete("/lab/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    sessions: SessionService = Depends(get_sessions),
    database: Database = Depends(get_database),
) -> None:
    """Delete a session and everything stored under it.

    Destructive, so it is an explicit call the UI gates behind a confirmation.
    """
    if not database.delete_session(session_id):
        from ..core.errors import NotFoundError

        raise NotFoundError(f"unknown session id: {session_id}", field="session_id")
    sessions._labs.pop(session_id, None)
    return None

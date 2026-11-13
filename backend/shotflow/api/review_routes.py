"""Review decisions and comments."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..models import Version
from ..services.review import add_note
from . import serializers as ser
from .deps import get_or_404, session_dependency


class NoteIn(BaseModel):
    author: str = Field(default="리뷰어", max_length=80)
    body: str = Field(default="", max_length=4000)
    decision: str = Field(default="comment", pattern="^(approve|reject|revise|comment)$")
    frame: int | None = Field(default=None, ge=0)


def router(make_session: sessionmaker[Session]) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    @api.post("/versions/{version_id}/notes", status_code=201)
    def create_note(version_id: int, payload: NoteIn, session: Session = Depends(db)) -> dict:
        version = get_or_404(session, Version, version_id)
        try:
            add_note(session, version, author=payload.author, body=payload.body,
                     decision=payload.decision, frame=payload.frame)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        session.flush()
        session.refresh(version)
        return ser.version_detail(version)

    return api

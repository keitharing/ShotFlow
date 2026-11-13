"""Edit decision lists: clone, update, colour suggestion and assembly."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db import session_scope
from ..models import Edit
from ..services import assemble as asm
from ..services import boundary
from ..services.qc import run_qc
from ..services.worker import worker
from . import serializers as ser
from .deps import get_or_404, session_dependency


class CloneIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class EntryIn(BaseModel):
    version_id: int
    src_in: int = Field(ge=0)
    src_out: int = Field(gt=0)
    transition: str = Field(default="cut", pattern="^(cut|dissolve)$")
    transition_frames: int = Field(default=0, ge=0, le=96)
    color: dict[str, float] = Field(default_factory=dict)


class EditUpdateIn(BaseModel):
    entries: list[EntryIn] | None = None
    settings: dict[str, Any] | None = None


class AssembleIn(BaseModel):
    version_code: str | None = Field(default=None, max_length=40)


def assemble_job(edit_id: int, version_code: str | None, settings: Settings) -> dict[str, Any]:
    """Render, register, QC and refresh the boundary sheet: the whole review-ready package."""
    with session_scope(settings) as session:
        edit = session.get(Edit, edit_id)
        version = asm.assemble(session, edit, settings, version_code, actor="ui")
        session.flush()
        run = run_qc(session, version, actor="ui")
        sheet, infos = boundary.render_sheet(edit, settings)
        edit.settings = {**edit.settings, "boundary_sheet": str(sheet), "boundaries": [i.__dict__ for i in infos]}
        return {"edit_id": edit_id, "version_id": version.id, "version_code": version.code,
                "frames": version.frame_count, "qc_status": run.status}


def router(make_session: sessionmaker[Session], settings: Settings) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    @api.post("/edits/{edit_id}/clone", status_code=201)
    def clone(edit_id: int, payload: CloneIn, session: Session = Depends(db)) -> dict:
        base = get_or_404(session, Edit, edit_id)
        if any(e.name == payload.name for e in base.sequence.edits):
            raise HTTPException(409, f"Edit name '{payload.name}' already exists in this sequence")
        return ser.edit(asm.clone_edit(session, base, payload.name, actor="ui"))

    @api.put("/edits/{edit_id}")
    def update(edit_id: int, payload: EditUpdateIn, session: Session = Depends(db)) -> dict:
        edit = get_or_404(session, Edit, edit_id)
        try:
            asm.update_edit(session, edit,
                            [e.model_dump() for e in payload.entries] if payload.entries is not None else None,
                            payload.settings, actor="ui")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return ser.edit(edit)

    @api.get("/edits/{edit_id}/color-suggestion")
    def color_suggestion(edit_id: int, reference: str, strength: float = 0.5, limit: float = 0.04,
                         session: Session = Depends(db)) -> dict:
        edit = get_or_404(session, Edit, edit_id)
        try:
            return {"reference": reference, "brightness": asm.suggest_brightness(edit, reference, strength, limit)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.post("/edits/{edit_id}/assemble", status_code=202)
    def queue_assemble(edit_id: int, payload: AssembleIn, session: Session = Depends(db)) -> dict:
        edit = get_or_404(session, Edit, edit_id)
        if not edit.entries:
            raise HTTPException(422, "Edit has no entries")
        task = worker.submit("assemble", f"edit:{edit_id}",
                             lambda: assemble_job(edit_id, payload.version_code, settings))
        return task.as_dict()

    @api.get("/edits/{edit_id}/renders")
    def renders(edit_id: int, session: Session = Depends(db)) -> list[dict]:
        edit = get_or_404(session, Edit, edit_id)
        return sorted(((edit.settings or {}).get("renders") or {}).values(), key=lambda r: r["version_id"])

    return api

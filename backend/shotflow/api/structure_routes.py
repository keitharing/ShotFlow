"""Routes that create sequences and shots by hand (week 6), plus film size, first edit and image import (week 7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Project, Sequence, Shot, Version
from ..services import structure
from . import serializers as ser
from .deps import get_or_404, session_dependency


class ShotIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    description: str | None = None


class SequenceIn(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    target_duration_s: float | None = Field(default=None, gt=0)
    shots: list[ShotIn] = Field(min_length=1)


class FrameSizeIn(BaseModel):
    width: int | None = None
    height: int | None = None


class NameIn(BaseModel):
    name: str = ""


class EditIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def router(make_session: sessionmaker[Session], settings: Settings | None = None) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    @api.post("/projects/{project_id}/sequences", status_code=201)
    def create_sequence(project_id: int, payload: SequenceIn, session: Session = Depends(db)) -> dict:
        project = get_or_404(session, Project, project_id)
        try:
            sequence = structure.create_sequence(
                session, project, code=payload.code, name=payload.name,
                target_duration_s=payload.target_duration_s,
                shots=[s.model_dump() for s in payload.shots])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return ser.sequence(sequence)

    @api.post("/sequences/{sequence_id}/shots", status_code=201)
    def add_shot(sequence_id: int, payload: ShotIn, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        try:
            shot = structure.add_shot(session, sequence, code=payload.code, description=payload.description)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return ser.shot(shot)

    @api.put("/sequences/{sequence_id}/frame-size")
    def frame_size(sequence_id: int, payload: FrameSizeIn, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        try:
            structure.set_frame_size(session, sequence, payload.width, payload.height)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        width, height = sequence.frame_size
        return {"id": sequence.id, "code": sequence.code, "width": width, "height": height,
                "own_size": sequence.width is not None}

    @api.put("/sequences/{sequence_id}/name")
    def rename(sequence_id: int, payload: NameIn, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        try:
            structure.rename_sequence(session, sequence, payload.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": sequence.id, "code": sequence.code, "name": sequence.name}

    @api.post("/sequences/{sequence_id}/edits", status_code=201)
    def create_edit(sequence_id: int, payload: EditIn, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        try:
            edit = structure.create_edit(session, sequence, name=payload.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": edit.id, "name": edit.name, "sequence_id": sequence.id}

    @api.post("/shots/{shot_id}/versions/import", status_code=201)
    async def import_image(shot_id: int, file: UploadFile = File(...), parent_version_id: str = Form(""),
                           note: str = Form(""), author: str = Form("user"),
                           session: Session = Depends(db)) -> dict:
        shot = get_or_404(session, Shot, shot_id)
        parent = get_or_404(session, Version, int(parent_version_id)) if parent_version_id.strip() else None
        try:
            version = structure.import_image(session, shot, file.filename or "", await file.read(), parent=parent,
                                             note=note, actor=author or "user", settings=settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"version_id": version.id, "version_code": version.code,
                "qc": version.qc_runs[-1].status if version.qc_runs else None}

    return api

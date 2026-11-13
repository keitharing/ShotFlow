"""Animatic -> blocking routes (decision D-015): upload an animatic, link its cuts to shots, mark a drawing,
see the camera numbers right away, then build the .blend in the Blender queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Animatic, Sequence, Shot, ShotLayout
from ..services import animatic as anim
from ..services import layout as lay
from ..services.layout_solve import KINDS, LayoutError
from .deps import get_or_404, session_dependency


class CutChange(BaseModel):
    index: int = Field(ge=0)
    shot_id: int | None = None
    frames: dict[str, int] | None = None


class CutsIn(BaseModel):
    changes: list[CutChange] = Field(min_length=1, max_length=200)


class MarksIn(BaseModel):
    animatic_id: int
    cut_index: int = Field(ge=0)
    marks: dict[str, Any]
    source: str = "manual"


def router(make_session: sessionmaker[Session], settings: Settings) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    @api.get("/layout/kinds")
    def kinds() -> list[dict]:
        return [{"kind": k, "name": name, "height_m": h} for k, (name, h, _d) in KINDS.items()]

    # ---- animatics
    @api.get("/animatics")
    def animatics(sequence_id: int | None = None, session: Session = Depends(db)) -> list[dict]:
        rows = session.query(Animatic).order_by(Animatic.id.desc())
        if sequence_id is not None:
            rows = rows.filter(Animatic.sequence_id == sequence_id)
        return [anim.animatic_out(a) for a in rows.limit(50)]

    @api.post("/animatics", status_code=201)
    async def animatic_upload(sequence_id: int = Form(...), file: UploadFile = File(...),
                              session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        try:
            animatic = anim.import_animatic(session, sequence, file.filename or "", await file.read(), settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return anim.animatic_out(animatic)

    @api.get("/animatics/{animatic_id}")
    def animatic_detail(animatic_id: int, session: Session = Depends(db)) -> dict:
        return anim.animatic_out(get_or_404(session, Animatic, animatic_id))

    @api.patch("/animatics/{animatic_id}/cuts")
    def animatic_cuts(animatic_id: int, payload: CutsIn, session: Session = Depends(db)) -> dict:
        animatic = get_or_404(session, Animatic, animatic_id)
        try:
            anim.update_cuts(session, animatic, [c.model_dump(exclude_unset=True) for c in payload.changes])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return anim.animatic_out(animatic)

    @api.get("/animatics/{animatic_id}/frames/{frame}")
    def animatic_frame(animatic_id: int, frame: int, session: Session = Depends(db)) -> FileResponse:
        animatic = get_or_404(session, Animatic, animatic_id)
        try:
            path = anim.frame_image(animatic, frame, settings)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg")

    # ---- layouts
    @api.post("/layouts/solve")
    def layout_solve(payload: MarksIn, session: Session = Depends(db)) -> dict:
        animatic = get_or_404(session, Animatic, payload.animatic_id)
        try:
            return lay.preview_solution(animatic, payload.cut_index, payload.marks)
        except (LayoutError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.post("/layouts", status_code=202)
    def layout_create(payload: MarksIn, session: Session = Depends(db)) -> dict:
        animatic = get_or_404(session, Animatic, payload.animatic_id)
        try:
            layout = lay.create_layout(session, animatic, payload.cut_index, payload.marks, source=payload.source)
        except (LayoutError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        lay.submit(layout.generation_job_id, settings)
        return lay.layout_out(layout)

    @api.get("/layouts")
    def layouts(shot_id: int | None = None, animatic_id: int | None = None, session: Session = Depends(db)) -> list[dict]:
        rows = session.query(ShotLayout).order_by(ShotLayout.id.desc())
        if shot_id is not None:
            rows = rows.filter(ShotLayout.shot_id == shot_id)
        if animatic_id is not None:
            rows = rows.filter(ShotLayout.animatic_id == animatic_id)
        return [lay.layout_out(r) for r in rows.limit(100)]

    @api.get("/layouts/{layout_id}")
    def layout_detail(layout_id: int, session: Session = Depends(db)) -> dict:
        return lay.layout_out(get_or_404(session, ShotLayout, layout_id))

    @api.get("/shots/{shot_id}/layouts")
    def shot_layouts(shot_id: int, session: Session = Depends(db)) -> list[dict]:
        get_or_404(session, Shot, shot_id)
        rows = session.query(ShotLayout).filter(ShotLayout.shot_id == shot_id).order_by(ShotLayout.id.desc())
        return [lay.layout_out(r) for r in rows]

    return api

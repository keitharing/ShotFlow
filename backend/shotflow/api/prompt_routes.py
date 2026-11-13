"""Prompt management routes (week 8): shared blocks per film, a scene text per shot and use, and the record of
which generation used which text."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..models import Sequence, Shot
from ..services import prompts
from .deps import get_or_404, session_dependency


class BlockIn(BaseModel):
    label: str = Field(default="", max_length=80)
    text: str = Field(min_length=1, max_length=4000)
    place: str = "after"
    order: int | None = None


class ShotPromptIn(BaseModel):
    scene: str = Field(default="", max_length=4000)
    block_keys: list[str] = Field(default_factory=list, max_length=40)
    note: str | None = Field(default=None, max_length=1000)


class ApplyIn(BaseModel):
    use: str
    add: list[str] = Field(default_factory=list, max_length=40)
    remove: list[str] = Field(default_factory=list, max_length=40)
    shot_ids: list[int] | None = None


def router(make_session: sessionmaker[Session]) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    def guarded(fn):
        try:
            return fn()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.get("/sequences/{sequence_id}/prompts")
    def overview(sequence_id: int, session: Session = Depends(db)) -> dict:
        return prompts.overview(session, get_or_404(session, Sequence, sequence_id))

    @api.put("/sequences/{sequence_id}/prompt-blocks/{key}")
    def block_set(sequence_id: int, key: str, payload: BlockIn, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        return prompts.block_out(guarded(lambda: prompts.set_block(
            session, sequence, key, label=payload.label, text=payload.text, place=payload.place, order=payload.order)))

    @api.delete("/sequences/{sequence_id}/prompt-blocks/{key}")
    def block_delete(sequence_id: int, key: str, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        guarded(lambda: prompts.delete_block(session, sequence, key))
        return {"deleted": key}

    @api.post("/sequences/{sequence_id}/prompts/apply-blocks")
    def apply(sequence_id: int, payload: ApplyIn, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        return {"changed": guarded(lambda: prompts.apply_blocks(
            session, sequence, payload.use, add=payload.add, remove=payload.remove, shot_ids=payload.shot_ids))}

    @api.get("/shots/{shot_id}/prompt/{use}")
    def shot_prompt(shot_id: int, use: str, session: Session = Depends(db)) -> dict:
        shot = get_or_404(session, Shot, shot_id)
        made = guarded(lambda: prompts.compose(session, shot, use))
        return {**made, "shot_id": shot.id, "code": shot.code, "history": prompts.history(session, shot, use)}

    @api.put("/shots/{shot_id}/prompt/{use}")
    def shot_prompt_set(shot_id: int, use: str, payload: ShotPromptIn, session: Session = Depends(db)) -> dict:
        shot = get_or_404(session, Shot, shot_id)
        guarded(lambda: prompts.set_shot_prompt(session, shot, use, scene=payload.scene, block_keys=payload.block_keys,
                                                note=payload.note))
        return {**prompts.compose(session, shot, use), "shot_id": shot.id, "code": shot.code}

    @api.post("/shots/{shot_id}/prompt/{use}/snapshot", status_code=201)
    def shot_prompt_snapshot(shot_id: int, use: str, session: Session = Depends(db)) -> dict:
        shot = get_or_404(session, Shot, shot_id)
        snap = guarded(lambda: prompts.snapshot(session, shot, use))
        return {"id": snap.id, "use": snap.use, "sha256": snap.sha256, "text": snap.text, "negative": snap.negative}

    return api

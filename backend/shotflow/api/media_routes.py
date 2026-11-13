"""Browser playback: proxies, thumbnails, and sequence context for the UI."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Sequence, Version
from ..services import proxy
from . import serializers as ser
from .deps import get_or_404, session_dependency


def router(make_session: sessionmaker[Session], settings: Settings) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    @api.get("/versions/{version_id}/proxy")
    def version_proxy(version_id: int, session: Session = Depends(db)) -> FileResponse:
        version = get_or_404(session, Version, version_id)
        try:
            path = proxy.ensure_proxy(version, settings)
        except (RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(500, str(exc)) from exc
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "video/mp4")

    @api.get("/versions/{version_id}/thumbnail")
    def version_thumbnail(version_id: int, session: Session = Depends(db)) -> FileResponse:
        version = get_or_404(session, Version, version_id)
        try:
            path = proxy.ensure_thumbnail(version, settings)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(500, str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @api.get("/sequences/{sequence_id}")
    def sequence_detail(sequence_id: int, session: Session = Depends(db)) -> dict:
        sequence = get_or_404(session, Sequence, sequence_id)
        return {**ser.sequence(sequence), "project": ser.project(sequence.project),
                "edit_list": [{"id": e.id, "name": e.name, "result_version_id": e.result_version_id,
                               "entries": len(e.entries)} for e in sequence.edits]}

    return api

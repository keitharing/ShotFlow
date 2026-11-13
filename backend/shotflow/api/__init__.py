from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import __version__
from ..config import ROOT, Settings, load_settings
from ..db import init_db, session_factory
from ..models import ActivityLog, Edit, GenerationJob, Project, QCIssue, QCRun, Sequence, Shot, Version
from ..services import jobs
from ..services.worker import worker
from . import serializers as ser


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    init_db(settings)
    make_session = session_factory(settings)
    app = FastAPI(title="ShotFlow", version=__version__,
                  description="AI 영상 제작용 샷·버전·검수·납품 관리 툴 (로컬 전용)")

    def db() -> Iterator[Session]:
        session = make_session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    def get_or_404(session: Session, model, item_id: int):
        item = session.get(model, item_id)
        if item is None:
            raise HTTPException(404, f"{model.__name__} {item_id} not found")
        return item

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "data_dir": str(settings.data_dir)}

    @app.get("/api/projects")
    def projects(session: Session = Depends(db)) -> list[dict]:
        return [ser.project(p) for p in session.query(Project).order_by(Project.id)]

    @app.get("/api/projects/{project_id}/sequences")
    def sequences(project_id: int, session: Session = Depends(db)) -> list[dict]:
        return [ser.sequence(s) for s in get_or_404(session, Project, project_id).sequences]

    @app.get("/api/sequences/{sequence_id}/shots")
    def shots(sequence_id: int, session: Session = Depends(db)) -> list[dict]:
        return [ser.shot(s) for s in get_or_404(session, Sequence, sequence_id).shots]

    @app.get("/api/shots/{shot_id}")
    def shot(shot_id: int, session: Session = Depends(db)) -> dict:
        return ser.shot(get_or_404(session, Shot, shot_id))

    @app.get("/api/versions/{version_id}")
    def version(version_id: int, session: Session = Depends(db)) -> dict:
        return ser.version_detail(get_or_404(session, Version, version_id))

    @app.get("/api/versions/{version_id}/media")
    def version_media(version_id: int, session: Session = Depends(db)) -> FileResponse:
        path = Path(get_or_404(session, Version, version_id).path)
        if not path.is_file():
            raise HTTPException(410, "Media file missing on disk")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/versions/{version_id}/qc-history")
    def qc_history(version_id: int, session: Session = Depends(db)) -> list[dict]:
        return [ser.qc_run(r) for r in get_or_404(session, Version, version_id).qc_runs]

    @app.post("/api/versions/{version_id}/qc", status_code=202)
    def queue_qc(version_id: int, session: Session = Depends(db)) -> dict:
        get_or_404(session, Version, version_id)
        task = worker.submit("qc", f"version:{version_id}", lambda: jobs.qc_version(version_id, settings))
        return task.as_dict()

    @app.get("/api/edits/{edit_id}")
    def edit(edit_id: int, session: Session = Depends(db)) -> dict:
        return ser.edit(get_or_404(session, Edit, edit_id))

    @app.post("/api/edits/{edit_id}/boundary-sheet", status_code=202)
    def queue_sheet(edit_id: int, session: Session = Depends(db)) -> dict:
        get_or_404(session, Edit, edit_id)
        task = worker.submit("boundary_sheet", f"edit:{edit_id}", lambda: jobs.boundary_sheet(edit_id, settings))
        return task.as_dict()

    @app.get("/api/edits/{edit_id}/boundary-sheet")
    def sheet_image(edit_id: int, session: Session = Depends(db)) -> FileResponse:
        path = (get_or_404(session, Edit, edit_id).settings or {}).get("boundary_sheet")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Boundary sheet not generated yet (POST to create)")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/tasks")
    def tasks() -> list[dict]:
        return [t.as_dict() for t in worker.recent()]

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> dict:
        found = worker.get(task_id)
        if found is None:
            raise HTTPException(404, "Task not found")
        return found.as_dict()

    @app.get("/api/dashboard")
    def dashboard(session: Session = Depends(db)) -> dict:
        shots_q = session.query(Shot).filter(Shot.kind == "shot")
        latest_runs = (
            session.query(func.max(QCRun.id)).group_by(QCRun.version_id).scalar_subquery()
        )
        issue_counts = dict(
            session.query(QCIssue.severity, func.count())
            .join(QCRun).filter(QCRun.id.in_(latest_runs)).group_by(QCIssue.severity).all()
        )
        status_counts = dict(session.query(Version.status, func.count()).group_by(Version.status).all())
        return {
            "shots": shots_q.count(),
            "shots_approved": shots_q.filter(Shot.approved_version_id.isnot(None)).count(),
            "versions": session.query(Version).count(),
            "version_status": status_counts,
            "credits_total": session.query(func.coalesce(func.sum(GenerationJob.credits), 0.0)).scalar(),
            "generation_jobs": dict(
                session.query(GenerationJob.status, func.count()).group_by(GenerationJob.status).all()
            ),
            "qc_versions_checked": session.query(func.count(func.distinct(QCRun.version_id))).scalar(),
            "qc_issues": issue_counts,
            "recent_activity": [
                {"at": a.at.isoformat(), "actor": a.actor, "action": a.action,
                 "target_type": a.target_type, "target_id": a.target_id, "detail": a.detail}
                for a in session.query(ActivityLog).order_by(ActivityLog.id.desc()).limit(15)
            ],
        }

    from . import (edit_routes, generation_routes, layout_routes, media_routes, production_routes, prompt_routes,
                   review_routes, scene_routes, structure_routes)

    app.include_router(media_routes.router(make_session, settings))
    app.include_router(review_routes.router(make_session))
    app.include_router(edit_routes.router(make_session, settings))
    app.include_router(generation_routes.router(make_session, settings))
    app.include_router(production_routes.router(make_session, settings))
    app.include_router(scene_routes.router(make_session, settings))
    app.include_router(structure_routes.router(make_session, settings))
    app.include_router(prompt_routes.router(make_session))
    app.include_router(layout_routes.router(make_session, settings))

    ui_dist = ROOT / "frontend" / "dist"
    if ui_dist.is_dir():  # built React app; mounted last so /api routes take priority
        app.mount("/", StaticFiles(directory=ui_dist, html=True), name="ui")

    return app

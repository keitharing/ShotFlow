"""ComfyUI integration: server control, recipes, generation jobs, variation grids and mask fixes."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import GenerationJob, Recipe, RecipeVersion, Shot, Version
from ..services import prompts
from ..services.comfy import fix as fixlib
from ..services.comfy import gpu, runner, server
from ..services.comfy.client import ComfyClient
from ..services.comfy.recipes import graph_for_display, load_object_info, sync
from ..services.media import is_image, read_frame
from .deps import get_or_404, session_dependency


class GenerateIn(BaseModel):
    recipe_version_id: int
    shot_id: int
    values: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] | None = Field(default=None, max_length=12)
    strengths: list[float] | None = Field(default=None, max_length=12)
    source_version_id: int | None = None
    source_frame: int = Field(default=0, ge=0)


class FixIn(BaseModel):
    frame: int = Field(ge=0)
    mask_png: str  # base64 PNG painted in the browser (opaque = edit)
    values: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3], min_length=1, max_length=6)
    recipe_version_id: int | None = None


def _recipe_out(recipe: Recipe) -> dict[str, Any]:
    latest = recipe.versions[-1]
    meta = (latest.ui_json or {}).get("meta", {})
    return {"id": recipe.id, "key": recipe.name, "name": meta.get("name", recipe.name), "kind": recipe.kind,
            "description": recipe.description, "latest_version_id": latest.id, "version": latest.number,
            "versions": [{"id": v.id, "number": v.number, "created_at": v.created_at.isoformat()} for v in recipe.versions],
            "exposed_inputs": latest.exposed_inputs, "required_models": latest.required_models,
            "bindings": list((meta.get("bindings") or {}).keys()), "output_kind": meta.get("output", {}).get("kind"),
            "strength_input": meta.get("strength_input"), "limits": meta.get("limits")}


def _shot_code(job: GenerationJob) -> str | None:
    from sqlalchemy.orm import object_session

    session = object_session(job)
    shot = session.get(Shot, (job.params or {}).get("shot_id")) if session else None
    return f"{shot.sequence.name} {shot.code}" if shot else None


def job_out(job: GenerationJob) -> dict[str, Any]:
    p = job.params or {}
    outputs = p.get("outputs", [])
    return {
        "id": job.id, "batch_id": job.batch_id, "status": job.status, "recipe": p.get("recipe"),
        "shot_code": _shot_code(job),
        "recipe_name": p.get("recipe_name"), "recipe_version": p.get("recipe_version"), "kind": p.get("kind"),
        "purpose": p.get("purpose"), "shot_id": p.get("shot_id"), "source_version_id": p.get("source_version_id"),
        "source_frame": p.get("source_frame"), "seed": job.seed, "values": p.get("values"),
        "progress": p.get("progress", 0), "stage": p.get("stage"), "error": job.error,
        "prompt": job.prompt, "prompt_snapshot_id": job.prompt_snapshot_id, "model": job.model, "models": p.get("models"), "oom_retry": p.get("oom_retry"),
        "seconds": p.get("seconds"), "peak_vram_mb": job.peak_vram_mb, "peak_temp_c": job.peak_temp_c,
        "external_id": job.external_id, "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "outputs": [{"index": i, "url": f"/api/generation/jobs/{job.id}/outputs/{i}",
                     "kind": "image" if is_image(path) else "video",
                     "version_id": p.get("registered", {}).get(str(i))} for i, path in enumerate(outputs)],
    }


def router(make_session: sessionmaker[Session], settings: Settings) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)
    runner.recover_interrupted(settings)

    # ---- ComfyUI process
    @api.get("/comfy/status")
    def comfy_status() -> dict:
        return {**server.status(settings), "gpu": gpu.sample()}

    @api.post("/comfy/start")
    def comfy_start() -> dict:
        try:
            state = server.start(settings)
            client = ComfyClient(state["url"])
            try:
                info = load_object_info(settings, client.object_info())
            finally:
                client.close()
            with make_session() as session:
                report = sync(session, info, actor="ui")
                session.commit()
            return {**state, "recipes": report}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @api.post("/comfy/stop")
    def comfy_stop() -> dict:
        return server.stop(settings)

    # ---- recipes
    @api.post("/recipes/sync")
    def recipes_sync(session: Session = Depends(db)) -> list[dict]:
        return sync(session, load_object_info(settings), actor="ui")

    @api.get("/recipes")
    def recipes(session: Session = Depends(db)) -> list[dict]:
        if not session.query(Recipe).count():
            sync(session, load_object_info(settings), actor="system")
        return [_recipe_out(r) for r in session.query(Recipe).order_by(Recipe.id) if r.versions]

    @api.get("/recipe-versions/{rv_id}/graph")
    def recipe_graph(rv_id: int, session: Session = Depends(db)) -> dict:
        return graph_for_display(get_or_404(session, RecipeVersion, rv_id).api_json)

    # ---- jobs
    @api.post("/generate", status_code=202)
    def generate(payload: GenerateIn, session: Session = Depends(db)) -> list[dict]:
        rv = get_or_404(session, RecipeVersion, payload.recipe_version_id)
        shot = get_or_404(session, Shot, payload.shot_id)
        source = get_or_404(session, Version, payload.source_version_id) if payload.source_version_id else None
        try:
            jobs = runner.create_jobs(session, rv, shot=shot, values=payload.values, seeds=payload.seeds,
                                      strengths=payload.strengths, source_version=source,
                                      source_frame=payload.source_frame)
        except (ValueError, KeyError, StopIteration) as exc:
            raise HTTPException(422, str(exc)) from exc
        for job in jobs:  # a prompt that is exactly one kept in prompt management is linked to it
            snap = prompts.match(session, shot, job.prompt)
            job.prompt_snapshot_id = snap.id if snap else None
        session.commit()
        runner.submit([j.id for j in jobs], settings)
        return [job_out(j) for j in jobs]

    @api.post("/versions/{version_id}/fix", status_code=202)
    def mask_fix(version_id: int, payload: FixIn, session: Session = Depends(db)) -> list[dict]:
        version = get_or_404(session, Version, version_id)
        if version.shot.kind != "shot":
            raise HTTPException(422, "편집본에는 부분 수정을 쓸 수 없습니다 (샷 버전에서 수정하세요)")
        rv = get_or_404(session, RecipeVersion, payload.recipe_version_id) if payload.recipe_version_id else \
            session.query(Recipe).filter_by(kind="inpaint").one().versions[-1]
        meta = runner.recipe_meta(rv)
        try:
            frame = fixlib.imread(Path(version.path)) if is_image(version.path) else \
                read_frame(Path(version.path), payload.frame)
            out_dir = settings.data_dir / "fix" / f"v{version.id}_f{payload.frame}_{runner.utcnow():%H%M%S}"
            prep = fixlib.prepare(frame, base64.b64decode(payload.mask_png.split(",")[-1]), out_dir,
                                  meta.get("fix_work_size", 512), settings)
            jobs = runner.create_jobs(session, rv, shot=version.shot, values=payload.values, seeds=payload.seeds,
                                      source_version=version, source_frame=payload.frame, fix=prep.as_dict())
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        runner.submit([j.id for j in jobs], settings)
        return [job_out(j) for j in jobs]

    @api.get("/generation/jobs")
    def jobs(batch: str | None = None, shot_id: int | None = None, limit: int = 60,
             session: Session = Depends(db)) -> list[dict]:
        q = session.query(GenerationJob).filter(GenerationJob.provider.in_(("comfyui", "comfyui-node")))
        if batch:
            q = q.filter(GenerationJob.batch_id == batch)
        rows = [j for j in q.order_by(GenerationJob.id.desc()).limit(limit if not shot_id else 500)]
        if shot_id:
            rows = [j for j in rows if (j.params or {}).get("shot_id") == shot_id][:limit]
        return [job_out(j) for j in rows]

    @api.get("/generation/jobs/{job_id}")
    def job(job_id: int, session: Session = Depends(db)) -> dict:
        found = get_or_404(session, GenerationJob, job_id)
        return {**job_out(found), "graph": (found.params or {}).get("graph")}

    @api.get("/generation/jobs/{job_id}/outputs/{index}")
    def job_output(job_id: int, index: int, session: Session = Depends(db)) -> FileResponse:
        outputs = (get_or_404(session, GenerationJob, job_id).params or {}).get("outputs", [])
        if not 0 <= index < len(outputs):
            raise HTTPException(404, "No such output")
        path = Path(outputs[index])
        return FileResponse(path, media_type="image/png" if is_image(path) else "video/mp4")

    @api.post("/generation/jobs/{job_id}/register/{index}", status_code=201)
    def register(job_id: int, index: int) -> dict:
        try:
            return {"version_id": runner.register_output(job_id, index, settings)}
        except (ValueError, IndexError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.post("/generation/jobs/{job_id}/cancel")
    def cancel(job_id: int, session: Session = Depends(db)) -> dict:
        return job_out(runner.cancel(session, get_or_404(session, GenerationJob, job_id)))

    @api.get("/versions/{version_id}/frame/{frame}")
    def frame_image(version_id: int, frame: int, session: Session = Depends(db)) -> Response:
        version = get_or_404(session, Version, version_id)
        try:
            return Response(runner.frame_png(version, frame), media_type="image/png")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return api

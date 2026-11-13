"""3D integration routes: Blender scenes and renders, the 3D guide recipe, layer composites, external generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import GenerationJob, Recipe, RecipeVersion, Shot, Version
from ..services import composite as comp
from ..services import external as ext
from ..services import prompts
from ..services.blender import runner as blender
from ..services.comfy import runner as comfy_runner
from ..services.comfy.recipes import load_object_info, sync
from ..services.worker import worker
from .deps import get_or_404, session_dependency
from .generation_routes import _recipe_out as recipe_out
from .generation_routes import job_out as generation_job_out


class PathIn(BaseModel):
    path: str = Field(min_length=1)


class PickIn(BaseModel):
    title: str = "Blender 파일 선택"


class BlenderJobIn(BaseModel):
    purpose: str = Field(pattern="^(passes|layer)$")
    scene_path: str = Field(min_length=1)
    shot_id: int | None = None
    frame: int | None = Field(default=None, ge=0)
    frame_start: int | None = Field(default=None, ge=0)
    frame_end: int | None = Field(default=None, ge=0)
    width: int = 1920
    height: int = 1080


class GuideIn(BaseModel):
    pass_job_id: int
    values: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] | None = Field(default=None, max_length=12)
    strengths: list[float] | None = Field(default=None, max_length=12)
    recipe_version_id: int | None = None
    prompt_use: str | None = None              # take the shot's kept prompt (image | image_local | video)


class CompositeIn(BaseModel):
    version_id: int
    layer_job_id: int
    opacity: float = Field(default=0.8, ge=0, le=1)
    mode: str = Field(default="screen", pattern="^(screen|over)$")
    offset: int = 0
    loop: bool = True
    frame: int = Field(default=0, ge=0)


class PackageIn(BaseModel):
    version_id: int
    prompt: str = Field(default="", max_length=4000)
    prompt_use: str | None = None              # take the shot's kept prompt instead of `prompt`
    model: str = "seedance_2_5"
    duration_s: int = 5
    end_version_id: int | None = None          # optional end frame (same shot)
    reference_version_id: int | None = None    # optional identity reference (same film)


def pick_file(title: str) -> str | None:
    """Native Windows file dialog on this PC (ShotFlow is a local tool), so nobody has to type a path."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askopenfilename(parent=root, title=title,
                                          filetypes=[("Blender 파일", "*.blend"), ("모든 파일", "*.*")])
    finally:
        root.destroy()
    return path or None


def router(make_session: sessionmaker[Session], settings: Settings) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)
    blender.recover_interrupted(settings)
    comp.recover_interrupted(settings)

    # ---- Blender scenes
    @api.get("/blender/status")
    def blender_status() -> dict:
        return blender.status()

    @api.post("/blender/pick-file")
    def blender_pick(payload: PickIn) -> dict:
        return {"path": pick_file(payload.title)}

    @api.get("/blender/scenes")
    def blender_scenes() -> list[dict]:
        return blender.scenes(settings)

    @api.post("/blender/scenes/inspect", status_code=202)
    def blender_inspect(payload: PathIn) -> dict:
        path = Path(payload.path.strip().strip('"'))
        if path.suffix.lower() != ".blend" or not path.is_file():
            raise HTTPException(422, "Blender 파일(.blend) 경로를 확인하세요")
        task = worker.submit("blender_inspect", str(path), lambda: blender.inspect(path, settings))
        return task.as_dict()

    @api.get("/blender/scenes/preview")
    def blender_scene_preview(path: str) -> FileResponse:
        entry = next((s for s in blender.scenes(settings) if s["path"] == path), None)
        if not entry or not entry.get("preview") or not Path(entry["preview"]).is_file():
            raise HTTPException(404, "미리보기가 아직 없습니다")
        return FileResponse(entry["preview"], media_type="image/png")

    # ---- Blender jobs
    @api.get("/blender/jobs")
    def blender_jobs(purpose: str | None = None, limit: int = 40, session: Session = Depends(db)) -> list[dict]:
        rows = session.query(GenerationJob).filter(GenerationJob.provider == "blender").order_by(GenerationJob.id.desc())
        jobs = [blender.job_out(j) for j in rows.limit(200)]
        return [j for j in jobs if purpose in (None, j["purpose"])][:limit]

    @api.post("/blender/jobs", status_code=202)
    def blender_job_create(payload: BlenderJobIn, session: Session = Depends(db)) -> dict:
        shot = get_or_404(session, Shot, payload.shot_id) if payload.shot_id else None
        try:
            job = blender.create_job(session, payload.purpose, Path(payload.scene_path), shot=shot,
                                     frame=payload.frame, frame_start=payload.frame_start, frame_end=payload.frame_end,
                                     width=payload.width, height=payload.height)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        blender.submit(job.id, settings)
        return blender.job_out(job)

    @api.get("/blender/jobs/{job_id}")
    def blender_job(job_id: int, session: Session = Depends(db)) -> dict:
        return blender.job_out(get_or_404(session, GenerationJob, job_id))

    @api.get("/blender/jobs/{job_id}/files/{name}")
    def blender_file(job_id: int, name: str, session: Session = Depends(db)) -> FileResponse:
        outputs = (get_or_404(session, GenerationJob, job_id).params or {}).get("outputs") or {}
        if name not in ("beauty", "depth", "mask", "preview") or name not in outputs:
            raise HTTPException(404, "No such file")
        return FileResponse(outputs[name], media_type="video/mp4" if name == "preview" else "image/png")

    @api.post("/blender/jobs/{job_id}/cancel")
    def blender_cancel(job_id: int, session: Session = Depends(db)) -> dict:
        return blender.job_out(blender.cancel(session, get_or_404(session, GenerationJob, job_id)))

    # ---- 3D guide keyframes (ComfyUI recipe fed by a Blender pass job)
    def _guide_recipe(session: Session) -> Recipe:
        recipe = session.query(Recipe).filter_by(kind="guide").one_or_none()
        if recipe is None:  # fresh install: register recipes from the cached ComfyUI schema
            sync(session, load_object_info(settings), actor="system")
            recipe = session.query(Recipe).filter_by(kind="guide").one_or_none()
        if recipe is None or not recipe.versions:
            raise HTTPException(422, "3D 가이드 레시피(recipes/guide_depth_sd15)를 찾을 수 없습니다")
        return recipe

    @api.get("/blender/guide-recipe")
    def guide_recipe(session: Session = Depends(db)) -> dict:
        return recipe_out(_guide_recipe(session))

    @api.post("/blender/guide", status_code=202)
    def guide(payload: GuideIn, session: Session = Depends(db)) -> list[dict]:
        passes = get_or_404(session, GenerationJob, payload.pass_job_id)
        p = passes.params or {}
        if passes.provider != "blender" or p.get("purpose") != "passes" or passes.status != "completed":
            raise HTTPException(422, "완료된 Blender 깊이 렌더를 고르세요")
        beauty = get_or_404(session, Version, (p.get("registered") or {}).get("beauty") or 0)
        rv = get_or_404(session, RecipeVersion, payload.recipe_version_id) if payload.recipe_version_id else \
            _guide_recipe(session).versions[-1]
        values, snap = dict(payload.values), None
        text_keys = {e["label"]: e["key"] for e in rv.exposed_inputs if e["input"] == "text"}
        try:
            if payload.prompt_use:
                snap = prompts.snapshot(session, beauty.shot, payload.prompt_use)
                if "프롬프트" not in text_keys:
                    raise ValueError("이 레시피에는 프롬프트 칸이 없습니다")
                values[text_keys["프롬프트"]] = snap.text
                if snap.negative and "네거티브" in text_keys:
                    values[text_keys["네거티브"]] = snap.negative
            jobs = comfy_runner.create_jobs(session, rv, shot=beauty.shot, values=values, seeds=payload.seeds,
                                            strengths=payload.strengths, source_version=beauty,
                                            guide={"depth_path": p["outputs"]["depth"], "pass_job_id": passes.id})
        except (ValueError, KeyError, StopIteration) as exc:
            raise HTTPException(422, str(exc)) from exc
        for job in jobs:
            linked = snap or prompts.match(session, beauty.shot, job.prompt)
            job.prompt_snapshot_id = linked.id if linked else None
        session.commit()
        blender.submit_cooled_generation([j.id for j in jobs], settings)
        return [generation_job_out(j) for j in jobs]

    # ---- layer composites
    def _composite_inputs(session: Session, payload: CompositeIn) -> tuple[Version, GenerationJob]:
        version = get_or_404(session, Version, payload.version_id)
        layer = get_or_404(session, GenerationJob, payload.layer_job_id)
        if layer.provider != "blender" or (layer.params or {}).get("purpose") != "layer":
            raise HTTPException(422, "Blender 레이어 작업을 고르세요")
        return version, layer

    @api.post("/composite/preview")
    def composite_preview(payload: CompositeIn, session: Session = Depends(db)) -> Response:
        version, layer = _composite_inputs(session, payload)
        try:
            data = comp.preview_png(version, layer, payload.frame, payload.opacity, payload.mode,
                                    payload.offset, payload.loop)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(data, media_type="image/jpeg")

    @api.post("/composite", status_code=202)
    def composite_create(payload: CompositeIn, session: Session = Depends(db)) -> dict:
        version, layer = _composite_inputs(session, payload)
        try:
            job = comp.create_job(session, version, layer, opacity=payload.opacity, mode=payload.mode,
                                  offset=payload.offset, loop=payload.loop)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        comp.submit(job.id, settings)
        return comp.job_out(job)

    @api.get("/composite/jobs")
    def composite_jobs(session: Session = Depends(db)) -> list[dict]:
        rows = session.query(GenerationJob).filter(GenerationJob.provider == "composite").order_by(GenerationJob.id.desc())
        return [comp.job_out(j) for j in rows.limit(40)]

    @api.get("/composite/jobs/{job_id}")
    def composite_job(job_id: int, session: Session = Depends(db)) -> dict:
        return comp.job_out(get_or_404(session, GenerationJob, job_id))

    # ---- external generation (Seedance by hand)
    def _external(session: Session, job_id: int) -> GenerationJob:
        job = get_or_404(session, GenerationJob, job_id)
        if job.provider != ext.PROVIDER or (job.params or {}).get("purpose") != "external":
            raise HTTPException(404, "외부 생성 패키지가 아닙니다")
        return job

    @api.get("/external/packages")
    def packages(session: Session = Depends(db)) -> list[dict]:
        rows = session.query(GenerationJob).filter(GenerationJob.provider == ext.PROVIDER).order_by(GenerationJob.id.desc())
        return [ext.job_out(j) for j in rows if (j.params or {}).get("purpose") == "external"]

    @api.post("/external/packages", status_code=201)
    def package_create(payload: PackageIn, session: Session = Depends(db)) -> dict:
        version = get_or_404(session, Version, payload.version_id)
        try:
            snap = prompts.snapshot(session, version.shot, payload.prompt_use) if payload.prompt_use else \
                prompts.match(session, version.shot, payload.prompt)
            job = ext.create_package(
                session, settings, version, prompt=snap.text if payload.prompt_use else payload.prompt,
                model=payload.model, duration_s=payload.duration_s, prompt_snapshot_id=snap.id if snap else None,
                end_version=get_or_404(session, Version, payload.end_version_id) if payload.end_version_id else None,
                reference_version=get_or_404(session, Version, payload.reference_version_id) if payload.reference_version_id else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return ext.job_out(job)

    @api.get("/external/packages/{job_id}/start-frame")
    def package_frame(job_id: int, session: Session = Depends(db)) -> FileResponse:
        folder = Path((_external(session, job_id).params or {}).get("package_dir", ""))
        first = folder / "start_frame.png"
        return FileResponse(first if first.is_file() else folder / "reference.png", media_type="image/png")

    @api.post("/external/packages/{job_id}/open-folder")
    def package_open(job_id: int, session: Session = Depends(db)) -> dict:
        try:
            return {"opened": ext.open_folder(_external(session, job_id))}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.post("/external/packages/{job_id}/result", status_code=201)
    async def package_result(job_id: int, file: UploadFile = File(...), credits: str = Form(""),
                             external_id: str = Form(""), note: str = Form(""), author: str = Form("ui"),
                             session: Session = Depends(db)) -> dict:
        job = _external(session, job_id)
        try:
            spent = float(credits) if credits.strip() else None
            version = ext.import_result(session, settings, job, file.filename or "result.mp4", await file.read(),
                                        credits=spent, external_id=external_id, note=note, actor=author or "ui")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {**ext.job_out(job), "version_id": version.id, "version_code": version.code,
                "qc": version.qc_runs[-1].status if version.qc_runs else None}

    return api

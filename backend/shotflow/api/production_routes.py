"""Production routes: ComfyUI node bridge (catalog/publish), delivery packages, production statistics."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Delivery, GenerationJob, Note, Shot
from ..services import delivery as dlv
from ..services.comfy import publish as pub
from .deps import get_or_404, session_dependency


class DeliveryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: str = Field(pattern="^(approved_shots|approved_edits|versions)$")
    sequence_ids: list[int] | None = None
    version_ids: list[int] | None = None


def _delivery_out(d: Delivery, detail: bool = False) -> dict[str, Any]:
    out = {"id": d.id, "name": d.name, "naming_rule": d.naming_rule, "folder": d.target_dir,
           "verified": d.verified, "files": len(d.items), "created_at": d.created_at.isoformat() if d.created_at else None}
    if detail:
        out["items"] = [{"id": i.id, "file": i.file_name, "sha256": i.sha256, "version_id": i.version_id} for i in d.items]
    return out


def router(make_session: sessionmaker[Session], settings: Settings) -> APIRouter:
    api = APIRouter(prefix="/api")
    db = session_dependency(make_session)

    # ---- ComfyUI node bridge
    @api.get("/catalog/versions")
    def catalog_versions(session: Session = Depends(db)) -> list[dict]:
        return pub.catalog_versions(session)

    @api.get("/catalog/shots")
    def catalog_shots(session: Session = Depends(db)) -> list[dict]:
        return pub.catalog_shots(session)

    @api.post("/shots/{shot_id}/publish", status_code=201)
    async def publish(shot_id: int, files: list[UploadFile] = File(...), graph: str = Form("{}"),
                      workflow: str = Form(""), note: str = Form(""), author: str = Form("ComfyUI"),
                      parent_version_id: int = Form(0), session: Session = Depends(db)) -> dict:
        shot = get_or_404(session, Shot, shot_id)
        try:
            payloads = [(f.filename or "image.png", await f.read()) for f in files]
            versions = pub.register_published(
                session, settings, shot, payloads, graph=json.loads(graph or "{}"),
                workflow=json.loads(workflow) if workflow else None,
                parent_id=parent_version_id or None, note=note, author=author or "ComfyUI")
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"shot_id": shot.id, "job_id": versions[0].generation_job_id,
                "versions": [{"id": v.id, "code": v.code, "qc": v.qc_runs[-1].status} for v in versions]}

    # ---- deliveries
    @api.get("/deliveries")
    def deliveries(session: Session = Depends(db)) -> list[dict]:
        return [_delivery_out(d) for d in session.query(Delivery).order_by(Delivery.id.desc())]

    @api.post("/deliveries", status_code=201)
    def create_delivery(payload: DeliveryIn, session: Session = Depends(db)) -> dict:
        try:
            versions = dlv.collect(session, payload.scope, payload.sequence_ids, payload.version_ids)
            created = dlv.create(session, settings, payload.name, versions)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return _delivery_out(created, detail=True)

    @api.get("/deliveries/{delivery_id}")
    def delivery(delivery_id: int, session: Session = Depends(db)) -> dict:
        return _delivery_out(get_or_404(session, Delivery, delivery_id), detail=True)

    @api.post("/deliveries/{delivery_id}/verify")
    def verify_delivery(delivery_id: int, session: Session = Depends(db)) -> dict:
        return dlv.verify(session, get_or_404(session, Delivery, delivery_id))

    # ---- statistics (measured values only)
    @api.get("/stats/production")
    def production_stats(session: Session = Depends(db)) -> dict:
        local = session.query(GenerationJob).filter(GenerationJob.provider.in_(("comfyui", "comfyui-node"))).all()
        by_recipe: dict[str, dict[str, Any]] = defaultdict(lambda: {"jobs": 0, "completed": 0, "failed": 0, "seconds": []})
        for job in local:
            p = job.params or {}
            key = p.get("recipe_name") or job.provider
            row = by_recipe[key]
            row["jobs"] += 1
            if job.status == "completed":
                row["completed"] += 1
                if p.get("seconds") is not None:
                    row["seconds"].append(float(p["seconds"]))
            elif job.status in ("failed", "interrupted"):
                row["failed"] += 1
        recipes = [{"recipe": k, "jobs": v["jobs"], "completed": v["completed"], "failed": v["failed"],
                    "avg_seconds": round(sum(v["seconds"]) / len(v["seconds"]), 1) if v["seconds"] else None}
                   for k, v in sorted(by_recipe.items(), key=lambda kv: -kv[1]["jobs"])]
        temps = [j.peak_temp_c for j in local if j.peak_temp_c]
        vram = [j.peak_vram_mb for j in local if j.peak_vram_mb]
        decisions: dict[str, int] = defaultdict(int)
        for note in session.query(Note).filter(Note.source != "imported"):
            decisions[note.decision or "comment"] += 1
        external = session.query(GenerationJob).filter(GenerationJob.provider == "higgsfield").all()
        return {
            "local_jobs": len(local),
            "local_completed": sum(1 for j in local if j.status == "completed"),
            "local_failed": sum(1 for j in local if j.status in ("failed", "interrupted")),
            "gpu_seconds": round(sum(float((j.params or {}).get("seconds") or 0) for j in local), 1),
            "peak_temp_c": max(temps) if temps else None,
            "peak_vram_mb": max(vram) if vram else None,
            "recipes": recipes,
            "external_jobs": len(external),
            "external_credits": sum(j.credits or 0 for j in external),
            "review_decisions": dict(decisions),
            "deliveries": session.query(Delivery).count(),
        }

    return api

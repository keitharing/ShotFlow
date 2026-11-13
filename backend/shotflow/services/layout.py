"""Animatic drawing -> Blender blocking (decision D-015).

The artist marks a cut's drawing (or takes a suggestion and fixes it); layout_solve turns the marks into a camera
and grey proxies; a Blender job (same queue as every GPU job) builds a NEW .blend and renders a preview from each
camera key for the overlay check. The .blend then shows up in the 3D keyframe step like any other scene.
Every build is a new layout (lay_001, lay_002 ...) with its marks, so a framing can always be traced back.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import ROOT, Settings, load_settings, writable_path
from ..db import session_scope
from ..models import Animatic, GenerationJob, Shot, ShotLayout, log_activity
from .blender import runner as blender
from .comfy.gpu import GpuGuard
from .layout_solve import LayoutError, solve_layout

LAYOUT_SCRIPT = ROOT / "integrations" / "blender" / "shotflow_layout.py"
PREVIEW_WIDTH = 960


def _next_code(session: Session, shot: Shot) -> str:
    used = [int(m.group(1)) for (code,) in session.query(ShotLayout.code).filter(ShotLayout.shot_id == shot.id)
            if (m := re.fullmatch(r"lay_(\d+)", code))]
    return f"lay_{max(used, default=0) + 1:03d}"


def _clean_marks(marks: dict[str, Any], animatic: Animatic, cut: dict[str, Any]) -> dict[str, Any]:
    frames = []
    for frame in marks.get("frames") or []:
        role = frame.get("role")
        if role not in ("start", "end"):
            continue
        index = int(frame.get("frame", cut["frames"][role]))
        if not cut["start"] <= index < cut["end"]:
            raise LayoutError(f"{role} 그림 프레임이 컷 범위({cut['start']}~{cut['end'] - 1}) 밖입니다")
        objects = []
        for i, ob in enumerate(frame.get("objects") or []):
            box = [float(v) for v in ob.get("box", [])]
            if len(box) != 4:
                raise LayoutError("상자 좌표가 잘못됐습니다")
            objects.append({"id": ob.get("id", i), "kind": ob.get("kind", "other"), "label": str(ob.get("label") or "")[:40],
                            "box": [round(min(max(v, -0.5), 1.5), 5) for v in box],
                            "height_m": ob.get("height_m"), "grounded": ob.get("grounded", True) is not False,
                            "mask": bool(ob.get("mask")), "anchor": bool(ob.get("anchor"))})
        frames.append({"role": role, "frame": index, "horizon": round(float(frame.get("horizon", 0.5)), 5),
                       "objects": objects})
    return {"lens_mm": float(marks.get("lens_mm") or 50), "frames": frames}


def preview_solution(animatic: Animatic, cut_index: int, marks: dict[str, Any]) -> dict[str, Any]:
    """Numbers only (no Blender): what the camera would be, for the live readout while the artist marks."""
    cut = _cut(animatic, cut_index)
    clean = _clean_marks(marks, animatic, cut)
    return solve_layout(clean, aspect=animatic.crop[2] / animatic.crop[3])


def _cut(animatic: Animatic, cut_index: int) -> dict[str, Any]:
    if not 0 <= cut_index < len(animatic.cuts):
        raise LayoutError("컷 번호를 확인하세요")
    cut = animatic.cuts[cut_index]
    if not cut.get("has_panel"):
        raise LayoutError("이 컷에는 그림이 없습니다 (제목 화면 등)")
    return cut


def create_layout(session: Session, animatic: Animatic, cut_index: int, marks: dict[str, Any], *,
                  source: str = "manual", actor: str = "ui") -> ShotLayout:
    cut = _cut(animatic, cut_index)
    if not cut.get("shot_id"):
        raise LayoutError("이 컷을 먼저 샷에 연결하세요")
    shot = session.get(Shot, cut["shot_id"])
    if shot is None or shot.kind != "shot" or shot.sequence_id != animatic.sequence_id:
        raise LayoutError("연결된 샷을 찾을 수 없습니다")
    clean = _clean_marks(marks, animatic, cut)
    solution = solve_layout(clean, aspect=animatic.crop[2] / animatic.crop[3])
    layout = ShotLayout(shot=shot, animatic=animatic, cut_index=cut_index, code=_next_code(session, shot),
                        marks=clean, solution=solution,
                        source=source if source in ("manual", "suggested", "mixed") else "manual")
    session.add(layout)
    session.flush()
    info = blender.status()
    job = GenerationJob(
        provider="blender", model=f"Blender {info['version']}" if info["version"] else "Blender", status="queued",
        params={"purpose": "layout", "recipe_name": "애니메틱 블로킹", "layout_id": layout.id, "shot_id": shot.id,
                "scene_name": f"{shot.sequence.code}_{shot.code}_{layout.code}", "progress": 0.0, "stage": "대기",
                "frame_start": 1, "frame_end": cut["end"] - cut["start"], "outputs": {}, "registered": {}},
    )
    session.add(job)
    session.flush()
    layout.generation_job = job
    session.flush()
    log_activity(session, actor, "layout_queued", layout, job_id=job.id, shot=shot.code, cut=cut_index + 1,
                 camera_height_m=solution["cameras"][0]["height_m"], source=layout.source)
    return layout


def submit(job_id: int, settings: Settings) -> None:
    blender.submit(job_id, settings)


def run_build(job_id: int, settings: Settings | None = None) -> dict[str, Any]:
    """Called by the Blender runner for purpose 'layout' (the job is already marked running)."""
    settings = settings or load_settings()
    started = time.perf_counter()
    try:
        with session_scope(settings) as session:
            job = session.get(GenerationJob, job_id)
            layout_id = job.params["layout_id"]
            layout = session.get(ShotLayout, layout_id)
            shot, animatic = layout.shot, layout.animatic
            cut = animatic.cuts[layout.cut_index]
            width, height = shot.sequence.frame_size
            aspect = animatic.crop[2] / animatic.crop[3]
            out_blend = settings.data_dir / "layouts" / f"shot{shot.id:05d}" / f"{shot.sequence.code}_{shot.code}_{layout.code}.blend"
            spec = {"width": width, "height": height, "fps": animatic.fps, "frame_count": cut["end"] - cut["start"],
                    "lens_mm": layout.solution["lens_mm"], "cameras": layout.solution["cameras"],
                    "objects": layout.solution["objects"], "preview": [PREVIEW_WIDTH, round(PREVIEW_WIDTH / aspect)],
                    "out_blend": str(writable_path(out_blend, settings)), "marks": layout.marks}
        out_dir = blender._job_dir(settings, job_id)
        blender.wait_for_cool_gpu(lambda message: blender._set(settings, job_id, params={"stage": message}))
        blender._set(settings, job_id, params={"stage": "블로킹 장면 만드는 중", "progress": 0.2})
        with GpuGuard() as guard:
            result = blender.run_blender(None, spec, out_dir, script=LAYOUT_SCRIPT,
                                         should_abort=lambda: f"GPU 보호 기준 초과: {guard.abort_reason()}"
                                         if guard.abort_reason() else None)
        blend = Path(spec["out_blend"])
        outputs = {"blend": str(blend), **{f"preview_{role}": path for role, path in result["previews"].items()}}
        blender.register_scene(blend, result["info"], Path(result["previews"].get("start", "")), settings)
        elapsed = round(time.perf_counter() - started, 2)
        with session_scope(settings) as session:
            layout = session.get(ShotLayout, layout_id)
            layout.blend_path = str(blend)
            log_activity(session, "blender", "layout_built", layout, job_id=job_id, blend=blend.name, seconds=elapsed)
        blender._set(settings, job_id, status="completed", finished_at=blender.utcnow(),
                     peak_vram_mb=guard.peak_vram_mb or None, peak_temp_c=guard.peak_temp_c or None,
                     params={"outputs": outputs, "result": result, "progress": 1.0, "stage": "완료", "seconds": elapsed,
                             "scene": str(blend), "camera": result["info"]["camera"]})
        return {"job_id": job_id, "status": "completed", "seconds": elapsed}
    except InterruptedError as exc:
        blender._set(settings, job_id, status="failed", error=str(exc), finished_at=blender.utcnow(), params={"stage": "중단"})
        raise
    except Exception as exc:
        blender._set(settings, job_id, status="failed", error=str(exc), finished_at=blender.utcnow(), params={"stage": "실패"})
        raise


def layout_out(layout: ShotLayout) -> dict[str, Any]:
    job = layout.generation_job
    job_info = blender.job_out(job) if job is not None else None
    return {"id": layout.id, "code": layout.code, "shot_id": layout.shot_id, "shot_code": layout.shot.code,
            "animatic_id": layout.animatic_id, "cut_index": layout.cut_index, "marks": layout.marks,
            "solution": layout.solution, "source": layout.source, "blend_path": layout.blend_path,
            "job": job_info, "created_at": layout.created_at.isoformat() if layout.created_at else None}
